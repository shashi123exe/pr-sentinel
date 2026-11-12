"""ESLint static analysis runner for JavaScript and TypeScript files."""

import json
import logging
import shutil
from pathlib import Path

from app.db.models import Category, Severity
from app.schemas.analysis import StaticFinding

logger = logging.getLogger(__name__)

SECURITY_RULES = {"no-eval", "no-implied-eval", "no-new-func"}
CODE_QUALITY_RULES = {
    "no-undef",
    "no-unused-vars",
    "@typescript-eslint/no-unused-vars",
    "@typescript-eslint/no-explicit-any",
}


def map_eslint_category(rule_id: str) -> Category:
    """Map ESLint rule to PR Sentinel Category taxonomy."""
    if rule_id in SECURITY_RULES:
        return Category.SECURITY
    if rule_id in CODE_QUALITY_RULES:
        return Category.CODE_QUALITY
    return Category.CODE_QUALITY


def map_eslint_severity(rule_id: str, eslint_severity: int) -> Severity:
    """Map ESLint severity and rule type to PR Sentinel Severity taxonomy."""
    if rule_id in SECURITY_RULES:
        return Severity.HIGH
    if eslint_severity == 2:
        return Severity.MEDIUM
    return Severity.LOW


def parse_eslint_json(
    raw_json: str,
    temp_dir: Path,
    file_contents: dict[str, str],
    added_lines_by_file: dict[str, set[int]],
) -> list[StaticFinding]:
    """Parse ESLint JSON output into validated StaticFinding models."""
    findings: list[StaticFinding] = []
    if not raw_json.strip():
        return findings

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to decode ESLint JSON output: %s", exc)
        return findings

    if not isinstance(data, list):
        return findings

    for file_result in data:
        filename = file_result.get("filePath", "")
        try:
            rel_path = Path(filename).relative_to(temp_dir).as_posix()
        except ValueError:
            rel_path = Path(filename).name

        messages = file_result.get("messages", [])
        if not isinstance(messages, list):
            continue

        file_added_lines = added_lines_by_file.get(rel_path, set())
        content = file_contents.get(rel_path, "")
        lines = content.splitlines()

        for msg in messages:
            rule_id = msg.get("ruleId") or "eslint-error"
            message_text = msg.get("message", "Lint issue")
            line_start = msg.get("line", 1)
            line_end = msg.get("endLine", line_start)

            # 1. Finding must be anchored to actual added/changed lines in the diff
            has_overlap = any(
                ln in file_added_lines for ln in range(line_start, line_end + 1)
            )
            if not has_overlap:
                continue

            # 2. Extract exact verbatim evidence from HEAD file content
            if 1 <= line_start <= len(lines):
                evidence = lines[line_start - 1]
            else:
                evidence = message_text

            category = map_eslint_category(rule_id)
            eslint_severity = msg.get("severity", 1)
            severity = map_eslint_severity(rule_id, eslint_severity)

            findings.append(
                StaticFinding(
                    file_path=rel_path,
                    line_start=line_start,
                    line_end=line_end,
                    title=f"[{rule_id}] {message_text}",
                    severity=severity,
                    category=category,
                    tool_name="eslint",
                    rule_id=rule_id,
                    evidence=evidence,
                    explanation=f"ESLint reported '{rule_id}': {message_text}",
                    confidence=0.9,
                    suggested_fix=None,
                )
            )

    return findings


def get_eslint_paths() -> tuple[str | None, Path, Path]:
    """Resolve paths to node binary, trusted eslint script, and eslint configuration."""
    node_exe = shutil.which("node")
    backend_dir = Path(__file__).resolve().parent.parent.parent
    tools_dir = backend_dir / "tools" / "eslint"
    eslint_bin = tools_dir / "node_modules" / "eslint" / "bin" / "eslint.js"
    eslint_config = tools_dir / "eslint.config.mjs"
    return node_exe, eslint_bin, eslint_config


def build_eslint_command(
    node_exe: str,
    eslint_bin: Path,
    eslint_config: Path,
    target_dir: Path,
) -> list[str]:
    """Construct command arguments for running ESLint isolated in temp dir."""
    return [
        node_exe,
        str(eslint_bin),
        "--no-config-lookup",
        "-c",
        str(eslint_config),
        "--format",
        "json",
        str(target_dir),
    ]
