"""Unit tests for finding fingerprinting and deduplication service."""

from app.db.models import Category, Severity, Source
from app.services.dedupe import (
    compute_finding_fingerprint,
    deduplicate_findings,
    lines_overlap_or_adjacent,
    normalize_title_for_fingerprint,
    title_token_similarity,
)


def test_title_normalization_and_similarity():
    """Verify headline normalization and token similarity calculation."""
    t1 = "Hardcoded JWT Secret Key!"
    t2 = "hardcoded jwt secret key"
    assert normalize_title_for_fingerprint(t1) == normalize_title_for_fingerprint(t2)

    sim = title_token_similarity(
        "SQL injection in user login query",
        "SQL injection risk in login",
    )
    assert sim >= 0.50

    dissimilar = title_token_similarity(
        "Unused import statement",
        "Remote code execution via eval",
    )
    assert sim > dissimilar


def test_lines_overlap_or_adjacent():
    """Verify line overlap and adjacency check."""
    assert lines_overlap_or_adjacent(10, 15, 12, 14)  # Overlapping
    assert lines_overlap_or_adjacent(10, 12, 13, 15)  # Adjacent (12 and 13)
    assert not lines_overlap_or_adjacent(10, 12, 25, 30)  # Far apart


def test_identical_fingerprints_collapse_and_keep_higher_confidence():
    """Verify duplicate findings collapse and retain higher confidence finding."""
    f1 = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 12,
        "title": "Hardcoded secret",
        "severity": Severity.MEDIUM.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "SECRET = '123'",
        "explanation": "Short explanation",
        "confidence": 0.75,
    }
    f2 = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 12,
        "title": "Hardcoded secret",
        "severity": Severity.HIGH.value,  # Higher severity!
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "SECRET = '123'",
        "explanation": "Longer detailed explanation of secret exposure",
        "confidence": 0.95,  # Higher confidence!
    }

    deduped = deduplicate_findings([f1, f2])
    assert len(deduped) == 1
    retained = deduped[0]
    assert retained["confidence"] == 0.95
    assert retained["severity"] == Severity.HIGH.value
    assert "Longer detailed" in retained["explanation"]


def test_static_plus_ai_duplicates_merge():
    """Verify overlapping Static and AI findings merge into static primary."""
    static_f = {
        "file_path": "app/db.py",
        "line_start": 45,
        "line_end": 45,
        "title": "Bandit: SQL injection risk",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.STATIC.value,
        "tool_name": "bandit",
        "rule_id": "B608",
        "evidence": "db.execute(query)",
        "explanation": "Possible SQL injection vector via string format.",
        "suggested_fix": None,
        "confidence": 1.0,
    }
    ai_f = {
        "file_path": "app/db.py",
        "line_start": 45,
        "line_end": 46,
        "title": "SQL Injection in query execution",
        "severity": Severity.CRITICAL.value,  # AI assigned CRITICAL!
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "db.execute(query)",
        "explanation": "Untrusted input concatenated into SQL statement.",
        "suggested_fix": "Use parameterized query: db.execute(sql, (param,))",
        "confidence": 0.95,
    }

    deduped = deduplicate_findings([static_f, ai_f])
    assert len(deduped) == 1
    merged = deduped[0]

    # Primary remains static tool
    assert merged["source"] == Source.STATIC.value
    assert merged["tool_name"] == "bandit"
    assert merged["rule_id"] == "B608"

    # Preserves highest severity (Critical from AI)
    assert merged["severity"] == Severity.CRITICAL.value

    # AI suggested_fix and explanation merged
    assert merged["suggested_fix"] == ai_f["suggested_fix"]
    assert "Untrusted input" in merged["explanation"]


def test_different_categories_not_merged():
    """Verify findings at same location with different categories are kept separate."""
    f_sec = {
        "file_path": "app/auth.py",
        "line_start": 20,
        "line_end": 20,
        "title": "Insecure token generation",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "token = random.random()",
        "explanation": "Weak PRNG",
        "confidence": 0.9,
    }
    f_perf = {
        "file_path": "app/auth.py",
        "line_start": 20,
        "line_end": 20,
        "title": "Repeated PRNG initialization",
        "severity": Severity.LOW.value,
        "category": Category.PERFORMANCE.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "token = random.random()",
        "explanation": "High CPU overhead",
        "confidence": 0.85,
    }

    deduped = deduplicate_findings([f_sec, f_perf])
    assert len(deduped) == 2


def test_deterministic_sorting():
    """Verify findings are sorted deterministically: Critical -> High -> Med -> Low."""
    f_low = {
        "file_path": "app/a.py",
        "line_start": 1,
        "line_end": 1,
        "title": "Low issue",
        "severity": Severity.LOW.value,
        "category": Category.CODE_QUALITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "pass",
        "explanation": "Low",
        "confidence": 0.9,
    }
    f_med = {
        "file_path": "app/b.py",
        "line_start": 1,
        "line_end": 1,
        "title": "Medium issue",
        "severity": Severity.MEDIUM.value,
        "category": Category.LOGIC_BUG.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "x = 1",
        "explanation": "Medium",
        "confidence": 0.8,
    }
    f_high = {
        "file_path": "app/c.py",
        "line_start": 1,
        "line_end": 1,
        "title": "High issue",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "token = 'x'",
        "explanation": "High",
        "confidence": 0.85,
    }
    f_crit = {
        "file_path": "app/d.py",
        "line_start": 1,
        "line_end": 1,
        "title": "Critical issue",
        "severity": Severity.CRITICAL.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "eval(code)",
        "explanation": "Critical",
        "confidence": 0.99,
    }

    # Pass in reverse order
    deduped = deduplicate_findings([f_low, f_med, f_high, f_crit])
    assert len(deduped) == 4
    assert deduped[0]["severity"] == Severity.CRITICAL.value
    assert deduped[1]["severity"] == Severity.HIGH.value
    assert deduped[2]["severity"] == Severity.MEDIUM.value
    assert deduped[3]["severity"] == Severity.LOW.value


def test_fingerprint_computation_and_collision_resolution():
    """Verify fingerprint uniqueness and collision resolution."""
    fp1 = compute_finding_fingerprint("app/auth.py", 10, "security", "JWT secret")
    fp2 = compute_finding_fingerprint("app/auth.py", 12, "security", "JWT secret")
    # Same line bucket (10 // 5 == 2 and 12 // 5 == 2) produces same bucket fingerprint
    assert fp1 == fp2

    # Deduplicating two distinct findings that hit collision resolution
    f_a = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 10,
        "title": "JWT secret violation A",
        "severity": Severity.MEDIUM.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "r1",
        "evidence": "key1",
        "explanation": "Violation A",
        "confidence": 0.8,
    }
    f_b = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Completely different issue B",
        "severity": Severity.MEDIUM.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "r2",
        "evidence": "key2",
        "explanation": "Violation B",
        "confidence": 0.8,
    }

    res = deduplicate_findings([f_a, f_b])
    assert len(res) == 2
    # Ensure fingerprints are unique
    assert res[0]["fingerprint"] != res[1]["fingerprint"]


def test_deduplication_preserves_path_case_sensitivity():
    """Verify distinct Git file paths differing only by case produce unique fps."""
    fp_upper = compute_finding_fingerprint("API.py", 10, "security", "Bug")
    fp_lower = compute_finding_fingerprint("api.py", 10, "security", "Bug")
    assert fp_upper != fp_lower

    f1 = {
        "file_path": "API.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Bug",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.STATIC.value,
        "tool_name": "bandit",
        "rule_id": "B1",
        "evidence": "ev",
        "explanation": "Upper API file",
        "confidence": 1.0,
    }
    f2 = {
        "file_path": "api.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Bug",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "ev",
        "explanation": "Lower api file",
        "confidence": 0.9,
    }

    # Should not merge because files are distinct
    res = deduplicate_findings([f1, f2])
    assert len(res) == 2


def test_collision_resolution_deterministic_regardless_of_input_order():
    """Verify fingerprints and sorting are identical regardless of input list order."""
    f_first = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Issue Alpha",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "r1",
        "evidence": "key1",
        "explanation": "Alpha",
        "confidence": 0.9,
    }
    f_second = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Issue Beta",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "r2",
        "evidence": "key2",
        "explanation": "Beta",
        "confidence": 0.9,
    }

    order_1 = deduplicate_findings([f_first, f_second])
    order_2 = deduplicate_findings([f_second, f_first])

    assert len(order_1) == len(order_2) == 2
    assert [f["fingerprint"] for f in order_1] == [f["fingerprint"] for f in order_2]
    assert [f["title"] for f in order_1] == [f["title"] for f in order_2]


def test_distinct_bugs_on_adjacent_lines_sharing_words_not_merged():
    """Verify distinct bugs sharing common words on adjacent lines are not merged."""
    static_null = {
        "file_path": "app/user.py",
        "line_start": 20,
        "line_end": 20,
        "title": "Bandit: Missing null check",
        "severity": Severity.MEDIUM.value,
        "category": Category.LOGIC_BUG.value,
        "source": Source.STATIC.value,
        "tool_name": "bandit",
        "rule_id": "B101",
        "evidence": "if user:",
        "explanation": "Missing null check",
        "confidence": 1.0,
    }
    ai_validation = {
        "file_path": "app/user.py",
        "line_start": 21,
        "line_end": 21,
        "title": "Missing validation check on auth token payload",
        "severity": Severity.HIGH.value,
        "category": Category.LOGIC_BUG.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "validate(token)",
        "explanation": "Validation check missing",
        "confidence": 0.85,
    }

    res = deduplicate_findings([static_null, ai_validation])
    assert len(res) == 2
    titles = {f["title"] for f in res}
    assert "Bandit: Missing null check" in titles
    assert "Missing validation check on auth token payload" in titles


def test_deterministic_tie_breaking_for_identical_fingerprints():
    """Verify identical fingerprint findings with same confidence select same winner.

    Regardless of whether input order is [F1, F2] or [F2, F1]:
    - The same final finding is selected.
    - The same fingerprint is produced.
    - Output is 100% deterministic.
    """
    f1 = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Hardcoded Secret Key",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "SECRET = 'alpha'",
        "explanation": "Alpha explanation detail",
        "confidence": 0.9,
    }
    f2 = {
        "file_path": "app/auth.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Hardcoded Secret Key",
        "severity": Severity.HIGH.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "SECRET = 'beta'",
        "explanation": "Beta explanation detail",
        "confidence": 0.9,
    }

    order_1 = deduplicate_findings([f1, f2])
    order_2 = deduplicate_findings([f2, f1])

    # Exactly 1 representative finding must be selected
    assert len(order_1) == 1
    assert len(order_2) == 1

    # Winner must be identical in both runs
    assert order_1[0]["explanation"] == order_2[0]["explanation"]
    assert order_1[0]["evidence"] == order_2[0]["evidence"]
    assert order_1[0]["fingerprint"] == order_2[0]["fingerprint"]
    assert order_1[0]["severity"] == Severity.HIGH.value
    assert order_1[0]["confidence"] == 0.9


def test_deduplicate_empty_and_single():
    """Verify deduplication gracefully handles empty and singleton inputs."""
    assert deduplicate_findings([]) == []
    single = {
        "file_path": "app/main.py",
        "line_start": 5,
        "line_end": 5,
        "title": "Unused variable",
        "severity": Severity.LOW.value,
        "category": Category.CODE_QUALITY.value,
        "source": Source.STATIC.value,
        "tool_name": "ruff",
        "rule_id": "F841",
        "evidence": "x = 1",
        "explanation": "Variable x unused",
        "confidence": 0.9,
    }
    result = deduplicate_findings([single])
    assert len(result) == 1
    assert result[0]["fingerprint"] is not None


def test_title_token_similarity_and_prefixes():
    """Verify similarity calculations strip tool prefixes and handle empty tokens."""
    # Only stopwords
    assert title_token_similarity("the and or", "in on of") == 0.0
    # Empty strings
    assert title_token_similarity("", "") == 0.0
    # Stripping tool prefixes
    sim = title_token_similarity(
        "Bandit: SQL injection risk", "ESLint: SQL injection risk"
    )
    assert sim == 1.0


def test_lines_overlap_or_adjacent_branches():
    """Verify all branch paths for interval overlap and adjacency."""
    # Strict overlap
    assert lines_overlap_or_adjacent(10, 20, 15, 25) is True
    # Forward adjacent within max_distance
    assert lines_overlap_or_adjacent(10, 12, 14, 16, max_distance=2) is True
    # Forward gap too far
    assert lines_overlap_or_adjacent(10, 12, 16, 18, max_distance=2) is False
    # Backward adjacent within max_distance
    assert lines_overlap_or_adjacent(14, 16, 10, 12, max_distance=2) is True
    # Backward gap too far
    assert lines_overlap_or_adjacent(16, 18, 10, 12, max_distance=2) is False


def test_merge_static_ai_severity_escalation_and_suggested_fix():
    """Verify merging static finding with AI escalates severity and adopts fix."""
    static_item = {
        "file_path": "app/db.py",
        "line_start": 20,
        "line_end": 20,
        "title": "SQL query formatting",
        "severity": Severity.LOW.value,
        "category": Category.SECURITY.value,
        "source": Source.STATIC.value,
        "tool_name": "bandit",
        "rule_id": "B608",
        "evidence": "query = 'SELECT *'",
        "explanation": "Static warning",
        "confidence": 0.8,
        "suggested_fix": None,
    }
    ai_item = {
        "file_path": "app/db.py",
        "line_start": 21,
        "line_end": 22,
        "title": "SQL query formatting risk",
        "severity": Severity.CRITICAL.value,
        "category": Category.SECURITY.value,
        "source": Source.AI.value,
        "tool_name": "gemini",
        "rule_id": "ai_review",
        "evidence": "query = 'SELECT *'",
        "explanation": "Critical vulnerability allows authentication bypass",
        "confidence": 0.95,
        "suggested_fix": "query = text('SELECT *')",
    }

    result = deduplicate_findings([static_item, ai_item])
    assert len(result) == 1
    merged = result[0]
    assert merged["source"] == Source.STATIC.value
    # Severity escalated to CRITICAL
    assert merged["severity"] == Severity.CRITICAL.value
    # Confidence elevated to 0.95
    assert merged["confidence"] == 0.95
    # Suggested fix adopted from AI
    assert merged["suggested_fix"] == "query = text('SELECT *')"
    # Explanations merged
    assert "[AI Review Confirmation]" in merged["explanation"]


def test_step4_collision_resolution():
    """Verify collision resolution when items have identical fingerprints."""
    # Force two distinct items to produce identical fingerprints
    item1 = {
        "file_path": "src/file.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Item 1",
        "category": Category.LOGIC_BUG.value,
        "source": Source.STATIC.value,
        "tool_name": "ruff",
        "rule_id": "R1",
        "evidence": "a = 1",
        "explanation": "Expl 1",
        "confidence": 0.9,
    }
    item2 = {
        "file_path": "src/file.py",
        "line_start": 10,
        "line_end": 10,
        "title": "Item 2",
        "category": Category.LOGIC_BUG.value,
        "source": Source.STATIC.value,
        "tool_name": "ruff",
        "rule_id": "R2",
        "evidence": "a = 1",
        "explanation": "Expl 2",
        "confidence": 0.8,
    }

    res = deduplicate_findings([item1, item2])
    # Both have distinct fingerprints
    fps = [r["fingerprint"] for r in res]
    assert len(set(fps)) == len(res)
