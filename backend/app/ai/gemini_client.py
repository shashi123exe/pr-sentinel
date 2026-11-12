"""Gemini AI client wrapper with retry, timeout, and schema validation."""

import asyncio
import logging
import secrets
from typing import Any, Protocol

from pydantic import SecretStr

from app.config import get_settings
from app.errors import AppError
from app.schemas.ai import AIReviewResponse

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
MAX_RETRY_ATTEMPTS = 3
INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0
MAX_OUTPUT_TOKENS = 8192


def resolve_model_name(
    app_settings_model: str | None = None,
) -> str:
    """Resolve Gemini model according to configuration priority.

    Priority order:
    1. app_settings.gemini_model, when non-empty.
    2. GEMINI_MODEL from environment settings.

    Raises:
        AppError(code="ai_not_configured"): If model is missing.
    """
    settings = get_settings()
    if app_settings_model and app_settings_model.strip():
        return app_settings_model.strip()
    if settings.GEMINI_MODEL and settings.GEMINI_MODEL.strip():
        return settings.GEMINI_MODEL.strip()

    raise AppError(
        code="ai_not_configured",
        message=(
            "Gemini model is not configured. Set GEMINI_MODEL in environment "
            "or app settings."
        ),
        status_code=400,
    )


def resolve_gemini_config(
    app_settings_model: str | None = None,
) -> tuple[str, SecretStr]:
    """Resolve Gemini model and API key according to configuration priority.

    Raises:
        AppError(code="ai_not_configured"): If model or API key is missing.
    """
    settings = get_settings()
    model_name = resolve_model_name(app_settings_model)

    # Resolve API key: must be present and non-empty
    if (
        not settings.GEMINI_API_KEY
        or not settings.GEMINI_API_KEY.get_secret_value().strip()
    ):
        raise AppError(
            code="ai_not_configured",
            message="GEMINI_API_KEY is not configured.",
            status_code=400,
        )

    return model_name, settings.GEMINI_API_KEY


class GeminiClientProtocol(Protocol):
    """Protocol for Gemini AI review client to enable dependency injection."""

    async def generate_review(
        self,
        prompt: str,
        system_instruction: str,
        model: str,
    ) -> tuple[AIReviewResponse, int, int]:
        """Generate structured AI review response for a prompt.

        Returns:
            Tuple of (AIReviewResponse, prompt_tokens, candidate_tokens).
        """
        ...


class GeminiClient:
    """Production asynchronous client for Google Gemini using the official SDK."""

    def __init__(
        self,
        api_key: SecretStr | None = None,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._sdk_client: Any = None

    def _get_sdk_client(self) -> Any:
        """Lazily initialize Google GenAI SDK client without logging secrets."""
        if self._sdk_client is None:
            from google import genai

            key_value = (
                self._api_key.get_secret_value()
                if self._api_key
                else get_settings().GEMINI_API_KEY.get_secret_value()
                if get_settings().GEMINI_API_KEY
                else None
            )
            if not key_value or not key_value.strip():
                raise AppError(
                    code="ai_not_configured",
                    message="GEMINI_API_KEY is not configured.",
                    status_code=400,
                )
            self._sdk_client = genai.Client(api_key=key_value)
        return self._sdk_client

    async def generate_review(
        self,
        prompt: str,
        system_instruction: str,
        model: str,
    ) -> tuple[AIReviewResponse, int, int]:
        """Generate structured review with retry, backoff, and fallback parsing."""
        from google.genai import types

        client = self._get_sdk_client()

        # Build primary config with structured schema enforcement
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AIReviewResponse,
                temperature=0.2,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except (TypeError, ValueError) as exc:
            logger.warning(
                "SDK rejected response_schema (%s); falling back to JSON MIME output.",
                exc,
            )
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )

        active_model = model
        attempt = 0
        backoff = INITIAL_BACKOFF_SECONDS

        while attempt < MAX_RETRY_ATTEMPTS:
            attempt += 1
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=active_model,
                        contents=prompt,
                        config=config,
                    ),
                    timeout=self.timeout_seconds,
                )

                # Check safety filters
                if hasattr(response, "candidates") and response.candidates:
                    candidate = response.candidates[0]
                    finish_reason = getattr(candidate, "finish_reason", None)
                    if finish_reason and str(finish_reason).upper() == "SAFETY":
                        raise AppError(
                            code="ai_blocked",
                            message="Gemini response was blocked by safety filters.",
                            status_code=422,
                        )

                # Extract token usage safely
                tokens_in = 0
                tokens_out = 0
                if (
                    hasattr(response, "usage_metadata")
                    and response.usage_metadata is not None
                ):
                    tokens_in = (
                        getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                    )
                    tokens_out = (
                        getattr(response.usage_metadata, "candidates_token_count", 0)
                        or 0
                    )
                else:
                    logger.warning(
                        "Usage metadata missing from Gemini response; "
                        "recorded 0 tokens."
                    )

                # Parse response text into validated AIReviewResponse
                raw_text = getattr(response, "text", "") or ""
                if not raw_text.strip():
                    raise AppError(
                        code="ai_invalid_response",
                        message="Gemini returned an empty response.",
                        status_code=502,
                        details={"tokens_in": tokens_in, "tokens_out": tokens_out},
                    )

                try:
                    review_response = AIReviewResponse.model_validate_json(raw_text)
                    return review_response, tokens_in, tokens_out
                except Exception as parse_exc:
                    logger.warning(
                        "Failed to parse Gemini response as AIReviewResponse: %s",
                        parse_exc,
                    )
                    raise AppError(
                        code="ai_invalid_response",
                        message="Gemini response did not match expected schema.",
                        status_code=502,
                        details={"tokens_in": tokens_in, "tokens_out": tokens_out},
                    ) from parse_exc

            except (AppError, asyncio.CancelledError):
                raise

            except TimeoutError as timeout_exc:
                logger.warning(
                    "Gemini request timed out on attempt %d/%d (%.1fs)",
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                    self.timeout_seconds,
                )
                if attempt >= MAX_RETRY_ATTEMPTS:
                    raise AppError(
                        code="ai_unavailable",
                        message="Gemini API request timed out.",
                        status_code=503,
                    ) from timeout_exc

            except Exception as exc:
                err_str = str(exc)
                status_code = getattr(exc, "code", None)

                # Fall back to gemini-3.5-flash if legacy model returns 404 (deprecated)
                is_not_found = (
                    status_code == 404
                    or "404" in err_str
                    or "NOT_FOUND" in err_str.upper()
                )
                if is_not_found and active_model in (
                    "gemini-2.5-pro",
                    "gemini-2.5-flash",
                ):
                    logger.warning(
                        "Model %s is unavailable; falling back to gemini-3.5-flash",
                        active_model,
                    )
                    active_model = "gemini-3.5-flash"
                    attempt = 0
                    backoff = INITIAL_BACKOFF_SECONDS
                    continue

                # Check rate limiting (429 or ResourceExhausted)
                is_rate_limited = (
                    status_code == 429
                    or "429" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str.upper()
                )
                is_transient_server = (
                    status_code in {500, 502, 503, 504}
                    or any(code in err_str for code in ["500", "502", "503", "504"])
                    or "UNAVAILABLE" in err_str.upper()
                )

                if is_rate_limited or is_transient_server:
                    logger.warning(
                        "Transient Gemini failure on attempt %d/%d: %s. Retrying...",
                        attempt,
                        MAX_RETRY_ATTEMPTS,
                        err_str[:150],
                    )
                    if attempt >= MAX_RETRY_ATTEMPTS:
                        # Fallback from 3.8-flash to 3.5-flash on rate limit
                        # or 503 high demand spike
                        if active_model == "gemini-3.8-flash":
                            logger.warning(
                                "gemini-3.8-flash capacity/quota exhausted retries; "
                                "falling back to gemini-3.5-flash"
                            )
                            active_model = "gemini-3.5-flash"
                            attempt = 0
                            backoff = INITIAL_BACKOFF_SECONDS
                            continue
                        if active_model == "gemini-3.5-flash":
                            logger.warning(
                                "gemini-3.5-flash exhausted retries; "
                                "falling back to gemini-flash-lite-latest"
                            )
                            active_model = "gemini-flash-lite-latest"
                            attempt = 0
                            backoff = INITIAL_BACKOFF_SECONDS
                            continue

                        if is_rate_limited:
                            raise AppError(
                                code="ai_rate_limited",
                                message="Gemini API rate limit exceeded.",
                                status_code=429,
                            ) from exc
                        raise AppError(
                            code="ai_unavailable",
                            message="Gemini API is temporarily unavailable.",
                            status_code=503,
                        ) from exc
                else:
                    # Non-retryable client error
                    logger.error(
                        "Non-retryable Gemini error: %s",
                        err_str[:200],
                    )
                    raise AppError(
                        code="ai_unavailable",
                        message="Gemini API invocation failed.",
                        status_code=503,
                    ) from exc

            # Exponential backoff with jitter
            jitter = secrets.SystemRandom().uniform(0.0, 0.5)
            sleep_time = backoff + jitter
            await asyncio.sleep(sleep_time)
            backoff *= BACKOFF_MULTIPLIER

        raise AppError(
            code="ai_unavailable",
            message="Gemini API failed after maximum retry attempts.",
            status_code=503,
        )


class FakeGeminiClient:
    """Test fake for Gemini API client ensuring zero network calls in tests."""

    def __init__(
        self,
        responses: list[AIReviewResponse] | None = None,
        raw_responses: list[str] | None = None,
        errors: list[Exception] | None = None,
        tokens_in: int = 150,
        tokens_out: int = 50,
        missing_usage_metadata: bool = False,
    ) -> None:
        self.responses = list(responses or [])
        self.raw_responses = list(raw_responses or [])
        self.errors = list(errors or [])
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.missing_usage_metadata = missing_usage_metadata
        self.calls: list[dict[str, Any]] = []

    async def generate_review(
        self,
        prompt: str,
        system_instruction: str,
        model: str,
    ) -> tuple[AIReviewResponse, int, int]:
        """Record the call and return queued simulated response or raise error."""
        self.calls.append(
            {
                "prompt": prompt,
                "system_instruction": system_instruction,
                "model": model,
            }
        )

        if self.errors:
            err = self.errors.pop(0)
            raise err

        tin = 0 if self.missing_usage_metadata else self.tokens_in
        tout = 0 if self.missing_usage_metadata else self.tokens_out

        if self.raw_responses:
            raw = self.raw_responses.pop(0)
            try:
                parsed = AIReviewResponse.model_validate_json(raw)
                return parsed, tin, tout
            except Exception as exc:
                raise AppError(
                    code="ai_invalid_response",
                    message="Failed to parse raw fake JSON as AIReviewResponse.",
                    status_code=502,
                    details={"tokens_in": tin, "tokens_out": tout},
                ) from exc

        if self.responses:
            resp = self.responses.pop(0)
            return resp, tin, tout

        # Default empty review response
        return AIReviewResponse(findings=[]), tin, tout
