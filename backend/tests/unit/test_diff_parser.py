"""Unit tests for unified diff parsing and line mapping."""

from app.services.diff_parser import (
    is_line_added,
    overlaps_added_lines,
    parse_diff_patch,
)
from tests.fixtures import (
    PATCH_ADDED_FILE,
    PATCH_DELETED_FILE,
    PATCH_MALFORMED,
    PATCH_MULTIPLE_HUNKS,
    PATCH_NO_NEWLINE,
    PATCH_RENAMED_FILE,
)


def test_parse_empty_or_none_patch():
    """Verify empty or None patches safely return empty hunks and line numbers."""
    hunks, added = parse_diff_patch(None)
    assert hunks == []
    assert added == set()

    hunks, added = parse_diff_patch("")
    assert hunks == []
    assert added == set()

    hunks, added = parse_diff_patch("   \n\t  ")
    assert hunks == []
    assert added == set()


def test_parse_multiple_hunks():
    """Verify patch with multiple hunks tracks correct line numbers and additions."""
    hunks, added_lines = parse_diff_patch(PATCH_MULTIPLE_HUNKS)

    assert len(hunks) == 2

    # Hunk 1: @@ -1,5 +1,6 @@
    h1 = hunks[0]
    assert h1.old_start == 1
    assert h1.old_lines == 5
    assert h1.new_start == 1
    assert h1.new_lines == 6

    # Added lines in hunk 1: msg = 'new' (line 2), return msg (line 3)
    assert 2 in added_lines
    assert 3 in added_lines
    assert is_line_added(added_lines, 2)
    assert is_line_added(added_lines, 3)
    assert not is_line_added(added_lines, 1)

    # Hunk 2: @@ -10,4 +11,5 @@
    h2 = hunks[1]
    assert h2.old_start == 10
    assert h2.new_start == 11
    # Added lines in hunk 2: farewell_msg = 'goodbye' (line 12),
    # return farewell_msg (line 13)
    assert 12 in added_lines
    assert 13 in added_lines


def test_parse_added_file():
    """Verify newly added file creates correct 1-based HEAD line numbers."""
    hunks, added_lines = parse_diff_patch(PATCH_ADDED_FILE)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.old_start == 0
    assert hunk.new_start == 1
    assert hunk.new_lines == 4

    # All 4 lines are additions: 1, 2, 3, 4
    assert added_lines == {1, 2, 3, 4}
    for line in hunk.lines:
        assert line.kind == "add"
        assert line.old_no is None
        assert line.new_no is not None


def test_parse_deleted_file():
    """Verify deleted file contains no added line numbers."""
    hunks, added_lines = parse_diff_patch(PATCH_DELETED_FILE)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.new_lines == 0
    assert added_lines == set()
    for line in hunk.lines:
        assert line.kind == "delete"
        assert line.new_no is None
        assert line.old_no is not None


def test_parse_renamed_file_with_modification():
    """Verify modified lines in renamed file are tracked."""
    hunks, added_lines = parse_diff_patch(PATCH_RENAMED_FILE)

    assert len(hunks) == 1
    # VERSION = "1.0.1" is at line 2
    assert added_lines == {2}
    assert is_line_added(added_lines, 2)
    assert not is_line_added(added_lines, 1)


def test_parse_patch_no_newline_marker():
    """Verify \\ No newline at end of file marker does not disrupt line numbering."""
    hunks, added_lines = parse_diff_patch(PATCH_NO_NEWLINE)

    assert len(hunks) == 1
    hunk = hunks[0]
    # Check that added lines were captured correctly (lines 2 and 3)
    assert 2 in added_lines
    assert 3 in added_lines

    # Ensure marker line itself was not added as a DiffLine
    assert not any("\\" in line.text for line in hunk.lines)


def test_parse_malformed_patch_graceful_recovery():
    """Verify malformed patches do not raise exceptions and parse any valid hunks."""
    hunks, added_lines = parse_diff_patch(PATCH_MALFORMED)

    # Valid hunk @@ -1,2 +1,2 @@ should be extracted despite earlier garbage
    assert len(hunks) == 1
    assert hunks[0].new_start == 1
    assert 2 in added_lines


def test_overlaps_added_lines():
    """Verify line range overlap helper."""
    added = {5, 6, 12}
    assert overlaps_added_lines(added, 5, 5)
    assert overlaps_added_lines(added, 4, 7)
    assert overlaps_added_lines(added, 10, 15)
    assert not overlaps_added_lines(added, 1, 4)
    assert not overlaps_added_lines(added, 7, 11)
    assert not overlaps_added_lines(set(), 1, 10)
