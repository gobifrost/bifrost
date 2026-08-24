"""Authorization for administrator-initiated session revocation."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers import auth
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(*, organization_id, capabilities: set[str]) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        name="Operator",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_admin_revoke_requires_target_users_exact_boundary(monkeypatch) -> None:
    selected_org_id = uuid4()
    target = SimpleNamespace(
        id=uuid4(),
        email="target@example.com",
        organization_id=uuid4(),
    )

    class _Users:
        def __init__(self, db) -> None:  # noqa: ANN001
            pass

        async def get_by_id(self, user_id):  # noqa: ANN001, ANN201
            return target

    monkeypatch.setattr(auth, "UserRepository", _Users)
    monkeypatch.setattr(
        auth,
        "revoke_all_user_refresh_tokens",
        pytest.fail,
    )

    with pytest.raises(HTTPException) as exc:
        await auth.admin_revoke_user_sessions(
            auth.AdminRevokeRequest(user_id=str(target.id)),
            _authorization(
                organization_id=selected_org_id,
                capabilities={"organizations.readwrite"},
            ),
            SimpleNamespace(),
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_admin_revoke_uses_capability_and_emits_audit(monkeypatch) -> None:
    organization_id = uuid4()
    target = SimpleNamespace(
        id=uuid4(),
        email="target@example.com",
        organization_id=organization_id,
    )
    audits: list[tuple[str, dict[str, object]]] = []

    class _Users:
        def __init__(self, db) -> None:  # noqa: ANN001
            pass

        async def get_by_id(self, user_id):  # noqa: ANN001, ANN201
            return target

    async def _revoke(user_id: str) -> int:
        assert user_id == str(target.id)
        return 3

    async def _audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audits.append((action, kwargs))

    class _Db:
        committed = False

        async def commit(self) -> None:
            self.committed = True

    monkeypatch.setattr(auth, "UserRepository", _Users)
    monkeypatch.setattr(auth, "revoke_all_user_refresh_tokens", _revoke)
    monkeypatch.setattr(auth, "emit_audit", _audit)

    db = _Db()
    response = await auth.admin_revoke_user_sessions(
        auth.AdminRevokeRequest(user_id=str(target.id)),
        _authorization(
            organization_id=organization_id,
            capabilities={"organizations.readwrite"},
        ),
        db,
    )

    assert response.sessions_revoked == 3
    assert db.committed is True
    assert audits == [
        (
            "auth.sessions.revoke_user",
            {
                "resource_type": "user",
                "resource_id": target.id,
                "details": {
                    "email": target.email,
                    "sessions_revoked": 3,
                },
            },
        )
    ]
