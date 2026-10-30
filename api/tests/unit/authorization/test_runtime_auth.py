"""Typed runtime credential authentication."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority
from src.services.authorization.runtime_auth import (
    is_legacy_runtime_payload,
    is_runtime_credential_payload,
    load_runtime_principal,
)


class _Db:
    def __init__(self, rows: dict[tuple[type, object], object]) -> None:
        self.rows = rows
        self.load_options: list[tuple[type, object, object]] = []

    async def get(
        self,
        model: type,
        identity: object,
        *,
        options: object = (),
        populate_existing: bool = False,
    ) -> object | None:
        assert populate_existing
        self.load_options.append((model, identity, options))
        return self.rows.get((model, identity))


def test_legacy_runtime_claims_are_runtime_credentials_and_cannot_fall_through() -> None:
    assert is_runtime_credential_payload({"engine_execution_id": str(uuid4())}) is True
    assert is_runtime_credential_payload({"service_id": str(uuid4())}) is True
    assert is_runtime_credential_payload({"sub": str(uuid4()), "email": "person@example.test"}) is False


def test_only_a_runtime_payload_without_ver_is_bridged() -> None:
    legacy = {"sub": str(uuid4()), "credential_kind": "workflow_execution"}
    assert is_legacy_runtime_payload(legacy) is True
    assert is_legacy_runtime_payload({"engine_execution_id": str(uuid4())}) is True
    assert is_legacy_runtime_payload({**legacy, "ver": 1}) is False
    assert is_legacy_runtime_payload({**legacy, "ver": None}) is False
    assert is_legacy_runtime_payload({"sub": str(uuid4()), "email": "person@example.test"}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_admin", [False, True])
async def test_a_legacy_runtime_payload_is_resolved_from_the_database_not_its_claims(
    monkeypatch: pytest.MonkeyPatch, actor_admin: bool
) -> None:
    """The legacy bridge (and in-process callers): authority comes from the
    database, never from the payload. Claims minted by ``issue_access_token``
    are authority only through the validator (``test_validator.py``: one
    statement; a tampered token is refused)."""
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User

    run_user_id = uuid4()
    actor_id = uuid4()
    execution_id = uuid4()
    actor = SimpleNamespace(
        id=actor_id,
        email="actor@example.test",
        name="Actor",
        organization_id=None,
        is_external=True,
        organization=None,
    )
    run_user = SimpleNamespace(id=run_user_id, organization_id=None)
    context = AuthorizationContext(
        user_id=run_user_id,
        home_organization_id=None,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset(),
    )
    authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=execution_id,
        run_user=RunUser(run_user_id, context, None, is_active=True),
        powers=Powers(WorkflowPermissionMode.RESTRICTED, ()),
        acting_user_id=actor_id,
        organization_id=uuid4(),
        workflow_id=uuid4(),
        started_by_user_id=run_user_id,
        root_execution_id=execution_id,
    )
    resolver = AsyncMock(return_value=authority)
    monkeypatch.setattr("src.services.authorization.runtime_auth.load_runtime_authority", resolver)
    actor_roles = ([PLATFORM_ADMIN_ROLE_ID], ["Platform Admin"]) if actor_admin else ([context.base_role_id], ["User"])
    role_loader = AsyncMock(return_value=actor_roles)
    monkeypatch.setattr("src.services.authorization.runtime_auth.get_user_roles", role_loader)
    db = _Db({(User, actor_id): actor, (User, run_user_id): run_user, (Organization, None): None})

    principal = await load_runtime_principal(
        db,  # type: ignore[arg-type]
        {
            "credential_kind": "workflow_execution",
            "sub": str(execution_id),
            "email": "spoofed@example.test",
            "is_superuser": True,
            "engine_execution_id": str(uuid4()),
            "engine_run_user_id": str(uuid4()),
        },
    )

    assert principal is not None
    assert principal.user_id == actor_id
    assert principal.email == "actor@example.test"
    assert principal.is_superuser is actor_admin
    assert principal.is_external is True
    assert principal.runtime_authority.run_user.ctx.is_external is False
    assert principal.role_names == actor_roles[1]
    assert principal.engine_solution_id is None
    assert principal.runtime_authority is authority
    assert principal.engine_execution_id == str(execution_id)
    assert [model for model, _, _ in db.load_options] == [User, User]
    from src.services.authorization.enforce import Caller, allows_permission

    caller = Caller(principal, context)
    assert not caller.is_platform_admin
    assert not allows_permission(caller, "users.readwrite", authority.organization_id)
    role_loader.assert_awaited_once_with(actor_id, db)
    resolver.assert_awaited_once_with(db, "workflow_execution", execution_id, service_id=None)
