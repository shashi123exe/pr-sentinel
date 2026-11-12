"""Manual diagnostic script for verifying real Google Gemini integration.

IMPORTANT:
- This script is strictly for manual developer verification.
- It is NEVER executed during automated tests or CI runs.
- Requires environment variable RUN_LIVE_GEMINI=1.
- Requires GEMINI_API_KEY and GEMINI_MODEL to be configured in .env.
- Does NOT print or leak API keys or credentials.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.ai.gemini_client import GeminiClient, resolve_gemini_config
from app.ai.reviewer import AIReviewer
from app.errors import AppError
from app.schemas.diff import DiffHunk, DiffLine, ProcessedFile
from app.schemas.github import PRMetadata


async def main() -> int:
    """Run diagnostic review on a tiny sample pull request diff."""
    # Strict gate: refuse to run without explicit RUN_LIVE_GEMINI=1
    if os.environ.get("RUN_LIVE_GEMINI") != "1":
        print(
            "ERROR: RUN_LIVE_GEMINI environment variable must be set to '1' "
            "to execute live Gemini API calls.\n\n"
            "Usage:\n"
            "  $env:RUN_LIVE_GEMINI='1'\n"
            "  python scripts/try_gemini.py\n",
            file=sys.stderr,
        )
        return 1

    try:
        model_name, _ = resolve_gemini_config()
    except AppError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    print("==================================================")
    print("      PR Sentinel - Gemini Diagnostic Tool        ")
    print("==================================================")
    print(f"Active Model: {model_name}")
    print("API Key: [REDACTED - Configured]")
    print("Preparing sample pull request diff...")

    # Tiny sample diff with an intentional security vulnerability
    sample_diff = (
        "@@ -1,5 +1,6 @@\n"
        " def execute_query(user_query):\n"
        "-    pass\n"
        "+    # Potential SQL injection vulnerability\n"
        "+    query = f'SELECT * FROM users WHERE name = {user_query}'\n"
        "+    return db.execute(query)\n"
    )

    metadata = PRMetadata(
        owner="pr-sentinel-test",
        repo="test-repo",
        number=1,
        title="Add user query execution helper",
        author="developer",
        base_sha="abcdef1234567890abcdef1234567890abcdef12",
        head_sha="1234567890abcdef1234567890abcdef12345678",
        description="Introduces custom query helper function for user lookup.",
    )

    hunk = DiffHunk(
        old_start=1,
        old_lines=5,
        new_start=1,
        new_lines=6,
        lines=[
            DiffLine(
                kind="context",
                old_no=1,
                new_no=1,
                text="def execute_query(user_query):",
            ),
            DiffLine(kind="delete", old_no=2, new_no=None, text="    pass"),
            DiffLine(
                kind="add",
                old_no=None,
                new_no=2,
                text="    # Potential SQL injection vulnerability",
            ),
            DiffLine(
                kind="add",
                old_no=None,
                new_no=3,
                text="    query = f'SELECT * FROM users WHERE name = {user_query}'",
            ),
            DiffLine(
                kind="add", old_no=None, new_no=4, text="    return db.execute(query)"
            ),
        ],
    )

    file = ProcessedFile(
        path="app/db/query.py",
        language="python",
        status="modified",
        additions=3,
        deletions=1,
        hunks=[hunk],
        added_line_numbers={2, 3, 4},
        patch=sample_diff,
    )

    print("\nSending review request to Gemini API (asynchronously)...")
    client = GeminiClient()
    reviewer = AIReviewer(client=client)

    try:
        result = await reviewer.review(
            metadata=metadata,
            files=[file],
        )

        print("\nReview Completed Successfully!")
        print(f"Model used:     {result.model}")
        print(f"Batches:        {result.batches}")
        print(f"Tokens in:      {result.tokens_in}")
        print(f"Tokens out:     {result.tokens_out}")
        print(f"Truncated:      {result.truncated}")
        print(f"Findings count: {len(result.findings)}")

        if result.errors:
            print(f"Errors noted:   {result.errors}")

        print("\nFindings:")
        for idx, finding in enumerate(result.findings, 1):
            print(f"\n--- Finding #{idx} ---")
            print(f"Title:       [{finding.category.value}] {finding.title}")
            print(
                f"Severity:    {finding.severity.value} (Confidence: {finding.confidence:.2f})"
            )
            print(
                f"File/Lines:  {finding.file_path}:{finding.line_start}-{finding.line_end}"
            )
            print(f"Evidence:    {finding.evidence}")
            print(f"Explanation: {finding.explanation}")
            if finding.suggested_fix:
                print(f"Fix Advice:  {finding.suggested_fix}")

        return 0

    except (AppError, RuntimeError) as exc:
        print(f"\nError during Gemini API review: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
