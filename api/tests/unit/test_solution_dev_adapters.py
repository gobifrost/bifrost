"""Server-owned local-dev provenance survives request adapter boundaries."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared.event_emission import EventEmissionCaller
from shared.file_access import FileCaller
from shared.sdk_workflow_execution import _server_caller_context
from src.core.principal import UserPrincipal
from src.services.solution_scope import resolve_trustworthy_caller


def _principal() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="developer@example.test",
        organization_id=uuid4(),
        is_superuser=True,
    )


@pytest.mark.asyncio
async def test_local_dev_provenance_survives_adapters_without_application_row() -> None:
    principal = _principal()
    caller_install = uuid4()
    explicit_target = uuid4()
    ctx = SimpleNamespace(
        user=principal,
        db=AsyncMock(),
        org_id=principal.organization_id,
        solution_id=str(explicit_target),
        app_id=str(uuid4()),
        caller_solution_id=None,
        dev_solution_id=str(caller_install),
    )

    file_caller = FileCaller.from_context(ctx)
    event_caller = EventEmissionCaller.from_context(ctx, principal)
    workflow_caller = _server_caller_context(
        principal,
        ctx.solution_id,
        ctx.app_id,
        ctx.caller_solution_id,
        ctx.dev_solution_id,
    )

    for adapter in (file_caller, event_caller, workflow_caller):
        assert adapter.solution_id == str(explicit_target)
        assert adapter.dev_solution_id == str(caller_install)
        # No Application lookup is needed: the authenticated dev context is
        # caller provenance, while the explicit request target stays separate.
        assert await resolve_trustworthy_caller(ctx.db, adapter) == caller_install
    ctx.db.execute.assert_not_awaited()
