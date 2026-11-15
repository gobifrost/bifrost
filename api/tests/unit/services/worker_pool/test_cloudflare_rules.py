"""Cloudflare capacity-start rules mutate only the durable connection metadata."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import CloudflareCapacityRules, CloudflareCapacityStatus
from src.core.principal import UserPrincipal
from src.models.orm.platform_jobs import PlatformJob
from src.routers import worker_capacity as router
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import entry_for_operation
from src.services.authorization.evaluator import GLOBAL, decide
from src.services.worker_pool.cloudflare_setup import (
    describe_cloudflare_capacity,
    update_cloudflare_rules,
)
from src.services.worker_pool.connection import (
    CapacityConnectionError,
    activate_cloudflare_connection,
    load_cloudflare_connection,
    stage_cloudflare_api_key,
)


async def _active_connection(db: AsyncSession):
    connection = await stage_cloudflare_api_key(
        db, "cloudflare-api-key-for-rules-test", updated_by="admin@example.test",
    )
    assert await activate_cloudflare_connection(
        db, connection.generation, endpoint="https://capacity.example.test",
        account_id="a" * 32, deployment={"worker_version": "test"},
    )
    return await load_cloudflare_connection(db)


async def test_rules_update_preserves_encrypted_connection_identity_and_starts_no_job(
    db_session: AsyncSession,
) -> None:
    before = await _active_connection(db_session)
    assert before is not None
    before_jobs = await db_session.scalar(select(func.count()).select_from(PlatformJob))

    result = await update_cloudflare_rules(
        db_session, CloudflareCapacityRules(workflows=False),
    )

    after = await load_cloudflare_connection(db_session)
    after_jobs = await db_session.scalar(select(func.count()).select_from(PlatformJob))
    assert after is not None
    assert (after.generation, after.api_key, after.capacity_token) == (
        before.generation, before.api_key, before.capacity_token,
    )
    assert after.metadata["endpoint"] == "https://capacity.example.test"
    assert after_jobs == before_jobs
    assert result.rules == CloudflareCapacityRules(workflows=False)


async def test_rules_patch_retains_omitted_fields_and_default_status_has_no_kubernetes_preference(
    db_session: AsyncSession,
) -> None:
    assert (await describe_cloudflare_capacity(db_session)).rules == CloudflareCapacityRules()
    await _active_connection(db_session)
    await update_cloudflare_rules(
        db_session, CloudflareCapacityRules(workflows=False, agents=False),
    )

    result = await update_cloudflare_rules(
        db_session, CloudflareCapacityRules(app_builds=False),
    )

    assert result.rules == CloudflareCapacityRules(
        workflows=False, agents=False, app_builds=False, prefer_kubernetes=False,
    )


async def test_unconfigured_connection_rejects_rules_update(db_session: AsyncSession) -> None:
    with pytest.raises(CapacityConnectionError, match="Connect Cloudflare"):
        await update_cloudflare_rules(db_session, CloudflareCapacityRules(workflows=False))


def test_rules_route_requires_global_platform_readwrite_permission() -> None:
    user = UserPrincipal(user_id=uuid4(), email="operator@example.test", organization_id=None)
    context = AuthorizationContext(
        user_id=user.user_id, home_organization_id=None, base_role_id=uuid4(),
        base_permissions=frozenset(), is_external=False,
    )
    entry = entry_for_operation("PUT /api/admin/worker-capacity/cloudflare/rules")

    assert entry.permission == "platform.readwrite"
    assert not decide(context, entry, GLOBAL).allowed


@pytest.mark.asyncio
async def test_rules_route_authorizes_then_commits_and_wakes_existing_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = UserPrincipal(user_id=uuid4(), email="operator@example.test", organization_id=None)
    authorize = AsyncMock()
    update = AsyncMock(return_value=CloudflareCapacityStatus(
        configured=True, status="ready", rules=CloudflareCapacityRules(agents=False),
    ))
    wake = AsyncMock()
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(router, "authorize_operation", authorize)
    monkeypatch.setattr(router, "update_cloudflare_rules", update)
    monkeypatch.setattr("src.services.work_dispatch.wake_pool_workers", wake)

    result = await router.put_cloudflare_capacity_rules(
        CloudflareCapacityRules(agents=False), db, user,
    )

    authorize.assert_awaited_once_with(
        db, user, "PUT /api/admin/worker-capacity/cloudflare/rules", GLOBAL,
    )
    update.assert_awaited_once_with(db, CloudflareCapacityRules(agents=False))
    db.commit.assert_awaited_once()
    from src.services.work_dispatch import WAKE_PLATFORM_JOB

    wake.assert_awaited_once_with(WAKE_PLATFORM_JOB)
    assert result.rules.agents is False
