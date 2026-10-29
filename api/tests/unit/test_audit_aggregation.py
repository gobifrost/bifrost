"""Opaque access-check aggregate selectors are strictly validated before drill-in."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from shared.audit_aggregation import AggregateSelector, decode_aggregate_key, encode_aggregate_key


def _key(**overrides: object) -> str:
    payload: dict[str, object] = {
        "v": 1,
        "interval": "1h",
        "window_start": "2026-10-05T10:00:00+00:00",
        "workflow_id": None,
        "resource_type": None,
        "organization_id": None,
        "operation_id": None,
        "user_id": None,
        "outcome": "failure",
    }
    payload.update(overrides)
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_aggregate_selector_round_trips_nullable_dimensions() -> None:
    selector = AggregateSelector(
        interval="6h",
        window_start=datetime(2026, 10, 5, 6, tzinfo=UTC),
        workflow_id=None,
        resource_type=None,
        organization_id=None,
        operation_id=None,
        user_id=None,
        outcome="failure",
    )

    assert decode_aggregate_key(encode_aggregate_key(selector)) == selector


@pytest.mark.parametrize(
    "key",
    [
        "@@not-base64@@",
        _key(v=True),
        _key(outcome="unknown"),
        _key(window_start="9999-12-31T00:00:00+00:00", interval="24h"),
    ],
)
def test_aggregate_selector_rejects_malformed_or_unrepresentable_values(key: str) -> None:
    with pytest.raises(ValueError):
        decode_aggregate_key(key)
