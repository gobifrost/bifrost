"""Deterministic renewable webhook adapter for development and test stacks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    RenewResult,
    SubscribeResult,
    WebhookAdapter,
    WebhookRequest,
)


class LocalFixtureWebhookAdapter(WebhookAdapter):
    """Exercise subscription renewal without contacting an external provider."""

    name = "local_fixture"
    display_name = "Local Scheduler Fixture"
    description = "Development-only adapter used to prove scheduled renewal."
    renewal_interval = timedelta(minutes=1)

    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        return SubscribeResult(
            external_id="local-scheduler-fixture",
            state={"renewal_count": 0},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    async def unsubscribe(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> None:
        return None

    async def handle_request(
        self,
        request: WebhookRequest,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> HandleResult:
        return Deliver(event_type="local.fixture", data={})

    async def renew(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> RenewResult | None:
        if external_id != "local-scheduler-fixture":
            return None
        renewal_count = int(state.get("renewal_count", 0)) + 1
        return RenewResult(
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
            state={"renewal_count": renewal_count},
        )
