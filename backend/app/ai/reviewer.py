"""AI review orchestrator with token budgeting, batching, and repair."""

import logging
from typing import Any

from app.ai.gemini_client import (
    GeminiClientProtocol,
    resolve_model_name,
)
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from app.db.models import AppSettings
from app.errors import AppError
from app.schemas.ai import AIFinding, AIReviewResult
from app.schemas.analysis import StaticFinding
from app.schemas.context import ContextSnippet
from app.schemas.diff import ProcessedFile
from app.schemas.github import PRMetadata
from app.services.file_filter import get_file_priority

logger = logging.getLogger(__name__)

# Token budgeting constants
DEFAULT_BATCH_TOKEN_BUDGET = 30_000  # ~120KB of prompt text per batch
DEFAULT_MAX_TOTAL_TOKEN_BUDGET = 90_000  # ~360KB cumulative prompt cap


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string using the 4-char heuristic."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class AIReviewer:
    """Orchestrates structured pull request reviews using Gemini AI."""

    def __init__(
        self,
        client: GeminiClientProtocol,
        settings: AppSettings | None = None,
        batch_token_budget: int = DEFAULT_BATCH_TOKEN_BUDGET,
        max_total_token_budget: int = DEFAULT_MAX_TOTAL_TOKEN_BUDGET,
    ) -> None:
        self.client = client
        self.settings = settings
        self.batch_token_budget = batch_token_budget
        self.max_total_token_budget = max_total_token_budget

    async def review(
        self,
        metadata: PRMetadata,
        files: list[ProcessedFile],
        context_snippets: list[ContextSnippet] | None = None,
        static_findings: list[StaticFinding] | None = None,
        model_override: str | None = None,
    ) -> AIReviewResult:
        """Execute AI code review across budgeted batches with invalid repair.

        Args:
            metadata: Pull request metadata (title, author, SHAs, description).
            files: Processed files from diff processing.
            context_snippets: Optional retrieved context snippets.
            static_findings: Optional deterministic static analysis findings.
            model_override: Optional model name override.

        Returns:
            AIReviewResult containing findings, token usage, and batch metrics.
        """
        context_snippets = context_snippets or []
        static_findings = static_findings or []

        # 1. Resolve model name according to configuration priority
        app_model = model_override or (
            self.settings.gemini_model if self.settings else None
        )
        model_name = resolve_model_name(app_model)

        # 2. Filter eligible code files with diff patches
        eligible_files = [f for f in files if f.skipped_reason is None and f.patch]
        if not eligible_files:
            return AIReviewResult(
                findings=[],
                tokens_in=0,
                tokens_out=0,
                model=model_name,
                truncated=False,
                batches=0,
                errors=[],
                dropped_content=[],
            )

        # 3. Deterministic file prioritization (1: source, 2: tests, 3: docs/config)
        indexed_files = [
            (get_file_priority(f.path, f.language), idx, f)
            for idx, f in enumerate(eligible_files)
        ]
        indexed_files.sort(key=lambda item: (item[0], item[1]))
        sorted_files = [item[2] for item in indexed_files]

        # 4. Partition context snippets and static findings by file path
        snippets_by_file: dict[str, list[ContextSnippet]] = {}
        for snip in context_snippets:
            snippets_by_file.setdefault(snip.file_path, []).append(snip)

        findings_by_file: dict[str, list[StaticFinding]] = {}
        for sf in static_findings:
            findings_by_file.setdefault(sf.file_path, []).append(sf)

        # 5. Build batches bounded by token budget
        batches: list[
            tuple[
                list[ProcessedFile],
                list[ContextSnippet],
                list[StaticFinding],
            ]
        ] = []
        dropped_content: list[dict[str, Any]] = []
        truncated = False

        base_overhead_tokens = (
            estimate_tokens(SYSTEM_PROMPT)
            + estimate_tokens(metadata.title)
            + estimate_tokens(getattr(metadata, "description", "") or "")
            + 200  # XML structure tags
        )

        current_batch_files: list[ProcessedFile] = []
        current_batch_snippets: list[ContextSnippet] = []
        current_batch_findings: list[StaticFinding] = []
        current_batch_tokens = base_overhead_tokens
        accumulated_total_tokens = 0

        for proc_file in sorted_files:
            file_snippets = snippets_by_file.get(proc_file.path, [])
            file_findings = findings_by_file.get(proc_file.path, [])

            file_tokens = estimate_tokens(proc_file.patch or "") + 100
            snip_tokens = sum(estimate_tokens(s.text) + 50 for s in file_snippets)
            find_tokens = sum(
                estimate_tokens(sf.evidence) + estimate_tokens(sf.explanation) + 50
                for sf in file_findings
            )
            item_total_tokens = file_tokens + snip_tokens + find_tokens

            # Check if cumulative budget exceeded across all batches
            if (
                accumulated_total_tokens + current_batch_tokens + item_total_tokens
                > self.max_total_token_budget
            ):
                truncated = True
                dropped_content.append(
                    {
                        "type": "file",
                        "path": proc_file.path,
                        "reason": "max_total_token_budget_exceeded",
                        "estimated_tokens": item_total_tokens,
                    }
                )
                continue

            # Check if item exceeds single batch budget
            if current_batch_tokens + item_total_tokens > self.batch_token_budget:
                if current_batch_files:
                    # Finalize current batch and start new batch
                    batches.append(
                        (
                            current_batch_files,
                            current_batch_snippets,
                            current_batch_findings,
                        )
                    )
                    accumulated_total_tokens += current_batch_tokens
                    current_batch_files = []
                    current_batch_snippets = []
                    current_batch_findings = []
                    current_batch_tokens = base_overhead_tokens

                # If item alone exceeds single batch budget, drop context first
                if base_overhead_tokens + item_total_tokens > self.batch_token_budget:
                    truncated = True
                    dropped_content.append(
                        {
                            "type": "context",
                            "path": proc_file.path,
                            "reason": "batch_budget_context_dropped",
                            "dropped_snippets": len(file_snippets),
                        }
                    )
                    file_snippets = []
                    snip_tokens = 0
                    item_total_tokens = file_tokens + find_tokens

            current_batch_files.append(proc_file)
            current_batch_snippets.extend(file_snippets)
            current_batch_findings.extend(file_findings)
            current_batch_tokens += item_total_tokens

        if current_batch_files:
            batches.append(
                (
                    current_batch_files,
                    current_batch_snippets,
                    current_batch_findings,
                )
            )

        # 6. Execute batches sequentially with invalid response repair
        all_findings: list[AIFinding] = []
        total_tokens_in = 0
        total_tokens_out = 0
        errors: list[str] = []

        for batch_idx, (b_files, b_snips, b_finds) in enumerate(batches):
            user_prompt = build_user_prompt(metadata, b_files, b_snips, b_finds)
            try:
                resp, t_in, t_out = await self.client.generate_review(
                    prompt=user_prompt,
                    system_instruction=SYSTEM_PROMPT,
                    model=model_name,
                )
                all_findings.extend(resp.findings)
                total_tokens_in += t_in
                total_tokens_out += t_out

            except AppError as exc:
                if exc.code == "ai_invalid_response":
                    # Capture tokens consumed by the initial attempt
                    if exc.details:
                        total_tokens_in += exc.details.get("tokens_in", 0)
                        total_tokens_out += exc.details.get("tokens_out", 0)

                    # Attempt exactly ONCE to repair invalid or malformed output
                    logger.warning(
                        "Batch %d returned invalid JSON/schema; attempting repair...",
                        batch_idx + 1,
                    )
                    repair_prompt = (
                        user_prompt
                        + "\n\nCRITICAL INSTRUCTION: Your previous response was "
                        + "invalid JSON or did not match the schema. Return valid "
                        + "JSON ONLY matching the schema with a 'findings' list."
                    )
                    try:
                        resp, r_in, r_out = await self.client.generate_review(
                            prompt=repair_prompt,
                            system_instruction=SYSTEM_PROMPT,
                            model=model_name,
                        )
                        all_findings.extend(resp.findings)
                        total_tokens_in += r_in
                        total_tokens_out += r_out
                        logger.info("Batch %d repair retry succeeded.", batch_idx + 1)
                    except Exception as repair_exc:
                        if isinstance(repair_exc, AppError) and repair_exc.details:
                            total_tokens_in += repair_exc.details.get("tokens_in", 0)
                            total_tokens_out += repair_exc.details.get("tokens_out", 0)
                        logger.error(
                            "Batch %d repair retry failed: %s",
                            batch_idx + 1,
                            repair_exc,
                        )
                        errors.append(
                            f"Batch {batch_idx + 1} repair failed: {repair_exc}"
                        )
                else:
                    logger.error(
                        "Batch %d failed with code '%s': %s",
                        batch_idx + 1,
                        exc.code,
                        exc.message,
                    )
                    errors.append(
                        f"Batch {batch_idx + 1} failed [{exc.code}]: {exc.message}"
                    )

            except Exception as unhandled_exc:
                logger.error(
                    "Unhandled exception during batch %d execution: %s",
                    batch_idx + 1,
                    unhandled_exc,
                )
                errors.append(f"Batch {batch_idx + 1} unhandled error: {unhandled_exc}")

        return AIReviewResult(
            findings=all_findings,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            model=model_name,
            truncated=truncated,
            batches=len(batches),
            errors=errors,
            dropped_content=dropped_content,
        )
