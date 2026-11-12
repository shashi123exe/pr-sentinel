"""Bandit static security analysis runner for Python files."""

import json
import logging
import sys
from pathlib import Path

from app.db.models import Category, Severity
from app.schemas.analysis import StaticFinding

logger = logging.getLogger(__name__)


def map_bandit_severity(issue_severity: str, issue_confidence: str) -> Severity:
    """Map Bandit severity and confidence to PR Sentinel Severity taxonomy."""
    sev_upper = issue_severity.upper()
    conf_upper = issue_confidence.upper()

    if sev_upper == "HIGH" and conf_upper == "HIGH":
        return Severity.CRITICAL
    if sev_upper == "HIGH":
        return Severity.HIGH
    if sev_upper == "MEDIUM":
        return Severity.MEDIUM
    return Severity.LOW


def map_bandit_confidence(issue_confidence: str) -> float:
    """Map Bandit confidence string to normalized float in [0.0, 1.0]."""
    conf_upper = issue_confidence.upper()
    if conf_upper == "HIGH":
        return 0.9
    if conf_upper == "MEDIUM":
        return 0.7
    if conf_upper == "LOW":
        return 0.5
    return 0.5


def parse_bandit_json(
    raw_json: str,
    temp_dir: Path,
    file_contents: dict[str, str],
    added_lines_by_file: dict[str, set[int]],
) -> list[StaticFinding]:
    """Parse Bandit JSON output into validated StaticFinding models."""
    findings: list[StaticFinding] = []
    if not raw_json.strip():
        return findings

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to decode Bandit JSON output: %s", exc)
        return findings

    results = data.get("results", [])
    if not isinstance(results, list):
        return findings

    for item in results:
        test_id = item.get("test_id", "BANDIT")
        issue_text = item.get("issue_text", "Security violation")
        filename = item.get("filename", "")

        # Compute relative repository path
        try:
            rel_path = Path(filename).relative_to(temp_dir).as_posix()
        except ValueError:
            rel_path = Path(filename).name

        line_start = item.get("line_number", 1)
        line_range = item.get("line_range") or [line_start]
        line_end = max(line_range) if line_range else line_start

        # 1. Finding must be anchored to actual added/changed lines in the diff
        file_added_lines = added_lines_by_file.get(rel_path, set())
        has_overlap = any(
            ln in file_added_lines for ln in range(line_start, line_end + 1)
        )
        if not has_overlap:
            # Discard findings on unchanged lines
            continue

        # 2. Extract exact verbatim evidence from HEAD file content
        content = file_contents.get(rel_path, "")
        lines = content.splitlines()
        if 1 <= line_start <= len(lines):
            evidence = lines[line_start - 1]
        else:
            evidence = item.get("code", issue_text).strip()

        # 3. CWE enrichment
        cwe_data = item.get("issue_cwe") or {}
        cwe_id = cwe_data.get("id")
        title_prefix = f"[{test_id}]"
        if cwe_id:
            title_prefix = f"[{test_id}/CWE-{cwe_id}]"
        title = f"{title_prefix} {issue_text}"

        issue_severity = item.get("issue_severity", "LOW")
        issue_confidence = item.get("issue_confidence", "LOW")
        severity = map_bandit_severity(issue_severity, issue_confidence)

        confidence_val = map_bandit_confidence(issue_confidence)

        more_info = item.get("more_info", "")
        explanation = (
            f"{issue_text}. Severity: {issue_severity}, "
            f"Confidence: {issue_confidence}. {more_info}"
        ).strip()

        findings.append(
            StaticFinding(
                file_path=rel_path,
                line_start=line_start,
                line_end=line_end,
                title=title,
                severity=severity,
                category=Category.SECURITY,
                tool_name="bandit",
                rule_id=test_id,
                evidence=evidence,
                explanation=explanation,
                confidence=confidence_val,
                suggested_fix=None,
            )
        )

    return findings


def build_bandit_command(target_dir: Path) -> list[str]:
    """Construct command arguments for running Bandit isolated in temp dir."""
    return [
        sys.executable,
        "-m",
        "bandit",
        "-f",
        "json",
        "-r",
        str(target_dir),
        "--quiet",
    ]
