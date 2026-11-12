"""Unit tests for Bandit and Ruff rule, category, and severity mapping."""

from pathlib import Path

from app.analysis.bandit_runner import (
    map_bandit_confidence,
    map_bandit_severity,
    parse_bandit_json,
)
from app.analysis.ruff_runner import (
    RUFF_SELECTED_RULES,
    map_ruff_category,
    map_ruff_severity,
)
from app.db.models import Category, Severity


def test_bandit_severity_matrix():
    """Verify Bandit severity + confidence mapping rules."""
    # HIGH severity + HIGH confidence -> CRITICAL
    assert map_bandit_severity("HIGH", "HIGH") == Severity.CRITICAL
    # Other HIGH severity findings -> HIGH
    assert map_bandit_severity("HIGH", "MEDIUM") == Severity.HIGH
    assert map_bandit_severity("HIGH", "LOW") == Severity.HIGH
    # MEDIUM -> MEDIUM
    assert map_bandit_severity("MEDIUM", "HIGH") == Severity.MEDIUM
    assert map_bandit_severity("MEDIUM", "MEDIUM") == Severity.MEDIUM
    assert map_bandit_severity("MEDIUM", "LOW") == Severity.MEDIUM
    # LOW -> LOW
    assert map_bandit_severity("LOW", "HIGH") == Severity.LOW
    assert map_bandit_severity("LOW", "LOW") == Severity.LOW


def test_bandit_confidence_mapping():
    """Verify Bandit confidence levels map accurately to float values."""
    assert map_bandit_confidence("HIGH") == 0.9
    assert map_bandit_confidence("MEDIUM") == 0.7
    assert map_bandit_confidence("LOW") == 0.5
    assert map_bandit_confidence("UNDEFINED") == 0.5


def test_bandit_findings_always_security_category():
    """Verify all findings parsed from Bandit JSON use Category.SECURITY."""
    raw_bandit_output = """{
        "results": [
            {
                "test_id": "B307",
                "issue_text": "Use of eval",
                "filename": "/tmp/test/app.py",
                "line_number": 5,
                "line_range": [5],
                "issue_severity": "HIGH",
                "issue_confidence": "HIGH",
                "issue_cwe": {"id": 78}
            },
            {
                "test_id": "B105",
                "issue_text": "Hardcoded password",
                "filename": "/tmp/test/app.py",
                "line_number": 10,
                "line_range": [10],
                "issue_severity": "LOW",
                "issue_confidence": "LOW",
                "issue_cwe": {"id": 259}
            }
        ]
    }"""
    temp_dir = Path("/tmp/test")
    file_contents = {"app.py": "x = 1\n" * 15}
    added_lines = {"app.py": {5, 10}}

    findings = parse_bandit_json(
        raw_bandit_output, temp_dir, file_contents, added_lines
    )

    assert len(findings) == 2
    assert all(f.category == Category.SECURITY for f in findings)

    # First finding: HIGH + HIGH -> CRITICAL, confidence 0.9
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].confidence == 0.9

    # Second finding: LOW + LOW -> LOW, confidence 0.5
    assert findings[1].severity == Severity.LOW
    assert findings[1].confidence == 0.5


def test_ruff_rule_selection():
    """Verify Ruff enabled rules match curated policy."""
    assert RUFF_SELECTED_RULES == "F,E9,B,PERF"


def test_ruff_rule_category_and_severity_mapping():
    """Verify consistent mapping of Ruff rule prefixes."""
    # PERF -> PERFORMANCE, MEDIUM
    assert map_ruff_category("PERF401") == Category.PERFORMANCE
    assert map_ruff_severity("PERF401") == Severity.MEDIUM

    # B (Bugbear) -> LOGIC_BUG, HIGH
    assert map_ruff_category("B006") == Category.LOGIC_BUG
    assert map_ruff_severity("B006") == Severity.HIGH

    # F821 (Undefined name) -> LOGIC_BUG, HIGH
    assert map_ruff_category("F821") == Category.LOGIC_BUG
    assert map_ruff_severity("F821") == Severity.HIGH

    # E902 (IO / Syntax error) -> LOGIC_BUG, HIGH
    assert map_ruff_category("E902") == Category.LOGIC_BUG
    assert map_ruff_severity("E902") == Severity.HIGH

    # F401 (Unused import) -> CODE_QUALITY, LOW
    assert map_ruff_category("F401") == Category.CODE_QUALITY
    assert map_ruff_severity("F401") == Severity.LOW

    # F841 (Unused variable) -> CODE_QUALITY, LOW
    assert map_ruff_category("F841") == Category.CODE_QUALITY
    assert map_ruff_severity("F841") == Severity.LOW
