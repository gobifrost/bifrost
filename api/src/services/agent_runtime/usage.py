"""Normalized usage metadata extracted from Pydantic model responses."""

from decimal import Decimal, InvalidOperation

from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage


def provider_reported_cost(response: ModelResponse) -> Decimal | None:
    """Return exact provider cost when the adapter preserved one."""

    raw = (response.provider_details or {}).get("cost")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


# Each adapter reports hidden reasoning under its provider's own name. All of
# them are already counted inside output_tokens.
_REASONING_DETAIL_KEYS = ("reasoning_tokens", "thinking_tokens", "thoughts_tokens")


def reasoning_tokens(usage: RequestUsage) -> int:
    """Hidden reasoning tokens for one request, whichever adapter served it."""

    return next(
        (usage.details[key] for key in _REASONING_DETAIL_KEYS if usage.details.get(key)),
        0,
    )
