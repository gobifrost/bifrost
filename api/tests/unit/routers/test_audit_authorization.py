"""Authorization for the platform audit log."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers import audit
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *, capabilities: set[str], boundary: AuthorizationBoundary
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="auditor@example.com",
        organization_id=boundary.organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_audit_log_requires_audit_read(monkeypatch) -> None:
    monkeypatch.setattr(audit, "AuditLogRepository", pytest.fail)

    with pytest.raises(HTTPException) as exc:
        await audit.list_audit_logs(
            _authorization(
                capabilities=set(),
                boundary=AuthorizationBoundary.platform(),
            ),
            SimpleNamespace(),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: audit.read"


@pytest.mark.asyncio
async def test_audit_log_requires_explicit_platform_boundary(monkeypatch) -> None:
    monkeypatch.setattr(audit, "AuditLogRepository", pytest.fail)

    with pytest.raises(HTTPException) as exc:
        await audit.list_audit_logs(
            _authorization(
                capabilities={"audit.read"},
                boundary=AuthorizationBoundary.organization(uuid4()),
            ),
            SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == ("Select Global before reviewing the platform Audit Log")
