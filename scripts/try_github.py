"""FOR MANUAL TESTING ONLY. NEVER RUN IN CI OR AUTOMATED TESTS. DOES NOT MAKE MOCKED CALLS.

PR Sentinel - GitHub API Manual Diagnostic Tool
Usage:
    python scripts/try_github.py https://github.com/owner/repo/pull/123
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.errors import AppError
from app.services.github_client import GitHubClient
from app.services.url_parser import parse_pr_url


async def run_diagnostics(pr_url: str) -> None:
    """Fetch and print PR snapshot for manual diagnostic purposes."""
    print(f"Parsing PR URL: {pr_url}")
    try:
        parsed = parse_pr_url(pr_url)
    except AppError as exc:
        print(f"\n[Validation Error] ({exc.code}): {exc.message}")
        sys.exit(1)

    print(f"Target: {parsed.owner}/{parsed.repo} #{parsed.number}")
    has_token = bool(os.getenv("GITHUB_TOKEN", "").strip())
    auth_state = (
        "Configured (Token present)"
        if has_token
        else "Anonymous (Subject to low rate limits)"
    )
    print(f"Authentication: {auth_state}")
    print("Contacting GitHub REST API...")

    try:
        async with GitHubClient() as client:
            snapshot = await client.fetch_pr_snapshot(parsed)
    except AppError as exc:
        print(f"\n[GitHub Error] ({exc.code}) [HTTP {exc.status_code}]: {exc.message}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[Unexpected Error] {exc.__class__.__name__}: {exc}")
        sys.exit(1)

    meta = snapshot.metadata
    print("\n" + "=" * 55)
    print("           PR SENTINEL - GITHUB SNAPSHOT")
    print("=" * 55)
    print(f"Title:        {meta.title}")
    print(f"Author:       @{meta.author}")
    print(f"State:        {meta.state} (Merged: {meta.merged})")
    base_sha_str = meta.base_sha[:8] if meta.base_sha else "N/A"
    head_sha_str = meta.head_sha[:8] if meta.head_sha else "N/A"
    print(f"Base:         {meta.base_ref} ({base_sha_str})")
    print(f"Head:         {meta.head_ref} ({head_sha_str})")
    diff_stats = (
        f"+{meta.additions} / -{meta.deletions} across {meta.changed_files} file(s)"
    )
    print(f"Diff Stats:   {diff_stats}")
    print(f"HTML URL:     {meta.html_url}")
    print(f"Truncated:    {snapshot.truncated}")
    print("-" * 55)
    print(f"Changed Files Retrieved ({len(snapshot.files)}):")

    for idx, f in enumerate(snapshot.files, start=1):
        prev = f" (renamed from {f.previous_path})" if f.previous_path else ""
        print(
            f"  {idx:2d}. [{f.status:<8}] {f.path}{prev} "
            f"(+{f.additions}/-{f.deletions})"
        )

    print("=" * 55)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PR Sentinel manual GitHub PR retrieval tool. FOR MANUAL TESTING ONLY."
    )
    parser.add_argument(
        "url",
        help="GitHub Pull Request URL (e.g. https://github.com/owner/repo/pull/1)",
    )
    args = parser.parse_args()

    asyncio.run(run_diagnostics(args.url))


if __name__ == "__main__":
    main()
