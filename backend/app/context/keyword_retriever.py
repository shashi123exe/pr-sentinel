"""Keyword and symbol-based repository context retriever."""

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.context.base import ContextRetriever
from app.schemas.context import ContextRequest, ContextResult, ContextSnippet
from app.schemas.diff import ProcessedFile
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)

JS_IMPORT_REGEX = re.compile(r"""(?:import|from|require)\s*\(?['"](\.[^'"]+)['"]""")
JS_FUNCTION_REGEX = re.compile(
    r"""^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)"""
)
JS_CLASS_REGEX = re.compile(r"""^(?:export\s+)?class\s+([A-Za-z0-9_$]+)""")
JS_CONST_FN_REGEX = re.compile(
    r"""^(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>"""
)


@dataclass
class ContextLimits:
    """Configurable boundaries for context extraction."""

    max_snippets: int = 50
    max_total_bytes: int = 100_000
    max_files_fetched: int = 10
    max_bytes_per_file: int = 50_000
    surrounding_lines: int = 25
    max_local_import_files: int = 5


@dataclass
class CodeSpan:
    """A target line range to extract with metadata."""

    start: int
    end: int
    reason: str
    symbol: str | None = None


def merge_spans(spans: list[CodeSpan], total_lines: int) -> list[CodeSpan]:
    """Merge and deduplicate overlapping or adjacent line spans."""
    if not spans:
        return []

    # Clamp line numbers to [1, total_lines]
    valid_spans: list[CodeSpan] = []
    for s in spans:
        st = max(1, min(s.start, total_lines))
        en = max(st, min(s.end, total_lines))
        valid_spans.append(CodeSpan(start=st, end=en, reason=s.reason, symbol=s.symbol))

    # Sort primarily by start line, then end line
    valid_spans.sort(key=lambda s: (s.start, s.end))

    merged: list[CodeSpan] = [valid_spans[0]]

    for current in valid_spans[1:]:
        previous = merged[-1]
        # Overlapping or contiguous (difference <= 1)
        if current.start <= previous.end + 1:
            new_end = max(previous.end, current.end)
            # Combine reasons if different
            combined_reason = previous.reason
            if current.reason not in previous.reason:
                combined_reason = f"{previous.reason}+{current.reason}"
            combined_symbol = previous.symbol or current.symbol
            merged[-1] = CodeSpan(
                start=previous.start,
                end=new_end,
                reason=combined_reason,
                symbol=combined_symbol,
            )
        else:
            merged.append(current)

    return merged


def resolve_relative_import_path(base_file_path: str, import_ref: str) -> list[str]:
    """Resolve a relative import reference to possible repository file paths."""
    base_dir = PurePosixPath(base_file_path).parent
    clean_ref = import_ref.strip()
    target_dir = base_dir

    if clean_ref.startswith("./"):
        stripped = clean_ref[2:]
    elif clean_ref.startswith("../"):
        parts = clean_ref.split("/")
        up_count = sum(1 for p in parts if p == "..")
        rem = "/".join(p for p in parts if p not in {"..", "."})
        for _ in range(up_count):
            target_dir = target_dir.parent
        stripped = rem
    elif clean_ref.startswith("."):
        dots = len(clean_ref) - len(clean_ref.lstrip("."))
        stripped = clean_ref.lstrip(".")
        for _ in range(dots - 1):
            target_dir = target_dir.parent
    else:
        stripped = clean_ref

    candidate_base = target_dir.joinpath(stripped)
    normalized = str(candidate_base).replace("\\", "/")

    candidates: list[str] = [
        f"{normalized}.py",
        f"{normalized}.ts",
        f"{normalized}.tsx",
        f"{normalized}.js",
        f"{normalized}.jsx",
        f"{normalized}/__init__.py",
        f"{normalized}/index.ts",
        f"{normalized}/index.tsx",
        f"{normalized}/index.js",
    ]
    return [c for c in candidates if not c.startswith("..") and "/../" not in c]


def extract_python_context(
    source: str,
    changed_lines: set[int],
) -> tuple[list[CodeSpan], list[str]]:
    """Extract enclosing functions/classes and import paths via ast."""
    spans: list[CodeSpan] = []
    local_imports: list[str] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.debug("Python AST syntax error: %s", exc)
        return spans, local_imports

    for node in ast.walk(tree):
        # 1. Imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            st = getattr(node, "lineno", 1)
            en = getattr(node, "end_lineno", st)
            spans.append(CodeSpan(start=st, end=en, reason="import_statement"))

            if isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # Relative import, e.g. from .models import User
                    prefix = "." * node.level
                    mod = node.module or ""
                    local_imports.append(f"{prefix}{mod}")
                elif node.module:
                    local_imports.append(node.module.replace(".", "/"))

        # 2. Enclosing Functions and Classes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            st = getattr(node, "lineno", 1)
            en = getattr(node, "end_lineno", st)
            # Check if any changed line is enclosed by this definition
            if any(st <= cl <= en for cl in changed_lines):
                reason = (
                    "enclosing_class"
                    if isinstance(node, ast.ClassDef)
                    else "enclosing_function"
                )
                spans.append(
                    CodeSpan(start=st, end=en, reason=reason, symbol=node.name)
                )

    return spans, local_imports


def extract_js_ts_context(
    source_lines: list[str],
    changed_lines: set[int],
) -> tuple[list[CodeSpan], list[str]]:
    """Extract functions, classes, imports, and relative paths using regex."""
    spans: list[CodeSpan] = []
    local_imports: list[str] = []

    for idx, line in enumerate(source_lines, start=1):
        # 1. Imports
        import_match = JS_IMPORT_REGEX.search(line)
        if import_match:
            spans.append(CodeSpan(start=idx, end=idx, reason="import_statement"))
            local_imports.append(import_match.group(1))

        # 2. Functions and classes
        fn_match = JS_FUNCTION_REGEX.search(line) or JS_CONST_FN_REGEX.search(line)
        if fn_match:
            # Check if changed lines fall nearby (e.g. within next 30 lines)
            symbol_name = fn_match.group(1)
            if any(idx <= cl <= idx + 40 for cl in changed_lines):
                spans.append(
                    CodeSpan(
                        start=idx,
                        end=min(len(source_lines), idx + 20),
                        reason="enclosing_function",
                        symbol=symbol_name,
                    )
                )

        cls_match = JS_CLASS_REGEX.search(line)
        if cls_match:
            symbol_name = cls_match.group(1)
            if any(idx <= cl <= idx + 60 for cl in changed_lines):
                spans.append(
                    CodeSpan(
                        start=idx,
                        end=min(len(source_lines), idx + 30),
                        reason="enclosing_class",
                        symbol=symbol_name,
                    )
                )

    return spans, local_imports


class KeywordSymbolRetriever(ContextRetriever):
    """Context retriever extracting hunks, symbols, and bounded local imports."""

    def __init__(
        self,
        github_client: GitHubClient,
        limits: ContextLimits | None = None,
    ) -> None:
        self.github_client = github_client
        self.limits = limits or ContextLimits()

    async def retrieve(self, request: ContextRequest) -> ContextResult:
        """Extract context snippets for eligible code files in the pull request."""
        snippets: list[ContextSnippet] = []
        truncated = False
        bytes_total = 0
        files_examined = 0
        local_files_fetched = 0

        # Filter only eligible files (not skipped)
        eligible_files: list[ProcessedFile] = [
            f for f in request.files if f.skipped_reason is None
        ]

        for proc_file in eligible_files:
            if files_examined >= self.limits.max_files_fetched:
                truncated = True
                break

            # 1. Fetch HEAD file content
            content = await self.github_client.get_file_content(
                owner=request.owner,
                repo=request.repo,
                path=proc_file.path,
                ref=request.head_sha,
                max_bytes=self.limits.max_bytes_per_file,
            )
            files_examined += 1

            if content is None:
                continue

            file_lines = content.splitlines()
            total_lines = len(file_lines)
            if total_lines == 0:
                continue

            raw_spans: list[CodeSpan] = []

            # 2. Extract surrounding hunk code (+/- surrounding_lines)
            for hunk in proc_file.hunks:
                hunk_start = max(1, hunk.new_start - self.limits.surrounding_lines)
                hunk_end = min(
                    total_lines,
                    hunk.new_start + hunk.new_lines + self.limits.surrounding_lines,
                )
                raw_spans.append(
                    CodeSpan(
                        start=hunk_start,
                        end=hunk_end,
                        reason="changed_hunk_context",
                    )
                )

            # 3. Language-specific AST/symbol extraction
            local_imports: list[str] = []
            if proc_file.language == "python":
                ast_spans, local_imports = extract_python_context(
                    content, proc_file.added_line_numbers
                )
                raw_spans.extend(ast_spans)
            elif proc_file.language in {"javascript", "typescript", "tsx", "jsx"}:
                js_spans, local_imports = extract_js_ts_context(
                    file_lines, proc_file.added_line_numbers
                )
                raw_spans.extend(js_spans)

            # 4. Deduplicate and merge overlapping line spans for this file
            merged_spans = merge_spans(raw_spans, total_lines)

            # 5. Build snippets from merged spans
            for span in merged_spans:
                if len(snippets) >= self.limits.max_snippets:
                    truncated = True
                    break

                # Extract line text (1-based index)
                snippet_text = "\n".join(file_lines[span.start - 1 : span.end])
                snippet_bytes = len(snippet_text.encode("utf-8"))

                if bytes_total + snippet_bytes > self.limits.max_total_bytes:
                    truncated = True
                    break

                snippets.append(
                    ContextSnippet(
                        file_path=proc_file.path,
                        start_line=span.start,
                        end_line=span.end,
                        text=snippet_text,
                        reason=span.reason,
                        symbol=span.symbol,
                        source="github_contents",
                    )
                )
                bytes_total += snippet_bytes

            if truncated:
                break

            # 6. Resolve bounded local imports
            for imp_ref in local_imports:
                if local_files_fetched >= self.limits.max_local_import_files:
                    break
                if len(snippets) >= self.limits.max_snippets:
                    truncated = True
                    break

                candidate_paths = resolve_relative_import_path(proc_file.path, imp_ref)
                for cand_path in candidate_paths:
                    import_content = await self.github_client.get_file_content(
                        owner=request.owner,
                        repo=request.repo,
                        path=cand_path,
                        ref=request.head_sha,
                        max_bytes=self.limits.max_bytes_per_file,
                    )
                    if import_content is not None:
                        local_files_fetched += 1
                        files_examined += 1
                        # Extract top 30 lines (definitions/signatures)
                        imp_lines = import_content.splitlines()[:30]
                        imp_text = "\n".join(imp_lines)
                        imp_bytes = len(imp_text.encode("utf-8"))

                        if bytes_total + imp_bytes <= self.limits.max_total_bytes:
                            snippets.append(
                                ContextSnippet(
                                    file_path=cand_path,
                                    start_line=1,
                                    end_line=len(imp_lines),
                                    text=imp_text,
                                    reason="referenced_symbol",
                                    symbol=imp_ref,
                                    source="github_contents",
                                )
                            )
                            bytes_total += imp_bytes
                        else:
                            truncated = True
                        break

        return ContextResult(
            snippets=snippets,
            truncated=truncated,
            bytes_total=bytes_total,
            files_examined=files_examined,
        )
