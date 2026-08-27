"""Safe evaluation for event subscription payload filters."""

from dataclasses import dataclass
from typing import Any

import jmespath
from jmespath.exceptions import JMESPathError


@dataclass(frozen=True)
class EventFilterDecision:
    """The result of evaluating one subscription filter."""

    matches: bool
    error: str | None = None


def _normalize_expression(expression: str) -> str:
    """Accept the documented JSONPath-style root prefix in JMESPath expressions."""
    normalized = expression.strip()
    if normalized == "$":
        return "@"
    if normalized.startswith("$."):
        return normalized[2:]
    if normalized.startswith("$["):
        return normalized[1:]
    return normalized


def _is_truthy(value: Any) -> bool:
    """Apply JMESPath truthiness, where numeric zero remains truthy."""
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, dict)) and not value:
        return False
    return True


def evaluate_event_filter(
    expression: str | None,
    payload: dict[str, Any],
) -> EventFilterDecision:
    """Evaluate a JSONPath-style subscription filter against an event payload.

    Expressions use JMESPath operators and accept an optional JSONPath ``$`` root
    prefix for compatibility with the public event subscription contract. Invalid
    or unevaluable expressions fail closed.
    """
    if expression is None or not expression.strip():
        return EventFilterDecision(matches=True)

    try:
        result = jmespath.search(_normalize_expression(expression), payload)
    except JMESPathError as exc:
        return EventFilterDecision(matches=False, error=str(exc))

    return EventFilterDecision(matches=_is_truthy(result))
