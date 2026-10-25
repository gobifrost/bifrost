"""Transaction-order regressions for provider-managed webhook creation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.events import EventSourceCreate
from src.models.enums import EventSourceType
from src.routers.events import create_source
from src.services.webhooks.protocol import SubscribeResult


def _session_that_assigns_ids() -> tuple[MagicMock, list[object]]:
    db = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock()
    # _validate_target_organization (routers/events.py) does `await
    # db.get(Organization, organization_id)` — simulate the target org
    # always existing so this transaction-ordering test isn't coupled to
    # that unrelated validation.
    db.get = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    added: list[object] = []

    def add(entity: object) -> None:
        if getattr(entity, "id", None) is None:
            entity.id = uuid4()
        added.append(entity)

    db.add.side_effect = add
    return db, added


def _request() -> EventSourceCreate:
    return EventSourceCreate.model_validate(
        {
            "name": "Graph callback ordering",
            "source_type": EventSourceType.WEBHOOK,
            "webhook": {
                "adapter_name": "test_provider",
                "config": {},
            },
        }
    )


def _caller(organization_id):
    return SimpleNamespace(
        principal=SimpleNamespace(runtime_authority=None),
        ctx=SimpleNamespace(home_organization_id=organization_id),
    )


def _stub_create_authorization(monkeypatch, organization_id):
    caller = _caller(organization_id)
    monkeypatch.setattr(
        "src.services.authorization.enforce.load_caller", AsyncMock(return_value=caller)
    )
    monkeypatch.setattr(
        "shared.scope_resolver.resolve_effective_scope", lambda **_kwargs: organization_id
    )
    monkeypatch.setattr(
        "src.services.authorization.enforce.user_reach",
        lambda _caller: SimpleNamespace(covers=lambda _target: True),
    )
    monkeypatch.setattr("src.services.authorization.enforce.require_permission", MagicMock())


@pytest.mark.asyncio
async def test_provider_subscription_starts_after_webhook_source_commit(monkeypatch):
    db, added = _session_that_assigns_ids()
    response = object()

    async def subscribe(**_kwargs):
        assert db.commit.await_count == 1
        assert len(added) == 2
        return SubscribeResult()

    adapter = SimpleNamespace(
        requires_integration=None,
        subscribe=AsyncMock(side_effect=subscribe),
    )
    registry = MagicMock()
    registry.get.return_value = adapter
    monkeypatch.setattr("src.services.webhooks.registry.get_adapter_registry", lambda: registry)
    _stub_create_authorization(monkeypatch, uuid4())
    monkeypatch.setattr(
        "src.routers.events._build_event_source_secret_response",
        AsyncMock(return_value=response),
    )
    async def execute(_statement):
        source = added[0]
        result = MagicMock()
        result.unique.return_value.scalar_one.return_value = source
        return result

    db.execute.side_effect = execute

    actual = await create_source(
        _request(),
        SimpleNamespace(org_id=uuid4(), user=SimpleNamespace(email="admin@example.com")),
        SimpleNamespace(),
        db,
    )

    assert actual is response
    assert db.commit.await_count == 1
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_provider_subscription_removes_provisional_source(monkeypatch):
    db, added = _session_that_assigns_ids()
    adapter = SimpleNamespace(
        requires_integration=None,
        subscribe=AsyncMock(side_effect=ValueError("provider rejected callback")),
    )
    registry = MagicMock()
    registry.get.return_value = adapter
    monkeypatch.setattr("src.services.webhooks.registry.get_adapter_registry", lambda: registry)
    _stub_create_authorization(monkeypatch, uuid4())

    with pytest.raises(HTTPException, match="Failed to create provider subscription"):
        await create_source(
            _request(),
            SimpleNamespace(org_id=uuid4(), user=SimpleNamespace(email="admin@example.com")),
            SimpleNamespace(),
            db,
        )

    db.delete.assert_awaited_once_with(added[0])
    assert db.commit.await_count == 2
