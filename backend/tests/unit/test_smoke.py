"""Smoke test for PR Sentinel backend environment."""

import importlib.metadata

import fastapi
import google.genai
import httpx
import langgraph
import pydantic
import slowapi
import sqlalchemy


def test_backend_environment_imports():
    """Verify backend and AI dependencies are installed and importable."""
    assert fastapi.__name__ == "fastapi"
    assert pydantic.__name__ == "pydantic"
    assert sqlalchemy.__name__ == "sqlalchemy"
    assert httpx.__name__ == "httpx"
    assert langgraph.__name__ == "langgraph"
    assert slowapi.__name__ == "slowapi"
    assert google.genai.__name__ == "google.genai"

    assert importlib.metadata.version("langgraph") == "1.2.19"
    assert importlib.metadata.version("fastapi") == "0.162.0"
