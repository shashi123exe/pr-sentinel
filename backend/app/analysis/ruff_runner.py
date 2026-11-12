"""Ruff static analysis runner for Python files."""

import json
import logging
import sys
from pathlib import Path

from app.db.models import Category, Severity
from app.schemas.analysis import StaticFinding

logger = logging.getLogger(__name__)

# Curated rule selection for PR Sentinel:
# - F: Pyflakes (F821 undefined symbols, F401 unused imports, F841 unused variables)
# - E9: Syntax and indentation runtime errors (IOError, IndentationError, SyntaxError)
# - B: flake8-bugbear (common bug patterns, mutable defaults, except clauses)
# - PERF: Perflint (performance anti-patterns in Python loops/structures)
# Note: Security rules are delegated to Bandit (bandit_runner.py)
# to prevent duplicate reports.
RUFF_SELECTED_RULES = "F,E9,B,PERF"


def map_ruff_category(code: str) -> Category:
    """Map Ruff rule code to PR Sentinel Category taxonomy."""
    code_upper = code.upper()
    if code_upper.startswith("PERF"):
        return Category.PERFORMANCE
    if code_upper.startswith("B"):
        return Category.LOGIC_BUG
    if code_upper.startswith("F401") or code_upper.startswith("F841"):
        return Category.CODE_QUALITY
    if code_upper.startswith(("F", "E9")):
        return Category.LOGIC_BUG
    return Category.CODE_QUALITY


def map_ruff_severity(code: str) -> Severity:
    """Map Ruff rule code to PR Sentinel Severity level."""
    code_upper = code.upper()
    # Syntax and undefined variable errors are high severity
    if code_upper.startswith(("E9", "F821", "B")):
        return Severity.HIGH
    # Performance and unused variables are medium or low
    if code_upper.startswith("PERF"):
        return Severity.MEDIUM
    if code_upper.startswith(("F401", "F841")):
        return Severity.LOW
    return Severity.MEDIUM


def parse_ruff_json(
    raw_json: str,
    temp_dir: Path,
    file_contents: dict[str, str],
    added_lines_by_file: dict[str, set[int]],
) -> list[StaticFinding]:
    """Parse Ruff JSON output into validated StaticFinding models."""
    findings: list[StaticFinding] = []
    if not raw_json.strip():
        return findings

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to decode Ruff JSON output: %s", exc)
        return findings

    if not isinstance(data, list):
        return findings

    for item in data:
        code = item.get("code") or "RUFF"
        message = item.get("message", "Ruff finding")
        filename = item.get("filename", "")

        # Compute relative repository path
        try:
            rel_path = Path(filename).relative_to(temp_dir).as_posix()
        except ValueError:
            rel_path = Path(filename).name

        loc = item.get("location") or {}
        line_start = loc.get("row", 1)
        end_loc = item.get("end_location") or {}
        line_end = end_loc.get("row", line_start)

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
            evidence = message

        category = map_ruff_category(code)
        severity = map_ruff_severity(code)

        findings.append(
            StaticFinding(
                file_path=rel_path,
                line_start=line_start,
                line_end=line_end,
                title=f"[{code}] {message}",
                severity=severity,
                category=category,
                tool_name="ruff",
                rule_id=code,
                evidence=evidence,
                explanation=f"Ruff detected '{code}': {message}",
                confidence=0.95,
                suggested_fix=item.get("fix", {}).get("message")
                if item.get("fix")
                else None,
            )
        )

    return findings


def build_ruff_command(target_dir: Path) -> list[str]:
    """Construct command arguments for running Ruff isolated in temporary directory."""
    return [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--isolated",
        "--output-format",
        "json",
        "--select",
        RUFF_SELECTED_RULES,
        str(target_dir),
    ]
