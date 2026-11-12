"""File classification, filtering, and deterministic limit enforcement."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from app.db.models import AppSettings
from app.schemas.diff import ProcessedFile
from app.schemas.github import ChangedFile
from app.services.diff_parser import parse_diff_patch

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".class",
    ".jar",
    ".war",
    ".pyc",
    ".pyo",
    ".pyd",
    ".bin",
    ".dat",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".flac",
}

LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "go.sum",
    "composer.lock",
}

GENERATED_PREFIXES = (
    "dist/",
    "build/",
    "node_modules/",
    "vendor/",
    "__generated__/",
)

GENERATED_PATTERNS = (
    ".pb.go",
    "_pb2.py",
    ".snap",
    ".map",
)

MINIFIED_EXTENSIONS = (
    ".min.js",
    ".min.css",
)

# Safety caps and limits
MAX_PER_FILE_DIFF_BYTES = 50 * 1024  # 50 KB per-file diff limit
HARD_MAX_FILES_SAFETY_CAP = 200  # Upper bound allowed by AppSettings
HARD_MAX_DIFF_BYTES_SAFETY_CAP = 5_000_000  # Upper bound allowed by AppSettings
DEFAULT_MAX_FILES = 50
DEFAULT_MAX_DIFF_BYTES = 500_000


@dataclass
class FileFilterMetrics:
    """Metrics recorded during file filtering and limit truncation."""

    truncated: bool = False
    reason: str | None = None
    files_skipped: int = 0
    bytes_dropped: int = 0


def detect_language(path: str) -> str:
    """Identify file programming language from extension."""
    norm_path = path.lower()
    if norm_path.endswith((".py", ".pyi")):
        return "python"
    if norm_path.endswith(".tsx"):
        return "tsx"
    if norm_path.endswith(".jsx"):
        return "jsx"
    if norm_path.endswith((".ts", ".mts", ".cts")):
        return "typescript"
    if norm_path.endswith((".js", ".mjs", ".cjs")):
        return "javascript"
    return "other"


def is_test_file(path: str) -> bool:
    """Determine whether a path represents a test or spec file."""
    p = PurePosixPath(path.lower())
    name = p.name
    parts = p.parts

    if any(part in {"tests", "test", "__tests__", "spec", "specs"} for part in parts):
        return True

    test_suffixes = (
        "_test.py",
        "_spec.py",
        ".test.ts",
        ".spec.ts",
        ".test.js",
        ".spec.js",
        ".test.tsx",
        ".spec.tsx",
    )
    if name.startswith(("test_", "spec_")) or name.endswith(test_suffixes):
        return True

    return False


def get_file_priority(path: str, language: str) -> int:
    """Return priority rank: 1 (source code), 2 (tests), 3 (config/docs/other)."""
    if is_test_file(path):
        return 2
    if language in {"python", "typescript", "tsx", "javascript", "jsx"}:
        return 1
    return 3


def check_skipped_reason(file: ChangedFile, max_diff_bytes: int) -> str | None:
    """Determine if a file should be skipped from static analysis."""
    # 1. Deleted files
    if file.status in {"removed", "deleted"}:
        return "deleted_file"

    norm_path = file.path.replace("\\", "/").strip().lower()
    file_name = PurePosixPath(norm_path).name

    # 2. Binary files (patch marker or known extension)
    if (
        file.patch is None
        or "Binary files" in file.patch
        or "GIT binary patch" in file.patch
    ):
        return "binary_file"

    ext = PurePosixPath(norm_path).suffix
    if ext in BINARY_EXTENSIONS:
        return "binary_file"

    # 3. Lockfiles
    if file_name in LOCKFILES:
        return "lockfile"

    # 4. Minified files (.min.js, .min.css, or lines > 1,000 chars)
    for min_ext in MINIFIED_EXTENSIONS:
        if norm_path.endswith(min_ext):
            return "minified_file"

    if file.patch:
        for line in file.patch.splitlines():
            if len(line) > 1000:
                return "minified_file"

    # 5. Generated directories and file patterns
    for prefix in GENERATED_PREFIXES:
        if norm_path.startswith(prefix) or f"/{prefix}" in norm_path:
            return "generated_file"

    for pattern in GENERATED_PATTERNS:
        if norm_path.endswith(pattern):
            return "generated_file"

    # 6. Oversized diff per file (bounded by 50KB cap and total max_diff_bytes)
    patch_bytes = len(file.patch.encode("utf-8")) if file.patch else 0
    effective_max_per_file = min(max_diff_bytes, MAX_PER_FILE_DIFF_BYTES)
    if patch_bytes > effective_max_per_file:
        return "oversized_diff"

    return None


def filter_and_process_files(
    changed_files: list[ChangedFile],
    settings: AppSettings,
) -> tuple[list[ProcessedFile], FileFilterMetrics]:
    """Classify, filter, and apply deterministic prioritization and limits to files.

    Args:
        changed_files: Retrieved changed files from pull request snapshot.
        settings: Application settings specifying max_files and max_diff_bytes.

    Returns:
        Tuple of (list of ProcessedFile, FileFilterMetrics).
    """
    effective_max_files = (
        min(settings.max_files, HARD_MAX_FILES_SAFETY_CAP)
        if settings and settings.max_files > 0
        else DEFAULT_MAX_FILES
    )
    effective_max_diff_bytes = (
        min(settings.max_diff_bytes, HARD_MAX_DIFF_BYTES_SAFETY_CAP)
        if settings and settings.max_diff_bytes > 0
        else DEFAULT_MAX_DIFF_BYTES
    )

    metrics = FileFilterMetrics()
    processed_candidates: list[tuple[int, int, ProcessedFile, int]] = []
    skipped_initially: list[ProcessedFile] = []

    for idx, cf in enumerate(changed_files):
        lang = detect_language(cf.path)
        skip_reason = check_skipped_reason(cf, effective_max_diff_bytes)
        hunks, added_lines = parse_diff_patch(cf.patch)
        patch_bytes = len(cf.patch.encode("utf-8")) if cf.patch else 0

        proc = ProcessedFile(
            path=cf.path,
            language=lang,
            status=cf.status,
            additions=cf.additions,
            deletions=cf.deletions,
            hunks=hunks,
            added_line_numbers=added_lines,
            skipped_reason=skip_reason,
            patch=cf.patch,
        )

        if skip_reason is not None:
            metrics.files_skipped += 1
            skipped_initially.append(proc)
        else:
            priority = get_file_priority(cf.path, lang)
            processed_candidates.append((priority, idx, proc, patch_bytes))

    # Sort eligible candidates deterministically by priority (1=src, 2=tests, 3=docs)
    processed_candidates.sort(key=lambda item: (item[0], item[1]))

    accepted_files: list[ProcessedFile] = []
    accumulated_bytes: int = 0

    for _priority, _orig_idx, proc_file, p_bytes in processed_candidates:
        # Check max_files limit
        if len(accepted_files) >= effective_max_files:
            proc_file.skipped_reason = "max_files_exceeded"
            metrics.files_skipped += 1
            metrics.bytes_dropped += p_bytes
            metrics.truncated = True
            metrics.reason = "max_files_exceeded"
            skipped_initially.append(proc_file)
            continue

        # Check max_diff_bytes limit
        if accumulated_bytes + p_bytes > effective_max_diff_bytes:
            proc_file.skipped_reason = "max_diff_bytes_exceeded"
            metrics.files_skipped += 1
            metrics.bytes_dropped += p_bytes
            metrics.truncated = True
            metrics.reason = "max_diff_bytes_exceeded"
            skipped_initially.append(proc_file)
            continue

        accumulated_bytes += p_bytes
        accepted_files.append(proc_file)

    # Return all processed files (accepted + skipped)
    all_files = accepted_files + skipped_initially
    return all_files, metrics
