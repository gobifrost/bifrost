"""
Event source / subscription MCP tools are platform-admin only.

Historically this file proved a per-org cascade (NEW-2): a non-bypass
caller was scoped to their own org + global, and an external principal
got the same cascade as any org user. That model is superseded — REST
reserves ALL event-source/webhook/schedule/subscription administration
for platform admins (every route in ``routers/events.py`` is
``CurrentSuperuser``), even within the caller's own org. So every tool in
this module now denies any non-platform-admin caller outright, regardless
of org, external status, or the broader ``has_scope_bypass`` provider-org
allowance. This file is rewritten to prove that law instead of the old
cascade.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import events as events_tool


def _ctx(*, is_platform_admin=False, is_provider_org=False, is_external=False, org_id=...):
    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.unique.return_value.scalars.return_value.all.return_value = []
    result.unique.return_value.scalar_one_or_none = MagicMock(return_value=None)
    result.scalar_one_or_none = MagicMock(return_value=None)
    result.scalar = MagicMock(return_value=0)
    session.execute.return_value = result
    return SimpleNamespace(
        user_id=uuid4(),
        org_id=uuid4() if org_id is ... else org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        is_external=is_external,
        user_email="x@y.z",
        user_name="X",
        session=session,
    )


def _source(org_id):
    src = MagicMock()
    src.id = uuid4()
    src.name = "src"
    src.organization_id = org_id
    src.source_type = MagicMock(value="topic")
    src.is_active = True
    src.error_message = None
    src.created_by = "x"
    src.created_at = None
    src.webhook_source = None
    src.schedule_source = None
    return src


def _subscription(source_id):
    sub = MagicMock()
    sub.id = uuid4()
    sub.event_source_id = source_id
    sub.workflow_id = uuid4()
    sub.workflow = None
    sub.event_type = None
    sub.input_mapping = None
    sub.is_active = True
    return sub


def _ctx_returning(source, **kw):
    """A context whose by-id fetch returns the given source; sub-count = 0."""
    ctx = _ctx(**kw)
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none = MagicMock(return_value=source)
    result.scalar = MagicMock(return_value=0)
    ctx.session.execute.return_value = result
    return ctx


def _ctx_source_then(source, then=None, **kw):
    """Context whose FIRST by-id fetch returns ``source``; later statements
    return ``then`` (a scalar_one_or_none-able object) or empty shapes."""
    ctx = _ctx(**kw)
    first = MagicMock()
    first.unique.return_value.scalar_one_or_none = MagicMock(return_value=source)
    first.scalar_one_or_none = MagicMock(return_value=source)
    first.scalars.return_value.all.return_value = []
    rest = MagicMock()
    rest.unique.return_value.scalar_one_or_none = MagicMock(return_value=then)
    rest.scalar_one_or_none = MagicMock(return_value=then)
    rest.scalars.return_value.all.return_value = []
    rest.scalar = MagicMock(return_value=0)
    ctx.session.execute = AsyncMock(side_effect=[first] + [rest] * 8)
    return ctx


def _is_error(tool_result) -> bool:
    sc = getattr(tool_result, "structured_content", None)
    return isinstance(sc, dict) and "error" in sc


@pytest.mark.asyncio
class TestListEventSourcesAdminOnly:
    async def test_non_admin_denied(self):
        ctx = _ctx(is_external=False)
        res = await events_tool.list_event_sources(ctx)
        assert _is_error(res)

    async def test_external_denied(self):
        ctx = _ctx(is_external=True)
        res = await events_tool.list_event_sources(ctx)
        assert _is_error(res)

    async def test_provider_org_non_admin_denied(self):
        """The broader has_scope_bypass provider-org allowance does NOT
        apply here — REST's CurrentSuperuser gate is platform-admin only."""
        ctx = _ctx(is_provider_org=True)
        res = await events_tool.list_event_sources(ctx)
        assert _is_error(res)

    async def test_platform_admin_allowed(self):
        ctx = _ctx(is_platform_admin=True)
        res = await events_tool.list_event_sources(ctx)
        assert not _is_error(res)


@pytest.mark.asyncio
class TestCreateEventSourceAdminOnly:
    async def test_non_admin_cannot_create(self):
        ctx = _ctx(is_external=False)
        res = await events_tool.create_event_source(
            ctx, name="x", source_type="topic"
        )
        assert _is_error(res)

    async def test_external_cannot_create(self):
        ctx = _ctx(is_external=True)
        res = await events_tool.create_event_source(
            ctx, name="x", source_type="topic", organization_id=str(uuid4())
        )
        assert _is_error(res)

    async def test_platform_admin_can_create(self):
        ctx = _ctx(is_platform_admin=True)
        added = []
        ctx.session.add = added.append
        res = await events_tool.create_event_source(ctx, name="x", source_type="topic")
        assert not _is_error(res)
        sources = [o for o in added if type(o).__name__ == "EventSource"]
        assert sources, "expected an EventSource to be created"


@pytest.mark.asyncio
class TestGetEventSourceAdminOnly:
    async def test_non_admin_denied_even_for_own_org_source(self):
        org = uuid4()
        ctx = _ctx_returning(_source(org), is_external=False, org_id=org)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert _is_error(res)

    async def test_non_admin_denied_for_global_source(self):
        ctx = _ctx_returning(_source(None), is_external=False)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert _is_error(res)

    async def test_external_denied_for_global_source(self):
        ctx = _ctx_returning(_source(None), is_external=True)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert _is_error(res)

    async def test_admin_allowed_any_source(self):
        ctx = _ctx_returning(_source(uuid4()), is_platform_admin=True)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert not _is_error(res)


@pytest.mark.asyncio
class TestListEventSubscriptionsAdminOnly:
    async def test_non_admin_denied_even_for_global_source(self):
        ctx = _ctx_source_then(_source(None), is_external=False)
        res = await events_tool.list_event_subscriptions(ctx, source_id=str(uuid4()))
        assert _is_error(res)

    async def test_external_denied_even_for_own_org_source(self):
        org = uuid4()
        ctx = _ctx_source_then(_source(org), is_external=True, org_id=org)
        res = await events_tool.list_event_subscriptions(ctx, source_id=str(uuid4()))
        assert _is_error(res)

    async def test_admin_allowed(self):
        ctx = _ctx_source_then(_source(uuid4()), is_platform_admin=True)
        res = await events_tool.list_event_subscriptions(ctx, source_id=str(uuid4()))
        assert not _is_error(res)


@pytest.mark.asyncio
class TestCreateEventSubscriptionAdminOnly:
    async def test_non_admin_denied_even_for_global_source(self):
        ctx = _ctx_source_then(_source(None), is_external=False)
        res = await events_tool.create_event_subscription(
            ctx, source_id=str(uuid4()), workflow_id=str(uuid4())
        )
        assert _is_error(res)

    async def test_external_denied(self):
        ctx = _ctx_source_then(_source(None), is_external=True)
        res = await events_tool.create_event_subscription(
            ctx, source_id=str(uuid4()), workflow_id=str(uuid4())
        )
        assert _is_error(res)


@pytest.mark.asyncio
class TestUpdateDeleteEventSubscriptionAdminOnly:
    async def test_update_denied_for_non_admin_even_for_own_org_source(self):
        org = uuid4()
        source_id = uuid4()
        ctx = _ctx_source_then(
            _source(org), then=_subscription(source_id), is_external=False, org_id=org
        )
        res = await events_tool.update_event_subscription(
            ctx,
            source_id=str(source_id),
            subscription_id=str(uuid4()),
            is_active=False,
        )
        assert _is_error(res)

    async def test_delete_denied_for_external_on_global_source(self):
        source_id = uuid4()
        ctx = _ctx_source_then(
            _source(None), then=_subscription(source_id), is_external=True
        )
        res = await events_tool.delete_event_subscription(
            ctx, source_id=str(source_id), subscription_id=str(uuid4())
        )
        assert _is_error(res)

    async def test_update_allowed_for_admin(self):
        org = uuid4()
        source_id = uuid4()
        ctx = _ctx_source_then(
            _source(org), then=_subscription(source_id), is_platform_admin=True
        )
        res = await events_tool.update_event_subscription(
            ctx,
            source_id=str(source_id),
            subscription_id=str(uuid4()),
            is_active=False,
        )
        assert not _is_error(res)
