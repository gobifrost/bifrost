"""Validated opaque selectors for fixed-window access-check aggregation."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

AggregateInterval = Literal["1h", "6h", "24h"]

_INTERVALS: dict[AggregateInterval, timedelta] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
}
_KEY_FIELDS = {
    "v",
    "interval",
    "window_start",
    "workflow_id",
    "resource_type",
    "organization_id",
    "operation_id",
    "user_id",
    "outcome",
}


@dataclass(frozen=True)
class AggregateSelector:
    """One access-check aggregate's fixed time window and composite dimensions."""

    interval: AggregateInterval
    window_start: datetime
    workflow_id: str | None
    resource_type: str | None
    organization_id: UUID | None
    operation_id: str | None
    user_id: UUID | None
    outcome: str

    @property
    def window_end(self) -> datetime:
        return self.window_start + _INTERVALS[self.interval]


def encode_aggregate_key(selector: AggregateSelector) -> str:
    """Encode a server-created aggregate selector for a subsequent raw drill-in."""
    payload = {
        "v": 1,
        "interval": selector.interval,
        "window_start": selector.window_start.isoformat(),
        "workflow_id": selector.workflow_id,
        "resource_type": selector.resource_type,
        "organization_id": None if selector.organization_id is None else str(selector.organization_id),
        "operation_id": selector.operation_id,
        "user_id": None if selector.user_id is None else str(selector.user_id),
        "outcome": selector.outcome,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return encoded.decode().rstrip("=")


def decode_aggregate_key(value: str) -> AggregateSelector:
    """Decode a selector only when every typed field describes a real UTC bucket."""
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid aggregate selector") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != _KEY_FIELDS
        or type(payload.get("v")) is not int
        or payload["v"] != 1
    ):
        raise ValueError("invalid aggregate selector")
    interval = payload["interval"]
    if not isinstance(interval, str) or interval not in _INTERVALS:
        raise ValueError("invalid aggregate selector interval")
    interval = cast(AggregateInterval, interval)
    window_start = _utc_bucket_start(payload["window_start"], interval)
    selector = AggregateSelector(
        interval=interval,
        window_start=window_start,
        workflow_id=_nullable_string(payload["workflow_id"], "workflow_id"),
        resource_type=_nullable_string(payload["resource_type"], "resource_type"),
        organization_id=_nullable_uuid(payload["organization_id"], "organization_id"),
        operation_id=_nullable_string(payload["operation_id"], "operation_id"),
        user_id=_nullable_uuid(payload["user_id"], "user_id"),
        outcome=_string(payload["outcome"], "outcome"),
    )
    if selector.outcome not in {"success", "failure"}:
        raise ValueError("invalid aggregate selector outcome")
    try:
        selector.window_end
    except OverflowError as error:
        raise ValueError("aggregate selector window exceeds the supported time range") from error
    return selector


def _utc_bucket_start(value: object, interval: AggregateInterval) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid aggregate selector window_start")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid aggregate selector window_start") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0) or parsed.microsecond:
        raise ValueError("aggregate selector window_start must be UTC and on a whole second")
    parsed = parsed.astimezone(UTC)
    if int(parsed.timestamp()) % int(_INTERVALS[interval].total_seconds()):
        raise ValueError("aggregate selector window_start is not interval-aligned")
    return parsed


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid aggregate selector {field}")
    return value


def _nullable_string(value: object, field: str) -> str | None:
    return None if value is None else _string(value, field)


def _nullable_uuid(value: object, field: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid aggregate selector {field}")
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError(f"invalid aggregate selector {field}") from error
