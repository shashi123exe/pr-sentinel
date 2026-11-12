"""Unit tests for finding validation and evidence verification service."""

from app.db.models import AppSettings, Category, Severity, Source
from app.schemas.ai import AIFinding
from app.schemas.analysis import StaticFinding
from app.schemas.context import ContextSnippet
from app.schemas.diff import DiffHunk, DiffLine, ProcessedFile
from app.services.validation import (
    FindingValidator,
    extract_code_tokens,
    extract_patch_candidate_text,
    match_code_evidence,
    normalize_path,
    sanitize_string,
)


def _make_sample_file() -> ProcessedFile:
    """Create a sample ProcessedFile with realistic patch hunks and added lines."""
    patch_text = """@@ -10,6 +10,7 @@ def verify_jwt(token: str) -> dict:
     if not token:
         raise ValueError("Token is required")
+    decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
     return decoded
"""
    hunk = DiffHunk(
        old_start=10,
        old_lines=6,
        new_start=10,
        new_lines=7,
        lines=[
            DiffLine(
                kind="context",
                text="    if not token:",
                old_no=10,
                new_no=10,
            ),
            DiffLine(
                kind="context",
                text='        raise ValueError("Token is required")',
                old_no=11,
                new_no=11,
            ),
            DiffLine(
                kind="add",
                text=(
                    '    decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])'
                ),
                old_no=None,
                new_no=12,
            ),
            DiffLine(
                kind="context",
                text="    return decoded",
                old_no=12,
                new_no=13,
            ),
        ],
    )
    return ProcessedFile(
        path="app/auth/jwt.py",
        language="python",
        status="modified",
        additions=1,
        deletions=0,
        patch=patch_text,
        hunks=[hunk],
        added_line_numbers={12},
    )


def _make_sample_context() -> ContextSnippet:
    """Create a sample ContextSnippet for surrounding code."""
    return ContextSnippet(
        file_path="app/auth/jwt.py",
        start_line=1,
        end_line=5,
        text="""import jwt
SECRET_KEY = "hardcoded_secret_key"
ALGORITHM = "HS256"
""",
        reason="enclosing_file_context",
    )


def test_sanitize_string_and_normalize_path():
    """Verify string sanitization strips control chars and normalizes paths."""
    dirty = "Line 1\x00\x08with hidden\tcontrol\rchars\nNext line"
    cleaned = sanitize_string(dirty, max_length=100)
    assert "\x00" not in cleaned
    assert "\x08" not in cleaned
    assert "Line 1" in cleaned

    assert normalize_path("app\\auth\\jwt.py") == "app/auth/jwt.py"
    assert normalize_path("./app/auth/jwt.py") == "app/auth/jwt.py"


def test_extract_code_tokens_and_patch_candidate_text():
    """Verify code token extraction and patch candidate line extraction."""
    tokens = extract_code_tokens('decoded = jwt.decode(token, "SECRET", [HS256])')
    assert "decoded" in tokens
    assert "jwt" in tokens
    assert "decode" in tokens

    patch = "@@ -1,2 +1,2 @@\n-old_code()\n+new_code()\n"
    extracted = extract_patch_candidate_text(patch)
    assert "new_code()" in extracted
    assert "old_code()" not in extracted


def test_match_code_evidence_variants():
    """Verify exact, normalized, token sequence, and fuzzy evidence matching."""
    source = """
    def login(user, password):
        hashed = bcrypt.hashpw(password.encode(), salt)
        return db.query(user, hashed)
"""
    # 1. Exact match
    assert match_code_evidence(
        "hashed = bcrypt.hashpw(password.encode(), salt)", source
    )

    # 2. Whitespace variation
    assert match_code_evidence(
        "hashed   =   bcrypt.hashpw( password.encode(),   salt )", source
    )

    # 3. Diff prefix marker variation
    assert match_code_evidence(
        "+ hashed = bcrypt.hashpw(password.encode(), salt)", source
    )

    # 4. Conservative fuzzy match (>= 0.90)
    assert match_code_evidence(
        "hashed = bcrypt.hashpw(password.encode(),  salt)", source
    )

    # 5. Non-matching fabricated code
    assert not match_code_evidence("completely_unrelated_code_execution_call()", source)


def test_valid_ai_finding_accepted():
    """Verify a well-formed AI finding with verified evidence is accepted."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
        app_settings=AppSettings(min_confidence=0.70),
    )

    finding = AIFinding(
        title="Hardcoded SECRET_KEY in JWT decoding",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        evidence='decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        explanation="Using global SECRET_KEY without rotation support.",
        suggested_fix="Use environment variable for secret key.",
        confidence=0.95,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 1
    assert result.dropped_count == 0
    accepted = result.valid_findings[0]
    assert accepted["file_path"] == "app/auth/jwt.py"
    assert accepted["line_start"] == 12
    assert accepted["severity"] == Severity.HIGH.value
    assert accepted["source"] == Source.AI.value


def test_valid_ai_finding_verified_in_context_snippet():
    """Verify finding evidence verified against context snippet is accepted."""
    proc_file = _make_sample_file()
    snippet = _make_sample_context()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[snippet],
        app_settings=AppSettings(min_confidence=0.70),
    )

    finding = AIFinding(
        title="Hardcoded secret key constant",
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        file_path="app/auth/jwt.py",
        line_start=2,
        line_end=2,
        evidence='SECRET_KEY = "hardcoded_secret_key"',
        explanation="Secret key hardcoded at module level.",
        confidence=0.92,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 1
    assert result.dropped_count == 0
    assert result.valid_findings[0]["line_start"] == 2


def test_hallucinated_file_rejected():
    """Verify finding on file not part of PR is dropped with nonexistent_file."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
    )

    finding = AIFinding(
        title="SQL Injection in users controller",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        file_path="app/controllers/users.py",
        line_start=10,
        line_end=10,
        evidence="cursor.execute(f'SELECT * FROM users WHERE id = {user_id}')",
        explanation="Raw string interpolation in query",
        confidence=0.9,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 0
    assert result.dropped_count == 1
    assert "nonexistent_file" in result.drop_reasons


def test_path_traversal_rejected():
    """Verify directory traversal or absolute paths are dropped with invalid_path."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
    )

    finding = AIFinding(
        title="Traversal file finding",
        severity=Severity.MEDIUM,
        category=Category.SECURITY,
        file_path="../etc/shadow",
        line_start=1,
        line_end=1,
        evidence="root:x:0:0",
        explanation="Shadow file leak",
        confidence=0.9,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 0
    assert result.dropped_count == 1
    assert "invalid_path" in result.drop_reasons


def test_fabricated_evidence_rejected():
    """Verify finding with unverifiable code evidence is dropped."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
    )

    finding = AIFinding(
        title="Fabricated issue",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        evidence="os.system('rm -rf /')",
        explanation="Fabricated code not in patch or context",
        confidence=0.9,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 0
    assert result.dropped_count == 1
    assert "unverifiable_evidence" in result.drop_reasons


def test_evidence_from_another_file_rejected():
    """Verify genuine evidence from file B reported on file A is dropped."""
    proc_file = _make_sample_file()
    other_snippet = ContextSnippet(
        file_path="app/utils/other.py",
        start_line=1,
        end_line=2,
        text="other_token = 'abc'",
        reason="context",
    )
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[other_snippet],
    )

    finding = AIFinding(
        title="Mismatched file evidence",
        severity=Severity.MEDIUM,
        category=Category.SECURITY,
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        evidence="other_token = 'abc'",
        explanation="Evidence belongs to other.py",
        confidence=0.9,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 0
    assert result.dropped_count == 1
    assert "unverifiable_evidence" in result.drop_reasons


def test_out_of_bounds_line_rejected():
    """Verify finding outside hunk and context lines is dropped with invalid_line."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
    )

    finding = AIFinding(
        title="Out of hunk finding",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,
        file_path="app/auth/jwt.py",
        line_start=999,
        line_end=999,
        evidence='decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        explanation="Evidence matches line 12 but line 999 is outside hunks",
        confidence=0.9,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 0
    assert result.dropped_count == 1
    assert "invalid_line" in result.drop_reasons


def test_derive_line_number_when_missing():
    """Verify missing line number is derived from hunk match."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
    )

    finding_dict = {
        "title": "Missing line number finding",
        "severity": "high",
        "category": "security",
        "file_path": "app/auth/jwt.py",
        "line_start": None,
        "line_end": None,
        "evidence": 'decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        "explanation": "Derived line number from hunk match",
        "confidence": 0.9,
    }

    result = validator.validate_all(raw_ai_findings=[finding_dict])
    assert result.accepted_count == 1
    assert result.valid_findings[0]["line_start"] == 12


def test_low_confidence_rejected():
    """Verify finding below min_confidence threshold is dropped."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
        app_settings=AppSettings(min_confidence=0.80),
    )

    finding = AIFinding(
        title="Low confidence finding",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        evidence='decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        explanation="Low confidence guess",
        confidence=0.65,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 0
    assert result.dropped_count == 1
    assert "low_confidence" in result.drop_reasons


def test_disabled_category_rejected():
    """Verify finding in category not configured in AppSettings is dropped."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
        app_settings=AppSettings(enabled_categories=["security", "performance"]),
    )

    finding = AIFinding(
        title="Code style finding",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        evidence='decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        explanation="Style preference",
        confidence=0.9,
    )

    result = validator.validate_all(raw_ai_findings=[finding])
    assert result.accepted_count == 0
    assert result.dropped_count == 1
    assert "disabled_category" in result.drop_reasons


def test_static_finding_validation():
    """Verify static analysis finding validation and filtering."""
    proc_file = _make_sample_file()
    validator = FindingValidator(
        files=[proc_file],
        context_snippets=[],
        app_settings=AppSettings(enabled_categories=["security"]),
    )

    # 1. Valid static finding
    sf_valid = StaticFinding(
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        title="Bandit: Hardcoded secret",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        tool_name="bandit",
        rule_id="B105",
        evidence='decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        explanation="Hardcoded secret key detection",
        confidence=1.0,
    )

    # 2. Disabled category static finding
    sf_disabled = StaticFinding(
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        title="Ruff: Line length",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,
        tool_name="ruff",
        rule_id="E501",
        evidence="long line",
        explanation="Line too long",
        confidence=1.0,
    )

    # 3. Nonexistent file static finding
    sf_nonexistent = StaticFinding(
        file_path="app/other/missing.py",
        line_start=1,
        line_end=1,
        title="Bandit: Missing file",
        severity=Severity.MEDIUM,
        category=Category.SECURITY,
        tool_name="bandit",
        rule_id="B101",
        evidence="assert False",
        explanation="Assert in production",
        confidence=1.0,
    )

    result = validator.validate_all(
        raw_ai_findings=[],
        raw_static_findings=[sf_valid, sf_disabled, sf_nonexistent],
    )

    assert result.accepted_count == 1
    assert result.dropped_count == 2
    assert result.valid_findings[0]["title"] == "Bandit: Hardcoded secret"
    assert result.valid_findings[0]["source"] == Source.STATIC.value


def test_reversed_line_range_rejected_ai_and_static():
    """Verify reversed line ranges (line_start > line_end) are rejected."""
    proc_file = _make_sample_file()
    validator = FindingValidator(files=[proc_file], context_snippets=[])

    # 1. AI finding with reversed line range
    ai_reversed = AIFinding(
        title="Reversed range AI bug",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        file_path="app/auth/jwt.py",
        line_start=13,
        line_end=10,  # line_end < line_start
        evidence='decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        explanation="Reversed lines",
        confidence=0.9,
    )

    # 2. Static finding with reversed line range
    sf_reversed = StaticFinding(
        file_path="app/auth/jwt.py",
        line_start=13,
        line_end=10,  # line_end < line_start
        title="Ruff: Reversed range",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,
        tool_name="ruff",
        rule_id="E999",
        evidence="decoded = jwt.decode",
        explanation="Reversed",
        confidence=1.0,
    )

    res = validator.validate_all(
        raw_ai_findings=[ai_reversed], raw_static_findings=[sf_reversed]
    )
    assert res.accepted_count == 0
    assert res.dropped_count == 2
    assert res.drop_reasons.get("invalid_line") == 2


def test_path_traversal_unc_and_drive_letter_rejected():
    """Verify absolute, UNC, drive letter, and traversal paths are rejected."""
    proc_file = _make_sample_file()
    validator = FindingValidator(files=[proc_file], context_snippets=[])

    ev = 'decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])'
    malicious_paths = [
        "\\\\server\\share\\app\\auth\\jwt.py",
        "//server/share/app/auth/jwt.py",
        "C:\\app\\auth\\jwt.py",
        "/app/auth/jwt.py",
        "app/../../app/auth/jwt.py",
    ]

    for p in malicious_paths:
        f = AIFinding(
            title="Path injection test",
            severity=Severity.HIGH,
            category=Category.SECURITY,
            file_path=p,
            line_start=12,
            line_end=12,
            evidence=ev,
            explanation="Malicious path",
            confidence=0.9,
        )
        res = validator.validate_all(raw_ai_findings=[f])
        assert res.accepted_count == 0, f"Path {p} was not rejected"
        assert "invalid_path" in res.drop_reasons

    # Test static finding with traversal and UNC
    sf_traversal = StaticFinding(
        file_path="../../etc/shadow",
        line_start=1,
        line_end=1,
        title="Bandit: Traversal path",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        tool_name="bandit",
        rule_id="B101",
        evidence="root:x:0:0",
        explanation="Traversal",
        confidence=1.0,
    )
    res_sf = validator.validate_all(raw_static_findings=[sf_traversal])
    assert res_sf.accepted_count == 0
    assert "invalid_path" in res_sf.drop_reasons


def test_static_finding_preserves_suggested_fix():
    """Verify valid static analysis finding preserves its suggested fix."""
    proc_file = _make_sample_file()
    validator = FindingValidator(files=[proc_file], context_snippets=[])

    sf = StaticFinding(
        file_path="app/auth/jwt.py",
        line_start=12,
        line_end=12,
        title="Bandit: Hardcoded secret",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        tool_name="bandit",
        rule_id="B105",
        evidence='decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])',
        explanation="Secret key violation",
        confidence=1.0,
        suggested_fix="Use os.environ.get('JWT_SECRET')",
    )

    res = validator.validate_all(raw_static_findings=[sf])
    assert res.accepted_count == 1
    assert res.valid_findings[0]["suggested_fix"] == "Use os.environ.get('JWT_SECRET')"


def test_token_bridging_across_lines_prevented():
    """Verify token collapsing does not bridge tokens across distinct lines."""
    line1 = "def authenticate():"
    line2 = "    user = get_current_user()"
    candidate_text = f"{line1}\n{line2}"

    # "authenticateuser" was never in the code as a single token sequence
    hallucinated_bridged = "authenticateuser"
    assert not match_code_evidence(hallucinated_bridged, candidate_text)

    # Legitimate code on line 2 must match
    legitimate_ev = "user = get_current_user()"
    assert match_code_evidence(legitimate_ev, candidate_text)


def test_fuzzy_matching_minimum_length_requirement():
    """Verify sliding-window fuzzy matching requires at least 15 characters."""
    candidate = "let authToken = req.headers['authorization'];"

    # 8-char short string that differs slightly: should not match via fuzzy window
    short_fake = "authTokX"
    assert not match_code_evidence(short_fake, candidate)

    # 20-char string with 95% similarity should match via fuzzy window
    similar_long = "authToken = req.headers['authorizatiom'];"  # 1 typo at end
    assert match_code_evidence(similar_long, candidate)


def test_evidence_token_boundaries_and_whitespace_preservation():
    """Verify meaningful token boundaries are preserved during normalization.

    - 'authenticate user' must not become equivalent to 'authenticateuser'.
    - 'def auth():' must not become equivalent to 'defauth():'.
    - Harmless indentation and punctuation formatting variations must match.
    - Tokens must not bridge across line boundaries.
    """
    # 1. Distinct identifiers/tokens must not concatenate
    tok_separated = extract_code_tokens("authenticate user")
    tok_fused = extract_code_tokens("authenticateuser")
    assert tok_separated != tok_fused
    assert "authenticate user" in tok_separated
    assert "authenticateuser" not in tok_separated

    # 2. Keywords and function names must preserve token boundary
    tok_kw = extract_code_tokens("def auth():")
    tok_kw_fused = extract_code_tokens("defauth():")
    assert tok_kw != tok_kw_fused
    assert tok_kw.startswith("def auth(")

    # 3. Harmless formatting/indentation variations must normalize identically
    variant1 = extract_code_tokens("    def    auth(   token: str   )   -> dict :   ")
    variant2 = extract_code_tokens("def auth(token: str) -> dict:")
    assert variant1 == variant2

    # 4. In evidence matching, harmless formatting matches, fused token fails
    cand_code = """
def auth(token: str) -> dict:
    if not token:
        raise ValueError("Missing token")
    return {"user": "admin"}
"""
    assert match_code_evidence("def    auth(  token: str )  -> dict:", cand_code)
    assert not match_code_evidence("defauth():", cand_code)
    assert not match_code_evidence("Missingtoken", cand_code)

    # 5. Tokens must not bridge across line boundaries
    multiline_source = "authenticate\nuser"
    assert extract_code_tokens(multiline_source) != "authenticateuser"
    assert not match_code_evidence("authenticateuser", multiline_source)


def test_sanitize_string_and_patch_candidate_boundaries():
    """Verify string sanitization and patch candidate extraction edge cases."""
    assert sanitize_string(None) == ""
    assert sanitize_string("") == ""
    assert sanitize_string("a" * 100, max_length=10) == "a" * 10
    assert extract_patch_candidate_text(None) == ""
    assert extract_patch_candidate_text("") == ""


def test_validation_with_testing_category_enabled_settings():
    """Verify validator handles Category.TESTING in app_settings."""
    settings = AppSettings(
        enabled_categories=[Category.TESTING.value, Category.LOGIC_BUG.value]
    )
    file_obj = _make_sample_file()
    validator = FindingValidator(files=[file_obj], app_settings=settings)

    assert Category.TESTING.value in validator.enabled_categories
    assert Category.MISSING_TESTS.value in validator.enabled_categories

    # Finding with disabled category (SECURITY) should be dropped
    finding = AIFinding(
        file_path="app/auth.py",
        line_start=11,
        line_end=11,
        title="Disabled Category Finding",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        evidence="decoded = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])",
        explanation="Security issue",
        confidence=0.9,
    )
    res = validator.validate_all(raw_ai_findings=[finding])
    assert len(res.valid_findings) == 0
    assert res.dropped_count == 1
    assert "disabled_category" in res.drop_reasons


def test_derive_line_number_from_context_snippet():
    """Verify line number derivation when matched against surrounding context."""
    file_obj = ProcessedFile(
        path="app/config.py",
        status="modified",
        language="python",
        hunks=[],
        added_line_numbers={5},
        patch="@@ -1,1 +1,2 @@\n+PORT = 8080\n",
    )
    snippet = ContextSnippet(
        file_path="app/config.py",
        start_line=20,
        end_line=25,
        text="DATABASE_URL = 'sqlite:///test.db'\nDEBUG = True\nTIMEOUT = 30\n",
        reason="config_context",
    )

    validator = FindingValidator(files=[file_obj], context_snippets=[snippet])
    derived = validator._derive_line_number(
        evidence="DEBUG = True",
        target_file=file_obj,
        matched_context=snippet,
    )
    # Line 20 + offset 1 = 21
    assert derived == 21


def test_static_finding_dropped_when_category_disabled():
    """Verify static findings with disabled categories are properly dropped."""
    settings = AppSettings(enabled_categories=[Category.SECURITY.value])
    file_obj = _make_sample_file()
    validator = FindingValidator(files=[file_obj], app_settings=settings)

    static_finding = StaticFinding(
        file_path="app/auth.py",
        line_start=11,
        line_end=11,
        title="Unused variable x",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,  # Disabled
        tool_name="ruff",
        rule_id="F841",
        evidence="decoded = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])",
        explanation="Unused var",
        confidence=0.9,
    )

    res = validator.validate_all(raw_static_findings=[static_finding])
    assert len(res.valid_findings) == 0
    assert res.dropped_count == 1
    assert "disabled_category" in res.drop_reasons
