"""
NEW-2 failing-first proof: MCP list_event_sources / get_event_source did not
consult context.org_id:

- list_event_sources with a caller-supplied FOREIGN org UUID read that org's
  sources.
- get_event_source did get_by_id_with_details with ZERO org scoping — any
  caller read any event source in any org by id.

The fix scopes to context.org_id; a non-bypass caller never reads cross-org
sources. The cascade is the same for every principal (org + global) —
``is_external`` plays no part in scope.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import events as events_tool


def _ctx(*, is_platform_admin=False, is_external=False, org_id=...):
    session = AsyncMock()
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
        is_external=is_external,
        user_email="x@y.z",
        user_name="X",
        session=session,
    )


def _executed_sql(ctx) -> str:
    out = []
    for call in ctx.session.execute.await_args_list:
        stmt = call.args[0]
        try:
            out.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        except Exception:
            out.append(str(stmt))
    return "\n".join(out)


@pytest.mark.asyncio
class TestListEventSourcesScope:
    async def test_non_admin_scoped_to_own_org_plus_global(self):
        # A regular (non-external) org user sees own org + global event sources.
        ctx = _ctx(is_external=False)
        await events_tool.list_event_sources(ctx)
        sql = _executed_sql(ctx)
        assert "event_sources.organization_id =" in sql
        assert "event_sources.organization_id IS NULL" in sql

    async def test_external_gets_normal_cascade(self):
        ctx = _ctx(is_external=True)
        await events_tool.list_event_sources(ctx)
        sql = _executed_sql(ctx)
        assert "event_sources.organization_id =" in sql
        assert "event_sources.organization_id IS NULL" in sql

    async def test_caller_supplied_foreign_org_is_ignored(self):
        ctx = _ctx(is_external=False)
        foreign = str(uuid4())
        await events_tool.list_event_sources(ctx, organization_id=foreign)
        sql = _executed_sql(ctx)
        # The caller's OWN org must scope the query, not the foreign one.
        assert str(ctx.org_id).replace("-", "") in sql.replace("-", "")
        assert foreign.replace("-", "") not in sql.replace("-", "")

    async def test_platform_admin_may_target_org(self):
        ctx = _ctx(is_platform_admin=True)
        target = str(uuid4())
        await events_tool.list_event_sources(ctx, organization_id=target)
        sql = _executed_sql(ctx)
        assert target.replace("-", "") in sql.replace("-", "")


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


def _ctx_returning(source, **kw):
    """A context whose by-id fetch returns the given source; sub-count = 0."""
    ctx = _ctx(**kw)
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none = MagicMock(return_value=source)
    result.scalar = MagicMock(return_value=0)
    ctx.session.execute.return_value = result
    return ctx


def _is_error(tool_result) -> bool:
    # error_result() puts an "error" key in structured_content; success_result
    # does not. This distinguishes denial from a successful read whose JSON
    # happens to contain "error_message".
    sc = getattr(tool_result, "structured_content", None)
    return isinstance(sc, dict) and "error" in sc


@pytest.mark.asyncio
class TestCreateEventSourceScope:
    async def test_external_cannot_create_foreign_org_source(self):
        ctx = _ctx(is_external=True)
        res = await events_tool.create_event_source(
            ctx, name="x", source_type="topic", organization_id=str(uuid4())
        )
        assert _is_error(res)

    async def test_external_with_no_org_cannot_create(self):
        ctx = _ctx(is_external=True, org_id=None)
        res = await events_tool.create_event_source(
            ctx, name="x", source_type="topic"
        )
        assert _is_error(res)

    async def test_non_admin_create_forced_to_own_org(self):
        # organization_id omitted would previously mean GLOBAL; a non-admin must
        # be forced to their OWN org. Assert the persisted EventSource carries
        # the caller's org (captured from session.add).
        org = uuid4()
        ctx = _ctx(is_external=False, org_id=org)
        added = []
        ctx.session.add = added.append
        await events_tool.create_event_source(ctx, name="x", source_type="topic")
        sources = [o for o in added if type(o).__name__ == "EventSource"]
        assert sources, "expected an EventSource to be created"
        assert all(s.organization_id == org for s in sources), (
            "non-admin create must be forced to the caller's own org, not global"
        )


@pytest.mark.asyncio
class TestGetEventSourceScope:
    async def test_non_admin_denied_cross_org_source(self):
        foreign_org = uuid4()
        ctx = _ctx_returning(_source(foreign_org), is_external=False)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert _is_error(res), "non-admin must not read a foreign-org event source"

    async def test_external_allowed_global_source(self):
        # Global sources are shared — an external in an org reads them like
        # any org user (scope is org-keyed, not user-keyed).
        ctx = _ctx_returning(_source(None), is_external=True)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert not _is_error(res)

    async def test_non_admin_allowed_own_org_source(self):
        org = uuid4()
        ctx = _ctx_returning(_source(org), is_external=False, org_id=org)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert not _is_error(res)

    async def test_regular_user_allowed_global_source(self):
        # A normal (non-external) org user MAY read a global event source.
        ctx = _ctx_returning(_source(None), is_external=False)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert not _is_error(res)

    async def test_admin_allowed_any_source(self):
        ctx = _ctx_returning(_source(uuid4()), is_platform_admin=True)
        res = await events_tool.get_event_source(ctx, source_id=str(uuid4()))
        assert not _is_error(res)


# =============================================================================
# OPEN-C — the SUBSCRIPTION tools missed the NEW-2 _source_in_scope gate:
# list/create fetched the source by id with ZERO org scoping (an external
# enumerated/wired onto a foreign-org or global source), and update/delete
# matched (subscription_id, source_id) with no org scope at all (cross-org
# tamper by id). Every subscription tool must gate on the SOURCE's org.
# =============================================================================


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


@pytest.mark.asyncio
class TestListEventSubscriptionsScope:
    async def test_external_allowed_global_source_subscriptions(self):
        ctx = _ctx_source_then(_source(None), is_external=True)
        res = await events_tool.list_event_subscriptions(
            ctx, source_id=str(uuid4())
        )
        assert not _is_error(res)

    async def test_non_admin_denied_cross_org_source_subscriptions(self):
        ctx = _ctx_source_then(_source(uuid4()), is_external=False)
        res = await events_tool.list_event_subscriptions(
            ctx, source_id=str(uuid4())
        )
        assert _is_error(res), (
            "non-admin must not enumerate a foreign-org source's subscriptions"
        )

    async def test_own_org_source_subscriptions_allowed(self):
        org = uuid4()
        ctx = _ctx_source_then(_source(org), is_external=True, org_id=org)
        res = await events_tool.list_event_subscriptions(
            ctx, source_id=str(uuid4())
        )
        assert not _is_error(res)

    async def test_regular_user_allowed_global_source_subscriptions(self):
        ctx = _ctx_source_then(_source(None), is_external=False)
        res = await events_tool.list_event_subscriptions(
            ctx, source_id=str(uuid4())
        )
        assert not _is_error(res)


@pytest.mark.asyncio
class TestCreateEventSubscriptionScope:
    async def test_external_may_wire_onto_global_source(self):
        # Same entitlement as any org user — global sources are shared.
        ctx = _ctx_source_then(_source(None), is_external=True)
        res = await events_tool.create_event_subscription(
            ctx, source_id=str(uuid4()), workflow_id=str(uuid4())
        )
        assert not _is_error(res)

    async def test_non_admin_cannot_wire_onto_foreign_org_source(self):
        ctx = _ctx_source_then(_source(uuid4()), is_external=False)
        res = await events_tool.create_event_subscription(
            ctx, source_id=str(uuid4()), workflow_id=str(uuid4())
        )
        assert _is_error(res), (
            "non-admin must not create a subscription on a foreign-org source"
        )


@pytest.mark.asyncio
class TestUpdateDeleteEventSubscriptionScope:
    async def test_update_denied_for_cross_org_source(self):
        source_id = uuid4()
        ctx = _ctx_source_then(
            _source(uuid4()), then=_subscription(source_id), is_external=False
        )
        res = await events_tool.update_event_subscription(
            ctx,
            source_id=str(source_id),
            subscription_id=str(uuid4()),
            is_active=False,
        )
        assert _is_error(res), (
            "non-admin must not update a subscription on a foreign-org source"
        )

    async def test_delete_allowed_for_external_on_global_source(self):
        source_id = uuid4()
        ctx = _ctx_source_then(
            _source(None), then=_subscription(source_id), is_external=True
        )
        res = await events_tool.delete_event_subscription(
            ctx, source_id=str(source_id), subscription_id=str(uuid4())
        )
        assert not _is_error(res)

    async def test_update_allowed_for_own_org_source(self):
        org = uuid4()
        source_id = uuid4()
        ctx = _ctx_source_then(
            _source(org), then=_subscription(source_id), is_external=True, org_id=org
        )
        res = await events_tool.update_event_subscription(
            ctx,
            source_id=str(source_id),
            subscription_id=str(uuid4()),
            is_active=False,
        )
        assert not _is_error(res)
