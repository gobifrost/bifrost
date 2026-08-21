from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers import users
from src.services.operation_catalog import get_operation


class _Authorization:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[str] = []
        self.effective_actor = SimpleNamespace(user_id=uuid4())

    def require_operation(self, operation_id: str) -> None:
        self.calls.append(operation_id)
        if not self.allowed:
            raise HTTPException(status_code=403, detail="Missing capability")


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_regenerate_invite_uses_exact_boundary_and_audit(monkeypatch) -> None:
    user_id = uuid4()
    organization_id = uuid4()
    target = SimpleNamespace(
        id=user_id,
        organization_id=organization_id,
        is_registered=False,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(target)))
    authorization = _Authorization()
    require_boundary = Mock()
    create_or_replace = AsyncMock(
        return_value=(
            "opaque-token",
            SimpleNamespace(expires_at=datetime.now(timezone.utc)),
        )
    )
    audit = AsyncMock()
    monkeypatch.setattr(users, "require_exact_user_boundary", require_boundary)
    monkeypatch.setattr(
        users,
        "UserInviteService",
        lambda _db: SimpleNamespace(create_or_replace=create_or_replace),
    )
    monkeypatch.setattr(
        users,
        "get_settings",
        lambda: SimpleNamespace(public_url="https://bifrost.example"),
    )
    monkeypatch.setattr(users, "emit_audit", audit)

    response = await users.regenerate_invite(user_id, authorization, db)

    assert authorization.calls == ["users.invites.regenerate"]
    require_boundary.assert_called_once_with(
        authorization=authorization,
        organization_id=organization_id,
    )
    create_or_replace.assert_awaited_once_with(
        user_id=user_id,
        created_by=authorization.effective_actor.user_id,
    )
    audit.assert_awaited_once_with(
        db,
        "user.invite_regenerate",
        resource_type="user",
        resource_id=user_id,
    )
    assert response.registration_url.endswith("token=opaque-token")


@pytest.mark.asyncio
async def test_invite_mutation_fails_before_storage_without_capability() -> None:
    authorization = _Authorization(allowed=False)
    db = SimpleNamespace(execute=pytest.fail)

    with pytest.raises(HTTPException, match="Missing capability"):
        await users.regenerate_invite(uuid4(), authorization, db)

    assert authorization.calls == ["users.invites.regenerate"]


def test_invite_operations_are_catalogued() -> None:
    expected = {
        "users.invites.resend": ("POST", "/api/users/{user_id}/invite/resend"),
        "users.invites.send": ("POST", "/api/users/{user_id}/invite/send"),
        "users.invites.regenerate": (
            "POST",
            "/api/users/{user_id}/invite/regenerate",
        ),
        "users.invites.revoke": ("DELETE", "/api/users/{user_id}/invite"),
    }
    for operation_id, rest in expected.items():
        operation = get_operation(operation_id)
        assert operation.rest is not None
        assert (operation.rest.method, operation.rest.path) == rest
        assert operation.action_scopes == ("organizations.readwrite",)
