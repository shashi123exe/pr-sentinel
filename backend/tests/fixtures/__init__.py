"""Test fixtures for PR Sentinel test suite."""

from tests.fixtures.sample_js_code import SAMPLE_JS_SOURCE
from tests.fixtures.sample_patches import (
    PATCH_ADDED_FILE,
    PATCH_DELETED_FILE,
    PATCH_MALFORMED,
    PATCH_MULTIPLE_HUNKS,
    PATCH_NO_NEWLINE,
    PATCH_RENAMED_FILE,
)
from tests.fixtures.sample_python_code import SAMPLE_PYTHON_SOURCE

__all__ = [
    "PATCH_ADDED_FILE",
    "PATCH_DELETED_FILE",
    "PATCH_MALFORMED",
    "PATCH_MULTIPLE_HUNKS",
    "PATCH_NO_NEWLINE",
    "PATCH_RENAMED_FILE",
    "SAMPLE_JS_SOURCE",
    "SAMPLE_PYTHON_SOURCE",
]
