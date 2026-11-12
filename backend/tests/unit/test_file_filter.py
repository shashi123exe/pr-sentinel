"""Unit tests for file filtering, classification, and limit enforcement."""

from app.db.models import AppSettings
from app.schemas.github import ChangedFile
from app.services.file_filter import (
    detect_language,
    filter_and_process_files,
    is_test_file,
)


def test_detect_language():
    """Verify correct language identification from file extensions."""
    assert detect_language("app/main.py") == "python"
    assert detect_language("types.pyi") == "python"
    assert detect_language("src/App.tsx") == "tsx"
    assert detect_language("components/Button.jsx") == "jsx"
    assert detect_language("lib/utils.ts") == "typescript"
    assert detect_language("index.mts") == "typescript"
    assert detect_language("server.js") == "javascript"
    assert detect_language("module.mjs") == "javascript"
    assert detect_language("README.md") == "other"
    assert detect_language("docker-compose.yml") == "other"


def test_is_test_file():
    """Verify test file path identification."""
    assert is_test_file("tests/unit/test_main.py")
    assert is_test_file("src/__tests__/app.test.tsx")
    assert is_test_file("test_service.py")
    assert is_test_file("service_test.py")
    assert is_test_file("spec/models/user_spec.rb")
    assert is_test_file("components/Button.spec.tsx")
    assert not is_test_file("app/main.py")
    assert not is_test_file("src/components/Button.tsx")


def test_filter_binary_files():
    """Verify binary files and diff markers are skipped with binary_file reason."""
    settings = AppSettings(max_files=50, max_diff_bytes=500000)
    files = [
        ChangedFile(path="assets/logo.png", status="added", patch=None),
        ChangedFile(
            path="lib/native.dll", status="modified", patch="Binary files differ"
        ),
        ChangedFile(path="data.db", status="modified", patch="GIT binary patch"),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    assert len(processed) == 3
    assert all(p.skipped_reason == "binary_file" for p in processed)
    assert metrics.files_skipped == 3


def test_filter_lockfiles():
    """Verify dependency lockfiles are skipped with lockfile reason."""
    settings = AppSettings(max_files=50, max_diff_bytes=500000)
    files = [
        ChangedFile(
            path="frontend/package-lock.json",
            status="modified",
            patch="@@ -1 +1 @@\n-a\n+b",
        ),
        ChangedFile(path="poetry.lock", status="modified", patch="@@ -1 +1 @@\n-a\n+b"),
        ChangedFile(
            path="pnpm-lock.yaml", status="modified", patch="@@ -1 +1 @@\n-a\n+b"
        ),
        ChangedFile(path="Cargo.lock", status="modified", patch="@@ -1 +1 @@\n-a\n+b"),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    assert len(processed) == 4
    assert all(p.skipped_reason == "lockfile" for p in processed)
    assert metrics.files_skipped == 4


def test_filter_minified_and_long_line_files():
    """Verify .min.js/.min.css and files with lines > 1,000 chars are skipped."""
    settings = AppSettings(max_files=50, max_diff_bytes=500000)
    long_line = "+" + "x" * 1200
    files = [
        ChangedFile(
            path="vendor/bundle.min.js",
            status="added",
            patch="@@ -0,0 +1 @@\n+var a=1;",
        ),
        ChangedFile(
            path="styles/theme.min.css", status="added", patch="@@ -0,0 +1 @@\n+body{}"
        ),
        ChangedFile(
            path="data/blob.txt", status="modified", patch=f"@@ -1 +1 @@\n{long_line}"
        ),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    assert len(processed) == 3
    assert all(p.skipped_reason == "minified_file" for p in processed)


def test_filter_generated_and_deleted_files():
    """Verify generated artifacts and deleted files are skipped with reasons."""
    settings = AppSettings(max_files=50, max_diff_bytes=500000)
    files = [
        ChangedFile(path="dist/bundle.js", status="added", patch="@@ -0,0 +1 @@\n+1"),
        ChangedFile(
            path="node_modules/foo/index.js", status="added", patch="@@ -0,0 +1 @@\n+1"
        ),
        ChangedFile(
            path="proto/service.pb.go", status="added", patch="@@ -0,0 +1 @@\n+1"
        ),
        ChangedFile(
            path="models/user_pb2.py", status="added", patch="@@ -0,0 +1 @@\n+1"
        ),
        ChangedFile(
            path="tests/__snapshots__/app.snap",
            status="added",
            patch="@@ -0,0 +1 @@\n+1",
        ),
        ChangedFile(path="dist/main.js.map", status="added", patch="@@ -0,0 +1 @@\n+1"),
        ChangedFile(
            path="old_feature.py", status="removed", patch="@@ -1,2 +0,0 @@\n-a\n-b"
        ),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    assert len(processed) == 7
    # First 6 should be generated_file
    for p in processed[:6]:
        assert p.skipped_reason == "generated_file"
    # Deleted file
    assert processed[6].skipped_reason == "deleted_file"


def test_filter_oversized_diff():
    """Verify single file with patch larger than max_diff_bytes is skipped."""
    settings = AppSettings(max_files=50, max_diff_bytes=500)
    huge_patch = "@@ -0,0 +1 @@\n+" + "a" * 800
    files = [
        ChangedFile(path="huge.py", status="added", patch=huge_patch),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    assert len(processed) == 1
    assert processed[0].skipped_reason == "oversized_diff"


def test_deterministic_prioritization_and_max_files_truncation():
    """Verify source code is prioritized over tests/docs when max_files hit."""
    # max_files = 2: 1 doc file, 1 test file, 1 source file
    settings = AppSettings(max_files=2, max_diff_bytes=500000)
    files = [
        ChangedFile(path="README.md", status="modified", patch="@@ -1 +1 @@\n+docs"),
        ChangedFile(
            path="tests/test_app.py",
            status="added",
            patch="@@ -0,0 +1 @@\n+def test_x(): pass",
        ),
        ChangedFile(
            path="app/core.py", status="added", patch="@@ -0,0 +1 @@\n+def run(): pass"
        ),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    # Processed files returned: accepted files sorted by priority (core.py, test_app.py)
    accepted = [p for p in processed if p.skipped_reason is None]
    skipped = [p for p in processed if p.skipped_reason is not None]

    assert len(accepted) == 2
    assert accepted[0].path == "app/core.py"  # Priority 1: source code
    assert accepted[1].path == "tests/test_app.py"  # Priority 2: tests

    assert len(skipped) == 1
    assert skipped[0].path == "README.md"  # Priority 3 was dropped when cap hit
    assert skipped[0].skipped_reason == "max_files_exceeded"
    assert metrics.truncated is True
    assert metrics.reason == "max_files_exceeded"


def test_max_diff_bytes_truncation():
    """Verify files are truncated when cumulative diff bytes exceed max_diff_bytes."""
    settings = AppSettings(max_files=50, max_diff_bytes=100)
    patch_60_bytes = "@@ -1 +1 @@\n+" + "x" * 45  # ~60 bytes
    files = [
        ChangedFile(path="app/a.py", status="added", patch=patch_60_bytes),
        ChangedFile(path="app/b.py", status="added", patch=patch_60_bytes),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    accepted = [p for p in processed if p.skipped_reason is None]
    skipped = [p for p in processed if p.skipped_reason is not None]

    assert len(accepted) == 1
    assert accepted[0].path == "app/a.py"
    assert len(skipped) == 1
    assert skipped[0].path == "app/b.py"
    assert skipped[0].skipped_reason == "max_diff_bytes_exceeded"
    assert metrics.truncated is True
    assert metrics.bytes_dropped > 0


def test_filter_per_file_50kb_cap():
    """Verify single file with patch > 50KB is skipped even if max_diff_bytes
    is large.
    """
    settings = AppSettings(max_files=50, max_diff_bytes=500_000)
    # Multiline patch without lines > 1000 chars to avoid minified_file detection
    patch_lines = ["+line_of_code_example = 12345\n"] * 2000  # ~60KB
    patch_55kb = "@@ -0,0 +1,2000 @@\n" + "".join(patch_lines)
    files = [
        ChangedFile(path="big.py", status="added", patch=patch_55kb),
    ]

    processed, metrics = filter_and_process_files(files, settings)

    assert len(processed) == 1
    assert processed[0].skipped_reason == "oversized_diff"
    assert metrics.files_skipped == 1


def test_settings_safety_caps_bounding():
    """Verify AppSettings values are bounded against hard safety caps."""
    settings = AppSettings(max_files=500, max_diff_bytes=10_000_000)
    files = [
        ChangedFile(path=f"file_{i}.py", status="added", patch="@@ -0,0 +1 @@\n+x = 1")
        for i in range(205)
    ]

    processed, metrics = filter_and_process_files(files, settings)

    accepted = [p for p in processed if p.skipped_reason is None]
    assert len(accepted) == 200  # Hard capped at 200 files
    assert metrics.truncated is True
    assert metrics.reason == "max_files_exceeded"
