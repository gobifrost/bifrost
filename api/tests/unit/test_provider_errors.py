"""Tests for plain-language provider error descriptions."""

import httpx
import openai
from pydantic_ai.exceptions import ModelHTTPError

from src.services.llm.provider_errors import describe_provider_error


def _http_error(status: int, body: object) -> ModelHTTPError:
    return ModelHTTPError(status_code=status, model_name="m", body=body)


def test_rejected_key_uses_the_nested_provider_message() -> None:
    error = _http_error(
        401,
        {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
    )
    assert describe_provider_error(error) == (
        "The provider rejected the API key. The provider said: invalid x-api-key."
    )


def test_top_level_message_is_used_when_nothing_is_nested() -> None:
    error = _http_error(400, {"message": "openai/nope is not a valid model ID", "code": 400})
    assert describe_provider_error(error) == (
        "The provider refused the request (HTTP 400). "
        "The provider said: openai/nope is not a valid model ID."
    )


def test_a_body_without_a_message_gives_only_the_summary() -> None:
    assert describe_provider_error(_http_error(404, "<html>not found</html>")) == (
        "The provider does not recognize this model."
    )
    assert describe_provider_error(_http_error(503, None)) == (
        "The provider is unavailable (HTTP 503)."
    )


def test_long_provider_messages_are_shortened() -> None:
    message = describe_provider_error(_http_error(403, {"error": {"message": "x" * 500}}))
    assert len(message) < 300
    assert message.endswith("…")


def test_unreachable_provider() -> None:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    assert describe_provider_error(openai.APIConnectionError(request=request)) == (
        "Bifrost could not reach the provider."
    )


def test_unrecognized_failure_does_not_leak_its_text() -> None:
    assert describe_provider_error(RuntimeError("{'secret': 'payload'}")) == (
        "The request failed before the provider answered."
    )
