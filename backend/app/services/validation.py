"""Advanced finding validation and code evidence verification."""

import difflib
import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from app.db.models import AppSettings, Category, Severity, Source
from app.schemas.ai import AIFinding
from app.schemas.analysis import StaticFinding
from app.schemas.context import ContextSnippet
from app.schemas.diff import ProcessedFile

logger = logging.getLogger(__name__)

# Valid taxonomy categories supported by PR Sentinel
SUPPORTED_CATEGORIES: set[str] = {
    Category.LOGIC_BUG.value,
    Category.SECURITY.value,
    Category.PERFORMANCE.value,
    Category.ERROR_HANDLING.value,
    Category.EDGE_CASE.value,
    Category.CODE_QUALITY.value,
    Category.TESTING.value,
    Category.MISSING_TESTS.value,
}

# Control characters to strip from text fields (excluding \n, \r, \t)
CONTROL_CHAR_REGEX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Diff header patterns to ignore as code evidence
DIFF_METADATA_REGEX = re.compile(
    r"^(@@.*?@@|diff --git.*|index [0-9a-fA-F]+\.\..*|--- [aiw]/.*|\+\+\+ [biw]/.*)$"
)


def sanitize_string(val: str | None, max_length: int | None = None) -> str:
    """Strip ASCII control characters and truncate to max length."""
    if not val:
        return ""
    clean = CONTROL_CHAR_REGEX.sub("", str(val)).strip()
    if max_length and len(clean) > max_length:
        return clean[:max_length]
    return clean


def normalize_path(path: str) -> str:
    """Normalize file path for consistent matching."""
    norm = path.replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    while norm.startswith("/"):
        norm = norm[1:]
    return norm


def extract_code_tokens(text: str) -> str:
    """Normalize code text by stripping diff prefixes and syntax-formatting whitespace.

    Collapses whitespace around syntax punctuation and operators, while strictly
    preserving required whitespace between distinct alphanumeric/identifier tokens
    and preserving line breaks to prevent cross-line token bridging.
    """
    lines = []
    for line in text.splitlines():
        trimmed = line.strip()
        if trimmed.startswith(("+", "-", " ")):
            trimmed = trimmed[1:].strip()
        if trimmed:
            collapsed = re.sub(r"[^\S\r\n]+", " ", trimmed)
            no_sym_spaces = re.sub(r"\s*([^\w\s])\s*", r"\1", collapsed)
            lines.append(no_sym_spaces.strip())
    return "\n".join(lines)


def extract_patch_candidate_text(patch: str | None) -> str:
    """Extract code body from unified diff patch, removing headers and deletions."""
    if not patch:
        return ""
    code_lines = []
    for line in patch.splitlines():
        line_clean = line.strip()
        if DIFF_METADATA_REGEX.match(line_clean):
            continue
        if line.startswith("-"):
            continue
        # Strip diff line marker (+ or space) if present
        if line.startswith(("+", " ")):
            code_lines.append(line[1:])
        else:
            code_lines.append(line)
    return "\n".join(code_lines)


def match_code_evidence(evidence: str, candidate_text: str) -> bool:
    """Deterministically check if code evidence exists in candidate text.

    Supports:
    1. Exact string containment.
    2. Normalized whitespace containment.
    3. Collapsed token sequence containment.
    4. Conservative fuzzy window matching with SequenceMatcher ratio >= 0.90.
    """
    ev_stripped = evidence.strip()
    if not ev_stripped or not candidate_text:
        return False

    # 1. Exact containment
    if ev_stripped in candidate_text:
        return True

    # 2. Normalized whitespace containment
    norm_ev = re.sub(r"\s+", " ", ev_stripped).strip()
    norm_cand = re.sub(r"\s+", " ", candidate_text).strip()
    if norm_ev in norm_cand:
        return True

    # 3. Collapsed whitespace / diff prefix containment
    tok_ev = extract_code_tokens(ev_stripped)
    tok_cand = extract_code_tokens(candidate_text)
    if tok_ev and tok_ev in tok_cand:
        return True

    # 4. Conservative sliding-window fuzzy matching (>= 0.90 ratio, min 15 chars)
    if len(norm_ev) >= 15:
        # If evidence is single-line, evaluate per-line to prevent line bridging
        if "\n" not in norm_ev:
            ev_tokens = norm_ev.split()
            ev_len = len(norm_ev)
            for c_line in candidate_text.splitlines():
                norm_line = re.sub(r"[^\S\r\n]+", " ", c_line).strip()
                if len(norm_line) < ev_len - 2:
                    continue
                # Try window lengths matching evidence length (or +/- 1 for char edit)
                for w_len in (ev_len, ev_len - 1, ev_len + 1):
                    if w_len > len(norm_line):
                        continue
                    for i in range(0, len(norm_line) - w_len + 1):
                        window = norm_line[i : i + w_len]
                        if len(window.split()) != len(ev_tokens):
                            continue
                        ratio = difflib.SequenceMatcher(None, norm_ev, window).ratio()
                        if ratio >= 0.90:
                            return True
        else:
            norm_cand_lines = "\n".join(
                re.sub(r"[^\S\r\n]+", " ", line_str).strip()
                for line_str in candidate_text.splitlines()
            )
            ev_len = len(norm_ev)
            step = max(1, ev_len // 4)
            for i in range(0, max(1, len(norm_cand_lines) - ev_len + 1), step):
                window = norm_cand_lines[i : i + ev_len + 4]
                ratio = difflib.SequenceMatcher(None, norm_ev, window).ratio()
                if ratio >= 0.90:
                    return True

    return False


@dataclass
class ValidationResult:
    """Container for validated findings and drop tracking statistics."""

    valid_findings: list[dict[str, Any]] = field(default_factory=list)
    dropped_count: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def accepted_count(self) -> int:
        """Count of findings that passed validation."""
        return len(self.valid_findings)

    def record_drop(self, reason: str, details: str = "") -> None:
        """Record a single dropped finding with its rejection reason."""
        self.dropped_count += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1
        if details:
            logger.info("Finding dropped [%s]: %s", reason, details)


class FindingValidator:
    """Validates AI and static findings against schemas, diffs, context, and rules."""

    def __init__(
        self,
        files: list[ProcessedFile],
        context_snippets: list[ContextSnippet] | None = None,
        app_settings: AppSettings | None = None,
    ) -> None:
        self.files = files
        # Only non-skipped files are eligible
        self.eligible_files: dict[str, ProcessedFile] = {
            normalize_path(f.path): f for f in files if f.skipped_reason is None
        }

        # Context snippets organized by normalized file path
        self.context_by_file: dict[str, list[ContextSnippet]] = {}
        for snip in context_snippets or []:
            norm_p = normalize_path(snip.file_path)
            self.context_by_file.setdefault(norm_p, []).append(snip)

        # Settings
        self.min_confidence = (
            float(app_settings.min_confidence)
            if app_settings and app_settings.min_confidence is not None
            else 0.5
        )
        if app_settings and app_settings.enabled_categories:
            raw_cats = app_settings.enabled_categories
            self.enabled_categories: set[str] = {
                c.value if hasattr(c, "value") else str(c).lower() for c in raw_cats
            }
            # Treat missing_tests and testing interchangeably
            if (
                Category.TESTING.value in self.enabled_categories
                or Category.MISSING_TESTS.value in self.enabled_categories
            ):
                self.enabled_categories.add(Category.TESTING.value)
                self.enabled_categories.add(Category.MISSING_TESTS.value)
        else:
            self.enabled_categories = set(SUPPORTED_CATEGORIES)

    def validate_all(
        self,
        raw_ai_findings: list[Any] | None = None,
        raw_static_findings: list[Any] | None = None,
    ) -> ValidationResult:
        """Validate both AI and static findings, tracking dropped counts and reasons."""
        result = ValidationResult()

        # 1. Validate AI findings
        for item in raw_ai_findings or []:
            validated = self._validate_single_ai_finding(item, result)
            if validated is not None:
                result.valid_findings.append(validated)

        # 2. Validate static findings
        for item in raw_static_findings or []:
            validated = self._validate_single_static_finding(item, result)
            if validated is not None:
                result.valid_findings.append(validated)

        return result

    def _validate_single_ai_finding(
        self,
        item: Any,
        result: ValidationResult,
    ) -> dict[str, Any] | None:
        """Execute the multi-stage validation pipeline for an AI-generated finding."""
        # Stage A: Schema Validation
        try:
            finding = (
                item if isinstance(item, AIFinding) else AIFinding.model_validate(item)
            )
        except Exception as exc:
            result.record_drop("invalid_schema", f"Schema validation error: {exc}")
            return None

        # Category check
        cat_str = (
            finding.category.value
            if hasattr(finding.category, "value")
            else str(finding.category)
        )
        if cat_str not in self.enabled_categories:
            result.record_drop(
                "disabled_category",
                f"Category '{cat_str}' not in enabled categories",
            )
            return None

        # Stage B: File Verification
        raw_path = finding.file_path
        raw_norm = raw_path.replace("\\", "/")
        norm_path = normalize_path(raw_path)

        # Reject absolute paths, Windows drive letters, UNC paths, and traversal
        if (
            raw_path.startswith(("\\\\", "//"))
            or re.match(r"^[a-zA-Z]:", raw_path)
            or raw_norm.startswith("/")
            or ".." in PurePosixPath(norm_path).parts
            or PurePosixPath(norm_path).is_absolute()
        ):
            result.record_drop(
                "invalid_path",
                f"Path traversal or absolute/UNC path: {raw_path}",
            )
            return None

        if norm_path not in self.eligible_files:
            result.record_drop(
                "nonexistent_file", f"File '{norm_path}' not found in PR files"
            )
            return None

        target_file = self.eligible_files[norm_path]

        # Stage C: Evidence Verification
        evidence_clean = sanitize_string(finding.evidence, max_length=5000)
        if not evidence_clean:
            result.record_drop("unverifiable_evidence", "Evidence is empty")
            return None

        # Check in patch of this exact file
        patch_text = extract_patch_candidate_text(target_file.patch)
        evidence_verified = match_code_evidence(evidence_clean, patch_text)

        # If not in patch, check context snippets for this exact file
        matched_context_snippet: ContextSnippet | None = None
        if not evidence_verified and norm_path in self.context_by_file:
            for snippet in self.context_by_file[norm_path]:
                if match_code_evidence(evidence_clean, snippet.text):
                    evidence_verified = True
                    matched_context_snippet = snippet
                    break

        if not evidence_verified:
            result.record_drop(
                "unverifiable_evidence",
                f"Evidence could not be verified in diff or context of {norm_path}",
            )
            return None

        # Stage D: Line Verification
        line_start = finding.line_start
        line_end = finding.line_end

        if line_start is not None:
            if line_start < 1:
                result.record_drop(
                    "invalid_line", f"line_start must be >= 1: {line_start}"
                )
                return None
            if line_end is not None and line_end < line_start:
                result.record_drop(
                    "invalid_line",
                    f"Reversed line range: line_start ({line_start}) > "
                    f"line_end ({line_end})",
                )
                return None
            if line_end is None:
                line_end = line_start

            # Must correspond to valid changed line or valid new-side hunk range
            in_added = line_start in target_file.added_line_numbers
            in_hunk = any(
                h.new_start <= line_start <= (h.new_start + max(1, h.new_lines) - 1)
                for h in target_file.hunks
            )

            if not in_added and not in_hunk:
                # If evidence was found in context snippet, check if line in snippet
                if (
                    matched_context_snippet
                    and matched_context_snippet.start_line
                    <= line_start
                    <= matched_context_snippet.end_line
                ):
                    pass  # Verified inside context snippet
                else:
                    result.record_drop(
                        "invalid_line",
                        f"Line {line_start} is outside valid hunks or lines "
                        f"for {norm_path}",
                    )
                    return None
        else:
            # Line start missing: attempt to derive from patch hunks or matched context
            derived_line = self._derive_line_number(
                evidence_clean, target_file, matched_context_snippet
            )
            if derived_line is not None:
                line_start = derived_line
                line_end = derived_line
            else:
                result.record_drop(
                    "invalid_line", f"Missing line_start for {norm_path}"
                )
                return None

        # Stage E: Confidence Filtering
        conf = float(finding.confidence)
        if conf < self.min_confidence:
            result.record_drop(
                "low_confidence",
                f"Confidence {conf:.2f} < threshold {self.min_confidence:.2f}",
            )
            return None

        # Stage F: String Sanitization
        clean_title = sanitize_string(finding.title, max_length=200)
        clean_explanation = sanitize_string(finding.explanation, max_length=5000)
        clean_fix = (
            sanitize_string(finding.suggested_fix, max_length=5000)
            if finding.suggested_fix
            else None
        )

        sev = (
            finding.severity
            if isinstance(finding.severity, Severity)
            else Severity(str(finding.severity).lower())
        )
        cat = (
            finding.category
            if isinstance(finding.category, Category)
            else Category(cat_str)
        )

        return {
            "file_path": target_file.path,
            "line_start": line_start,
            "line_end": line_end,
            "title": clean_title,
            "severity": sev.value,
            "category": cat.value,
            "source": Source.AI.value,
            "tool_name": "gemini",
            "rule_id": "ai_review",
            "evidence": evidence_clean,
            "explanation": clean_explanation,
            "suggested_fix": clean_fix,
            "confidence": conf,
        }

    def _validate_single_static_finding(
        self,
        item: Any,
        result: ValidationResult,
    ) -> dict[str, Any] | None:
        """Validate a deterministic static analysis finding from local tools."""
        try:
            sf = (
                item
                if isinstance(item, StaticFinding)
                else StaticFinding.model_validate(item)
            )
        except Exception as exc:
            result.record_drop("invalid_schema", f"Static finding schema error: {exc}")
            return None

        # Enabled category check
        cat_str = (
            sf.category.value if hasattr(sf.category, "value") else str(sf.category)
        )
        if cat_str not in self.enabled_categories:
            result.record_drop(
                "disabled_category",
                f"Static category '{cat_str}' not in enabled categories",
            )
            return None

        # File membership verification & security check
        raw_path = sf.file_path
        raw_norm = raw_path.replace("\\", "/")
        norm_path = normalize_path(raw_path)

        if (
            raw_path.startswith(("\\\\", "//"))
            or re.match(r"^[a-zA-Z]:", raw_path)
            or raw_norm.startswith("/")
            or ".." in PurePosixPath(norm_path).parts
            or PurePosixPath(norm_path).is_absolute()
        ):
            result.record_drop(
                "invalid_path",
                f"Static path traversal or absolute/UNC path: {raw_path}",
            )
            return None

        if norm_path not in self.eligible_files:
            result.record_drop(
                "nonexistent_file",
                f"Static finding file '{norm_path}' not eligible",
            )
            return None

        target_file = self.eligible_files[norm_path]

        # Line range validation
        line_start = sf.line_start
        if line_start < 1:
            result.record_drop("invalid_line", f"Static line_start < 1: {line_start}")
            return None

        if sf.line_end is not None and sf.line_end < line_start:
            result.record_drop(
                "invalid_line",
                f"Static finding reversed line range: {line_start} > {sf.line_end}",
            )
            return None
        line_end = sf.line_end if sf.line_end is not None else line_start

        # Sanitization
        clean_title = sanitize_string(sf.title, max_length=200)
        clean_evidence = sanitize_string(sf.evidence, max_length=5000)
        clean_explanation = sanitize_string(sf.explanation, max_length=5000)
        clean_fix = (
            sanitize_string(sf.suggested_fix, max_length=5000)
            if sf.suggested_fix
            else None
        )

        sev = (
            sf.severity
            if isinstance(sf.severity, Severity)
            else Severity(str(sf.severity).lower())
        )
        cat = sf.category if isinstance(sf.category, Category) else Category(cat_str)
        src = Source.STATIC

        return {
            "file_path": target_file.path,
            "line_start": line_start,
            "line_end": line_end,
            "title": clean_title,
            "severity": sev.value,
            "category": cat.value,
            "source": src.value,
            "tool_name": sf.tool_name,
            "rule_id": sf.rule_id,
            "evidence": clean_evidence,
            "explanation": clean_explanation,
            "suggested_fix": clean_fix,
            "confidence": 1.0,
        }

    def _derive_line_number(
        self,
        evidence: str,
        target_file: ProcessedFile,
        matched_context: ContextSnippet | None,
    ) -> int | None:
        """Attempt to derive line number from hunks or matched context snippet."""
        ev_norm = re.sub(r"\s+", " ", evidence).strip()

        # Check hunk lines
        for hunk in target_file.hunks:
            for d_line in hunk.lines:
                if d_line.new_no is not None:
                    line_norm = re.sub(r"\s+", " ", d_line.text).strip()
                    if ev_norm in line_norm or line_norm in ev_norm:
                        return d_line.new_no

        # Check matched context snippet
        if matched_context and matched_context.text:
            for idx, line in enumerate(matched_context.text.splitlines()):
                line_norm = re.sub(r"\s+", " ", line).strip()
                if ev_norm in line_norm or line_norm in ev_norm:
                    return matched_context.start_line + idx

        return None
