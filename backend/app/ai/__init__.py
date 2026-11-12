"""AI review package using Google Gemini."""

from app.ai.gemini_client import (
    FakeGeminiClient,
    GeminiClient,
    GeminiClientProtocol,
    resolve_gemini_config,
    resolve_model_name,
)
from app.ai.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
    escape_xml_attribute,
    neutralize_delimiters,
)
from app.ai.reviewer import AIReviewer

__all__ = [
    "AIReviewer",
    "FakeGeminiClient",
    "GeminiClient",
    "GeminiClientProtocol",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "escape_xml_attribute",
    "neutralize_delimiters",
    "resolve_gemini_config",
    "resolve_model_name",
]
