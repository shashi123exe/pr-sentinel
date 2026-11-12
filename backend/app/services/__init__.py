"""Business logic and integration services for PR Sentinel."""

from app.services.dedupe import (
    compute_finding_fingerprint,
    deduplicate_findings,
)
from app.services.diff_parser import (
    is_line_added,
    overlaps_added_lines,
    parse_diff_patch,
)
from app.services.file_filter import (
    FileFilterMetrics,
    detect_language,
    filter_and_process_files,
    is_test_file,
)
from app.services.github_client import GitHubClient
from app.services.persistence import save_review_results
from app.services.url_parser import ParsedPR, parse_pr_url
from app.services.validation import FindingValidator, ValidationResult

__all__ = [
    "FileFilterMetrics",
    "FindingValidator",
    "GitHubClient",
    "ParsedPR",
    "ValidationResult",
    "compute_finding_fingerprint",
    "deduplicate_findings",
    "detect_language",
    "filter_and_process_files",
    "is_line_added",
    "is_test_file",
    "overlaps_added_lines",
    "parse_diff_patch",
    "parse_pr_url",
    "save_review_results",
]
