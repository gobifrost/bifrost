"""Elevated branches note the named permission that replaces their flag, on
the branch that requires authority and nowhere else."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from shared import access_checks
from shared.table_document_writes import TableWriteForbidden, resolve_attribution
from src.core.principal import UserPrincipal
from src.services.authorization.enforce import require_permission, require_unprotected
from src.services.mcp_server.tools.code_editor import _check_read_scope, _check_write_scope
from tests.helpers.authorization import admin_caller

CONTOSO = UUID("00000000-0000-0000-0000-00000000c001")
FABRIKAM = UUID("00000000-0000-0000-0000-00000000f001")


@pytest.fixture
def notes():
    token = access_checks.collect_person(uuid4())
    try:
        collector = access_checks.current()
        assert collector is not None
        yield collector.notes
    finally:
        access_checks.stop_collecting(token)


def _powers(notes) -> list[tuple[str, object]]:
    return [(note.facts["permission"], note.target) for note in notes]


def _person(*, admin: bool = False, provider: bool = False, org: UUID = CONTOSO) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(), email="p@contoso.example", organization_id=org, is_superuser=admin, is_provider_org=provider
    )




def test_privileged_assignment_notes_privileged_access(notes) -> None:
    require_permission(admin_caller(), "privilegedaccess.readwrite", None)

    assert _powers(notes) == [("privilegedaccess.readwrite", None)]


def test_a_privileged_target_notes_privileged_access(notes) -> None:
    caller = admin_caller()

    require_unprotected(caller, target_is_privileged=False, target_organization_id=None)
    require_unprotected(caller, target_is_privileged=True, target_organization_id=None)

    assert _powers(notes) == [("privilegedaccess.readwrite", None)]


@pytest.mark.asyncio
async def test_an_attribution_override_notes_table_attribution(notes, monkeypatch) -> None:
    from unittest.mock import AsyncMock
    from src.services.authorization.enforce import Caller
    from src.services.authorization.context import AuthorizationContext
    from shared.builtin_roles import USER_ROLE_ID

    admin = admin_caller()
    monkeypatch.setattr("src.services.authorization.enforce.load_caller", AsyncMock(return_value=admin))
    assert await resolve_attribution(admin.principal, None, None, db=None, organization_id=CONTOSO) == (
        str(admin.principal.user_id), str(admin.principal.user_id),
    )
    await resolve_attribution(admin.principal, "someone", None, db=None, organization_id=CONTOSO)
    ordinary = _person()
    context = AuthorizationContext(
        user_id=ordinary.user_id, home_organization_id=CONTOSO, base_role_id=USER_ROLE_ID,
        base_permissions=frozenset(), is_external=False,
    )
    monkeypatch.setattr("src.services.authorization.enforce.load_caller", AsyncMock(return_value=Caller(ordinary, context)))
    with pytest.raises(TableWriteForbidden):
        await resolve_attribution(ordinary, "someone", None, db=None, organization_id=CONTOSO)
    assert _powers(notes) == [("tableattribution.readwrite", CONTOSO)] * 2
    assert all(note.facts["enforced"] for note in notes)


@pytest.mark.asyncio
async def test_repository_scope_records_actual_named_permission(notes, monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from src.services.mcp_server.server import MCPContext
    caller = admin_caller()

    @asynccontextmanager
    async def tool_db(_context):
        yield None

    monkeypatch.setattr("src.services.mcp_server.tools.code_editor.get_tool_db", tool_db)
    monkeypatch.setattr("src.services.mcp_server.authorization.load_mcp_caller", AsyncMock(return_value=caller))
    context = MCPContext(user_id=caller.principal.user_id)
    assert await _check_read_scope(context) is None
    assert await _check_write_scope(context, "workflows/contoso.py") is None
    assert _powers(notes) == [("repository.read", None), ("repository.readwrite", None)]
    assert all(note.facts["enforced"] for note in notes)




def test_an_owned_object_carries_who_acted() -> None:
    token = access_checks.collect_person(uuid4())
    try:
        workspace, actor = uuid4(), uuid4()
        access_checks.note_power(
            "artifacts.readwrite.all", access_checks.Owned("artifact_workspace", workspace, actor), subject="w"
        )
        collector = access_checks.current()
        assert collector is not None
        [note] = collector.notes
        assert note.target is None
        assert note.facts == {
            "permission": "artifacts.readwrite.all",
            "subject": "w",
            "owned": "artifact_workspace",
            "object_id": str(workspace),
            "actor": str(actor),
        }
    finally:
        access_checks.stop_collecting(token)
