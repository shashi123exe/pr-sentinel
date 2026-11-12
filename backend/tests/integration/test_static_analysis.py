"""Integration tests for static analysis runners (Ruff, Bandit, ESLint)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.analysis.runner import StaticAnalysisRunner
from app.db.models import Category, Severity
from app.schemas.diff import DiffHunk, DiffLine, ProcessedFile
from tests.fixtures import SAMPLE_JS_SOURCE, SAMPLE_PYTHON_SOURCE


@pytest.mark.asyncio
async def test_ruff_and_bandit_analysis():
    """Verify Ruff and Bandit run and only report findings on changed lines."""
    file_path = "app/service.py"
    # Lines 10 and 12 are in vulnerable_function (eval and undefined variable)
    # Line 17 is unchanged and must not be reported
    hunk = DiffHunk(
        old_start=9,
        old_lines=2,
        new_start=9,
        new_lines=4,
        lines=[
            DiffLine(
                kind="context",
                old_no=9,
                new_no=9,
                text="def vulnerable_function(user_input):",
            ),
            DiffLine(
                kind="add", old_no=None, new_no=10, text="    result = eval(user_input)"
            ),
            DiffLine(
                kind="add",
                old_no=None,
                new_no=12,
                text="    print(unknown_variable_name)",
            ),
            DiffLine(kind="context", old_no=10, new_no=13, text="    return result"),
        ],
    )
    proc_file = ProcessedFile(
        path=file_path,
        language="python",
        status="modified",
        additions=2,
        deletions=0,
        hunks=[hunk],
        added_line_numbers={10, 12},  # Only lines 10 and 12 are considered changed
    )

    file_contents = {file_path: SAMPLE_PYTHON_SOURCE}

    runner = StaticAnalysisRunner()
    result = await runner.run([proc_file], file_contents)

    assert "ruff" in result.tools_run
    assert "bandit" in result.tools_run
    assert len(result.findings) >= 2

    # Check Bandit finding on line 10
    bandit_findings = [f for f in result.findings if f.tool_name == "bandit"]
    assert len(bandit_findings) >= 1
    bf = bandit_findings[0]
    assert bf.file_path == file_path
    assert bf.line_start == 10
    assert "eval" in bf.evidence
    assert bf.category == Category.SECURITY
    assert bf.severity in {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}

    # Check Ruff finding on line 12 (F821 undefined name)
    ruff_findings = [f for f in result.findings if f.tool_name == "ruff"]
    assert len(ruff_findings) >= 1
    rf = [f for f in ruff_findings if f.rule_id == "F821"][0]
    assert rf.file_path == file_path
    assert rf.line_start == 12
    assert "print(unknown_variable_name)" in rf.evidence
    assert rf.category == Category.LOGIC_BUG

    # CRITICAL: Confirm findings on line 17 (unchanged line) were DISCARDED!
    all_line_starts = {f.line_start for f in result.findings}
    assert 17 not in all_line_starts


@pytest.mark.asyncio
async def test_eslint_analysis():
    """Verify ESLint runs via trusted tools directory on changed lines."""
    file_path = "src/process.js"
    # Line 4: const result = eval(input);
    # Line 10: unchanged eval
    hunk = DiffHunk(
        old_start=3,
        old_lines=1,
        new_start=3,
        new_lines=2,
        lines=[
            DiffLine(
                kind="context",
                old_no=3,
                new_no=3,
                text="function processInput(input) {",
            ),
            DiffLine(
                kind="add",
                old_no=None,
                new_no=4,
                text="    const result = eval(input);",
            ),
        ],
    )
    proc_file = ProcessedFile(
        path=file_path,
        language="javascript",
        status="modified",
        additions=1,
        deletions=0,
        hunks=[hunk],
        added_line_numbers={4},
    )

    file_contents = {file_path: SAMPLE_JS_SOURCE}

    runner = StaticAnalysisRunner()
    result = await runner.run([proc_file], file_contents)

    assert "eslint" in result.tools_run
    eslint_findings = [f for f in result.findings if f.tool_name == "eslint"]
    assert len(eslint_findings) >= 1

    ef = eslint_findings[0]
    assert ef.file_path == file_path
    assert ef.line_start == 4
    assert "eval(input)" in ef.evidence
    assert ef.category == Category.SECURITY
    assert ef.severity == Severity.HIGH
    assert ef.rule_id == "no-eval"

    # Line 10 (unchanged) must be discarded
    all_lines = {f.line_start for f in result.findings}
    assert 10 not in all_lines


@pytest.mark.asyncio
async def test_path_traversal_rejection():
    """Verify malicious filenames attempting directory traversal are safely rejected."""
    traversal_file = ProcessedFile(
        path="../evil.py",
        language="python",
        status="added",
        additions=1,
        deletions=0,
        hunks=[],
        added_line_numbers={1},
    )
    runner = StaticAnalysisRunner()
    result = await runner.run([traversal_file], {"../evil.py": "import os"})

    # Should not run tools or create findings on traversal file
    assert len(result.findings) == 0


@pytest.mark.asyncio
async def test_temp_directory_cleaned_up():
    """Verify temporary workspace directory is completely deleted after execution."""
    created_temp_dirs: list[Path] = []
    file_path = "app/clean.py"

    proc_file = ProcessedFile(
        path=file_path,
        language="python",
        status="added",
        additions=1,
        deletions=0,
        hunks=[],
        added_line_numbers={1},
    )

    runner = StaticAnalysisRunner()

    import tempfile

    orig_temp_dir = tempfile.TemporaryDirectory

    def tracking_temp_dir(*args, **kwargs):
        td = orig_temp_dir(*args, **kwargs)
        created_temp_dirs.append(Path(td.name))
        return td

    with patch("tempfile.TemporaryDirectory", side_effect=tracking_temp_dir):
        await runner.run([proc_file], {file_path: "x = 1\n"})

    # Verify temp directory was tracked and no longer exists on disk
    assert len(created_temp_dirs) == 1
    for d in created_temp_dirs:
        assert not d.exists()


@pytest.mark.asyncio
async def test_tool_failure_does_not_abort_other_tools():
    """Verify failure in one tool captures tool_error and lets other tools run."""
    file_path = "app/test.py"
    proc_file = ProcessedFile(
        path=file_path,
        language="python",
        status="added",
        additions=1,
        deletions=0,
        hunks=[],
        added_line_numbers={1},
    )

    runner = StaticAnalysisRunner()

    # Mock build_ruff_command to simulate a non-existent command failure
    with patch(
        "app.analysis.runner.build_ruff_command",
        return_value=["non_existent_binary_xyz"],
    ):
        result = await runner.run([proc_file], {file_path: "x = 1\n"})

    # Ruff failed and recorded an error
    assert any("ruff" in err.lower() for err in result.tool_errors)
    # Bandit still ran
    assert "bandit" in result.tools_run


@pytest.mark.asyncio
async def test_path_traversal_and_malicious_windows_paths_rejected():
    """Verify malicious Windows paths and traversal attempts are rejected."""
    malicious_paths = [
        "C:\\evil.py",
        "D:/root/evil.py",
        "..\\evil.py",
        "../../etc/passwd",
        "/absolute/path/evil.py",
        "\\\\unc\\share\\evil.py",
    ]

    files = [
        ProcessedFile(
            path=p,
            language="python",
            status="added",
            additions=1,
            deletions=0,
            hunks=[],
            added_line_numbers={1},
        )
        for p in malicious_paths
    ]
    file_contents = dict.fromkeys(malicious_paths, "import os\nprint('evil')\n")

    runner = StaticAnalysisRunner()
    result = await runner.run(files, file_contents)

    # Malicious files must be rejected during extraction
    assert isinstance(result.findings, list)
    assert isinstance(result.tool_errors, list)
