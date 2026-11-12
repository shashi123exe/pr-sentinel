"""Unified diff parser for GitHub Pull Request patch strings."""

import logging
import re

from app.schemas.diff import DiffHunk, DiffLine

logger = logging.getLogger(__name__)

# Matches unified diff hunk headers: @@ -old_start,old_lines +new_start,new_lines @@
HUNK_HEADER_REGEX = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(?:[ \t]+(.*))?$"
)


def parse_diff_patch(patch: str | None) -> tuple[list[DiffHunk], set[int]]:
    """Parse a unified diff patch string into structured hunks and added line numbers.

    Args:
        patch: Raw unified diff string from GitHub API (or None).

    Returns:
        A tuple of:
          - list[DiffHunk]: Structured hunks containing diff metadata.
          - set[int]: 1-based line numbers in the HEAD file that were added.
    """
    if not patch or not patch.strip():
        return [], set()

    hunks: list[DiffHunk] = []
    added_lines: set[int] = set()

    current_hunk: DiffHunk | None = None
    cur_old: int = 0
    cur_new: int = 0

    lines = patch.splitlines()

    for line in lines:
        header_match = HUNK_HEADER_REGEX.match(line)
        if header_match:
            # Finalize previous hunk if any
            if current_hunk is not None:
                hunks.append(current_hunk)

            try:
                old_start = int(header_match.group(1))
                old_lines = (
                    int(header_match.group(2))
                    if header_match.group(2) is not None
                    else 1
                )
                new_start = int(header_match.group(3))
                new_lines = (
                    int(header_match.group(4))
                    if header_match.group(4) is not None
                    else 1
                )

                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_lines=old_lines,
                    new_start=new_start,
                    new_lines=new_lines,
                    lines=[],
                )
                cur_old = old_start
                cur_new = new_start
            except (ValueError, TypeError) as exc:
                logger.warning("Failed to parse hunk header '%s': %s", line, exc)
                current_hunk = None
            continue

        if current_hunk is None:
            # We are in diff header metadata (e.g. diff --git, index, ---, +++)
            continue

        # Handle "\ No newline at end of file" marker
        if line.startswith("\\"):
            continue

        if line.startswith("+"):
            content = line[1:]
            diff_line = DiffLine(
                kind="add",
                old_no=None,
                new_no=cur_new,
                text=content,
            )
            current_hunk.lines.append(diff_line)
            added_lines.add(cur_new)
            cur_new += 1

        elif line.startswith("-"):
            content = line[1:]
            diff_line = DiffLine(
                kind="delete",
                old_no=cur_old,
                new_no=None,
                text=content,
            )
            current_hunk.lines.append(diff_line)
            cur_old += 1

        elif line.startswith(" ") or line == "":
            content = line[1:] if line.startswith(" ") else ""
            diff_line = DiffLine(
                kind="context",
                old_no=cur_old,
                new_no=cur_new,
                text=content,
            )
            current_hunk.lines.append(diff_line)
            cur_old += 1
            cur_new += 1

        else:
            # Malformed line inside hunk - log warning and skip safely
            logger.warning("Skipping malformed diff line: %r", line)

    if current_hunk is not None:
        hunks.append(current_hunk)

    return hunks, added_lines


def is_line_added(added_lines: set[int], line_no: int) -> bool:
    """Check whether a specific 1-based new-file line was added or modified."""
    return line_no in added_lines


def overlaps_added_lines(added_lines: set[int], start_line: int, end_line: int) -> bool:
    """Check whether a line range [start_line, end_line] overlaps any added lines."""
    if not added_lines:
        return False
    return any(line in added_lines for line in range(start_line, end_line + 1))
