"""Normalized usage metadata extracted from Pydantic model responses."""

from decimal import Decimal, InvalidOperation

from pydantic_ai.messages import ModelResponse


def provider_reported_cost(response: ModelResponse) -> Decimal | None:
    """Return exact provider cost when the adapter preserved one."""

    raw = (response.provider_details or {}).get("cost")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
