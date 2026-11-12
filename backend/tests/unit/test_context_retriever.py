"""Unit tests for repository context retrieval and snippet extraction."""

import base64

import pytest
import respx

from app.context.keyword_retriever import ContextLimits, KeywordSymbolRetriever
from app.schemas.context import ContextRequest
from app.schemas.diff import DiffHunk, DiffLine, ProcessedFile
from app.services.github_client import GitHubClient
from tests.fixtures import SAMPLE_JS_SOURCE, SAMPLE_PYTHON_SOURCE


def b64(text: str) -> str:
    """Helper to base64 encode text for respx GitHub contents responses."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@pytest.mark.asyncio
@respx.mock
async def test_context_retrieval_python_ast_and_surrounding_code():
    """Verify context retriever extracts hunks and AST symbols safely."""
    owner = "octocat"
    repo = "Hello-World"
    head_sha = "abc1234567"
    file_path = "app/service.py"

    # Mock HEAD content endpoint for app/service.py
    respx.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}",
        params={"ref": head_sha},
    ).respond(
        status_code=200,
        json={
            "type": "file",
            "size": len(SAMPLE_PYTHON_SOURCE),
            "encoding": "base64",
            "content": b64(SAMPLE_PYTHON_SOURCE),
        },
    )

    # File with changed lines inside vulnerable_function (lines 10-12)
    hunk = DiffHunk(
        old_start=10,
        old_lines=2,
        new_start=10,
        new_lines=3,
        lines=[
            DiffLine(
                kind="add", old_no=None, new_no=10, text="    result = eval(user_input)"
            ),
            DiffLine(
                kind="add",
                old_no=None,
                new_no=12,
                text="    print(unknown_variable_name)",
            ),
        ],
    )
    proc_file = ProcessedFile(
        path=file_path,
        language="python",
        status="modified",
        additions=2,
        deletions=0,
        hunks=[hunk],
        added_line_numbers={10, 12},
    )

    req = ContextRequest(
        files=[proc_file],
        head_sha=head_sha,
        owner=owner,
        repo=repo,
    )

    async with GitHubClient(token="ghp_test") as gh_client:
        retriever = KeywordSymbolRetriever(gh_client)
        result = await retriever.retrieve(req)

    assert len(result.snippets) > 0
    assert not result.truncated
    assert result.files_examined >= 1

    # Check that enclosing function vulnerable_function was captured
    reasons = [s.reason for s in result.snippets]
    assert any(
        "enclosing_function" in r or "changed_hunk_context" in r for r in reasons
    )
    symbols = [s.symbol for s in result.snippets if s.symbol]
    assert "vulnerable_function" in symbols or any(
        "vulnerable_function" in s.text for s in result.snippets
    )


@pytest.mark.asyncio
@respx.mock
async def test_context_retrieval_js_ts():
    """Verify JavaScript/TypeScript regex context extraction."""
    owner = "octocat"
    repo = "Hello-World"
    head_sha = "abc1234567"
    file_path = "src/process.js"

    respx.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}",
        params={"ref": head_sha},
    ).respond(
        status_code=200,
        json={
            "type": "file",
            "size": len(SAMPLE_JS_SOURCE),
            "encoding": "base64",
            "content": b64(SAMPLE_JS_SOURCE),
        },
    )

    hunk = DiffHunk(
        old_start=4,
        old_lines=1,
        new_start=4,
        new_lines=1,
        lines=[
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

    req = ContextRequest(
        files=[proc_file],
        head_sha=head_sha,
        owner=owner,
        repo=repo,
    )

    async with GitHubClient(token="ghp_test") as gh_client:
        retriever = KeywordSymbolRetriever(gh_client)
        result = await retriever.retrieve(req)

    assert len(result.snippets) > 0
    assert any("processInput" in s.text for s in result.snippets)


@pytest.mark.asyncio
@respx.mock
async def test_context_retrieval_local_relative_imports():
    """Verify bounded resolution and snippet extraction of local relative imports."""
    owner = "octocat"
    repo = "Hello-World"
    head_sha = "sha1"
    main_path = "app/main.py"
    util_path = "app/utils.py"

    main_code = "from .utils import helper\n\ndef run():\n    return helper()\n"
    util_code = "# Utilities module\ndef helper():\n    return 42\n"

    # Main file mock
    respx.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{main_path}",
        params={"ref": head_sha},
    ).respond(
        status_code=200,
        json={
            "type": "file",
            "size": len(main_code),
            "encoding": "base64",
            "content": b64(main_code),
        },
    )

    # Relative import mock for app/utils.py
    respx.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{util_path}",
        params={"ref": head_sha},
    ).respond(
        status_code=200,
        json={
            "type": "file",
            "size": len(util_code),
            "encoding": "base64",
            "content": b64(util_code),
        },
    )

    proc_file = ProcessedFile(
        path=main_path,
        language="python",
        status="modified",
        additions=1,
        deletions=0,
        hunks=[
            DiffHunk(
                old_start=3,
                old_lines=1,
                new_start=3,
                new_lines=2,
                lines=[DiffLine(kind="add", old_no=None, new_no=3, text="def run():")],
            )
        ],
        added_line_numbers={3},
    )

    req = ContextRequest(
        files=[proc_file],
        head_sha=head_sha,
        owner=owner,
        repo=repo,
    )

    async with GitHubClient(token="ghp_test") as gh_client:
        retriever = KeywordSymbolRetriever(gh_client)
        result = await retriever.retrieve(req)

    # Verify that snippets include both main.py and referenced utils.py
    file_paths = {s.file_path for s in result.snippets}
    assert main_path in file_paths
    assert util_path in file_paths


@pytest.mark.asyncio
@respx.mock
async def test_context_retrieval_limits_and_truncation():
    """Verify max_snippets and max_total_bytes enforce truncation."""
    owner = "octocat"
    repo = "Hello-World"
    head_sha = "sha1"
    code = "\n".join([f"line_{i} = {i}" for i in range(1, 100)])

    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/contents/app/large.py",
        params={"ref": head_sha},
    ).respond(
        status_code=200,
        json={
            "type": "file",
            "size": len(code),
            "encoding": "base64",
            "content": b64(code),
        },
    )

    proc_file = ProcessedFile(
        path="app/large.py",
        language="python",
        status="modified",
        additions=1,
        deletions=0,
        hunks=[
            DiffHunk(
                old_start=1,
                old_lines=1,
                new_start=1,
                new_lines=1,
                lines=[DiffLine(kind="add", old_no=None, new_no=1, text="line_1 = 1")],
            )
        ],
        added_line_numbers={1},
    )

    req = ContextRequest(files=[proc_file], head_sha=head_sha, owner=owner, repo=repo)

    # Restrict max_total_bytes to very small limit
    limits = ContextLimits(max_total_bytes=10)
    async with GitHubClient(token="ghp_test") as gh_client:
        retriever = KeywordSymbolRetriever(gh_client, limits=limits)
        result = await retriever.retrieve(req)

    assert result.truncated is True
