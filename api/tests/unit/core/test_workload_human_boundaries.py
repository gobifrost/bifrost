"""Federated authority stays machine-only, including in a signed-in browser."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from shared.app_connections import require_app_approver
from shared.browser_impersonation import IMPERSONATION_COOKIE_NAME, BrowserImpersonationError, create_browser_impersonation
from src.core import auth
from src.services.authorization.tokens import FederatedCredentialClaims, WorkloadAccessTokenClaims, _encode_claims, _workload_principal
from tests.helpers.human_tokens import human_claims


def _claims(*, expired: bool = False) -> WorkloadAccessTokenClaims:
    human = human_claims(organization_id=uuid4(), admin=True)
    payload = human.model_dump(exclude={"usr", "sid"})
    if expired:
        payload.update(iat=int(time.time()) - 301, exp=int(time.time()) - 1)
    return WorkloadAccessTokenClaims(
        **payload, usr=human.usr.model_copy(update={"kind": "custom"}),
        fed=FederatedCredentialClaims(id=uuid4(), rev=1),
    )


@pytest.mark.parametrize("expired", [False, True])
def test_browser_marker_cannot_replace_a_workload_bearer_or_socket_token(expired: bool) -> None:
    token = _encode_claims(_claims(expired=expired))
    request = Request({"type": "http", "headers": [(b"cookie", f"{IMPERSONATION_COOKIE_NAME}=marker".encode())]})
    socket = SimpleNamespace(
        query_params={}, headers={"authorization": f"Bearer {token}"},
        cookies={IMPERSONATION_COOKIE_NAME: "marker"},
    )
    assert auth.select_http_auth_token(request, token) == token
    assert auth.select_websocket_auth_token(socket) == token


def test_workload_principal_cannot_manage_human_credentials_even_with_admin_roles() -> None:
    from src.routers import mfa, passkeys

    principal = _workload_principal(_claims())
    assert principal.is_platform_admin
    for require_human in (mfa._refuse_impersonation_enrollment, passkeys._refuse_impersonation_enrollment, require_app_approver):
        with pytest.raises(HTTPException) as refused:
            require_human(principal)
        assert refused.value.status_code == 403


async def test_workload_cannot_be_the_original_browser_operator(monkeypatch) -> None:
    principal = _workload_principal(_claims())
    monkeypatch.setattr(auth, "_get_current_user_optional", AsyncMock(return_value=principal))
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as refused:
        await auth.get_original_human_user(request, None, None)
    assert refused.value.status_code == 401
    with pytest.raises(BrowserImpersonationError, match="original human"):
        await create_browser_impersonation(None, principal, uuid4(), initiator_refresh_jti="not-a-human-session")
