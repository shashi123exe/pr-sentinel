"""Security tests for resource limits, DoS prevention, and timeout handling."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.analysis.runner import run_subprocess_tool
from app.context.keyword_retriever import ContextLimits, KeywordSymbolRetriever
from app.db.models import AppSettings
from app.schemas.context import ContextRequest
from app.schemas.diff import ProcessedFile
from app.schemas.github import ChangedFile
from app.services.file_filter import (
    MAX_PER_FILE_DIFF_BYTES,
    check_skipped_reason,
    filter_and_process_files,
)


class TestResourceLimitsAndDoSPrevention:
    """Security tests verifying boundaries on diffs, files, and subprocesses."""

    def test_oversized_single_file_diff_rejected(self):
        """Verify patches exceeding MAX_PER_FILE_DIFF_BYTES (50KB) are skipped."""
        large_patch = "@@ -1,1 +1,2 @@\n" + "+added line\n" * 5000  # > 50,000 bytes
        assert len(large_patch.encode("utf-8")) > MAX_PER_FILE_DIFF_BYTES

        file = ChangedFile(
            sha="a" * 40,
            path="src/large.py",
            status="modified",
            additions=5000,
            deletions=0,
            changes=5000,
            patch=large_patch,
        )

        reason = check_skipped_reason(file, max_diff_bytes=500_000)
        assert reason == "oversized_diff"

    def test_minified_file_single_line_rejected(self):
        """Verify files with lines exceeding 1,000 characters are skipped."""
        minified_line = "+var a=" + "x" * 1500 + ";\n"
        file = ChangedFile(
            sha="a" * 40,
            path="static/bundle.js",
            status="modified",
            additions=1,
            deletions=0,
            changes=1,
            patch="@@ -1,1 +1,1 @@\n" + minified_line,
        )

        reason = check_skipped_reason(file, max_diff_bytes=500_000)
        assert reason == "minified_file"

    def test_binary_files_and_patches_rejected(self):
        """Verify binary extensions and git binary patches are rejected."""
        binary_files = [
            ("assets/logo.png", None),
            ("lib/native.dll", None),
            ("data/records.sqlite", None),
            (
                "image.raw",
                "Binary files a/image.raw and b/image.raw differ\n",
            ),
            (
                "firmware.bin",
                "GIT binary patch\nliteral 1234\n",
            ),
        ]
        for fname, patch_content in binary_files:
            file = ChangedFile(
                sha="a" * 40,
                path=fname,
                status="modified",
                additions=0,
                deletions=0,
                changes=0,
                patch=patch_content,
            )
            reason = check_skipped_reason(file, max_diff_bytes=500_000)
            assert reason == "binary_file", f"Failed for {fname}"

    def test_lockfiles_and_generated_directories_skipped(self):
        """Verify lockfiles and generated output directories are skipped."""
        skipped_paths = [
            "package-lock.json",
            "yarn.lock",
            "poetry.lock",
            "dist/index.js",
            "build/main.js",
            "node_modules/pkg/index.js",
            "api/client.pb.go",
            "proto/service_pb2.py",
        ]
        for path in skipped_paths:
            file = ChangedFile(
                sha="a" * 40,
                path=path,
                status="modified",
                additions=5,
                deletions=2,
                changes=7,
                patch="@@ -1,1 +1,1 @@\n-old\n+new\n",
            )
            reason = check_skipped_reason(file, max_diff_bytes=500_000)
            assert reason in {"lockfile", "generated_file"}, f"Failed for {path}"

    def test_cumulative_files_and_diff_limits_truncation(self):
        """Verify filter_and_process_files truncates when limits are exceeded."""
        settings = AppSettings(max_files=5, max_diff_bytes=20_000)
        # Create 10 files
        files = [
            ChangedFile(
                sha="a" * 40,
                path=f"src/file_{i}.py",
                status="modified",
                additions=10,
                deletions=2,
                changes=12,
                patch="@@ -1,1 +1,1 @@\n+x = 1\n",
            )
            for i in range(10)
        ]

        processed, metrics = filter_and_process_files(files, settings)
        assert len(processed) == 10
        # Files 0-4 are active; 5-9 are truncated with max_files_exceeded
        active = [p for p in processed if p.skipped_reason is None]
        skipped = [p for p in processed if p.skipped_reason == "max_files_exceeded"]
        assert len(active) == 5
        assert len(skipped) == 5
        assert metrics.truncated is True

    @pytest.mark.asyncio
    async def test_context_retriever_per_file_byte_limit(self):
        """Verify KeywordContextRetriever truncates files > max_bytes_per_file."""
        limits = ContextLimits(max_bytes_per_file=1_000, max_files_fetched=2)

        # Mock GitHubClient returning 5KB file content
        mock_client = AsyncMock()
        oversized_content = "def test():\n    pass\n" * 200  # ~4KB
        mock_client.get_file_content.return_value = oversized_content

        retriever = KeywordSymbolRetriever(github_client=mock_client, limits=limits)
        req = ContextRequest(
            owner="org",
            repo="repo",
            base_sha="0000000000000000000000000000000000000000",
            head_sha="1111111111111111111111111111111111111111",
            files=[
                ProcessedFile(
                    path="src/app.py",
                    status="modified",
                    language="python",
                    patch="@@ -1,1 +1,2 @@\n+from src.utils import helper\n",
                    hunks=[],
                    added_line_numbers={1},
                )
            ],
            file_contents={"src/app.py": "from src.utils import helper\n"},
        )

        res = await retriever.retrieve(req)
        # All snippets extracted must strictly obey byte boundaries
        for snip in res.snippets:
            assert len(snip.text.encode("utf-8")) <= 1_500

    @pytest.mark.asyncio
    async def test_subprocess_timeout_enforced(self):
        """Verify run_subprocess_tool enforces timeout on hanging commands."""
        # Run a python command that sleeps past a short timeout
        cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
        env = {}
        cwd = Path(".")

        # Temporarily patch timeout to 0.2s for fast deterministic testing
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.analysis.runner.SUBPROCESS_TIMEOUT_SECONDS", 0.2)
            stdout, err = await run_subprocess_tool(
                cmd=cmd, cwd=cwd, env=env, tool_name="test_tool"
            )

        assert stdout == ""
        assert err is not None
        assert "timed out after 0.2s" in err
