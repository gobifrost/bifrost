"""Write-scope enforcement for the event source MCP mutation tools.

REST reserves ALL event-source/webhook/schedule/subscription
administration for platform admins, even within the caller's own org —
every route in ``routers/events.py`` is ``CurrentSuperuser``. ``update_event_source``
and ``delete_event_source`` (and every other mutation in this module) are
therefore platform-admin only: neither org membership nor the broader
``has_scope_bypass`` provider-org allowance is sufficient. This supersedes
the earlier per-org cascade this file used to test.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import events as events_tool


def _ctx(*, is_platform_admin=False, is_provider_org=False, is_external=False, org_id=...):
    from unittest.mock import AsyncMock

    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
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
    src.webhook_source = None
    src.schedule_source = None
    return src


def _ctx_returning(source, **kw):
    ctx = _ctx(**kw)
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none = MagicMock(return_value=source)
    ctx.session.execute.return_value = result
    return ctx


def _is_error(tool_result) -> bool:
    sc = getattr(tool_result, "structured_content", None)
    return isinstance(sc, dict) and "error" in sc


@pytest.mark.asyncio
class TestUpdateEventSourceWriteScope:
    async def test_non_admin_denied_for_own_org_source(self):
        """Own-org membership alone no longer grants write access."""
        org = uuid4()
        ctx = _ctx_returning(_source(org), is_external=False, org_id=org)
        res = await events_tool.update_event_source(ctx, source_id=str(uuid4()), name="renamed")
        assert _is_error(res)

    async def test_non_admin_denied_for_global_source(self):
        ctx = _ctx_returning(_source(None), is_external=False)
        res = await events_tool.update_event_source(ctx, source_id=str(uuid4()), name="renamed")
        assert _is_error(res)

    async def test_provider_org_non_admin_denied(self):
        """The broader has_scope_bypass provider-org allowance does NOT
        apply here — REST's CurrentSuperuser gate is platform-admin only."""
        ctx = _ctx_returning(_source(None), is_provider_org=True)
        res = await events_tool.update_event_source(ctx, source_id=str(uuid4()), name="renamed")
        assert _is_error(res)

    async def test_platform_admin_allowed_for_global_source(self):
        ctx = _ctx_returning(_source(None), is_platform_admin=True)
        res = await events_tool.update_event_source(ctx, source_id=str(uuid4()), name="renamed")
        assert not _is_error(res)

    async def test_platform_admin_allowed_for_own_org_source(self):
        org = uuid4()
        ctx = _ctx_returning(_source(org), is_platform_admin=True, org_id=org)
        res = await events_tool.update_event_source(ctx, source_id=str(uuid4()), name="renamed")
        assert not _is_error(res)


@pytest.mark.asyncio
class TestDeleteEventSourceWriteScope:
    async def test_non_admin_denied_for_own_org_source(self):
        org = uuid4()
        ctx = _ctx_returning(_source(org), is_external=False, org_id=org)
        res = await events_tool.delete_event_source(ctx, source_id=str(uuid4()))
        assert _is_error(res)

    async def test_non_admin_denied_for_global_source(self):
        ctx = _ctx_returning(_source(None), is_external=False)
        res = await events_tool.delete_event_source(ctx, source_id=str(uuid4()))
        assert _is_error(res)

    async def test_platform_admin_allowed_for_own_org_source(self):
        org = uuid4()
        ctx = _ctx_returning(_source(org), is_platform_admin=True, org_id=org)
        res = await events_tool.delete_event_source(ctx, source_id=str(uuid4()))
        assert not _is_error(res)
