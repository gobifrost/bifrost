"""Contracts for the ``user.created`` and ``user.signed_in`` topics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from src.core.constants import SYSTEM_USER_EMAIL, SYSTEM_USER_ID
from src.services.events.builtins import (
    emit_user_created,
    emit_user_signed_in,
    identity_claims_for_event,
)

USER = SimpleNamespace(
    id=UUID("00000000-0000-0000-0000-000000000011"),
    email="alice@example.com",
    name="Alice",
    organization_id=UUID("00000000-0000-0000-0000-000000000002"),
)
EVENT_USER = {
    "id": "00000000-0000-0000-0000-000000000011",
    "email": "alice@example.com",
    "name": "Alice",
    "organization_id": "00000000-0000-0000-0000-000000000002",
}
SELF_ACTOR = {
    "type": "user",
    "id": "00000000-0000-0000-0000-000000000011",
    "email": "alice@example.com",
    "name": "Alice",
}


def test_identity_claims_drop_token_binding_values_and_lift_groups() -> None:
    claims = {
        "iss": "https://login.microsoftonline.com/tenant/v2.0",
        "sub": "subject",
        "tid": "tenant",
        "oid": "object",
        "roles": ["Bifrost.Admin"],
        "groups": ["g1", "g2"],
        "nonce": "n",
        "at_hash": "a",
        "c_hash": "c",
        "s_hash": "s",
        "sid": "session",
        "session_state": "keycloak-session",
        "jti": "token-id",
        "origin_jti": "cognito-token-id",
        "aio": "opaque",
        "uti": "opaque",
        "rh": "opaque",
    }

    identity = identity_claims_for_event(claims)

    assert identity == {
        "claims": {
            "iss": "https://login.microsoftonline.com/tenant/v2.0",
            "sub": "subject",
            "tid": "tenant",
            "oid": "object",
            "roles": ["Bifrost.Admin"],
            "groups": ["g1", "g2"],
        },
        "groups": ["g1", "g2"],
        "groups_overage": False,
    }


@pytest.mark.parametrize(
    "overage_claims",
    [
        {"_claim_names": {"groups": "src1"}, "_claim_sources": {"src1": {"endpoint": "x"}}},
        {"hasgroups": True},
    ],
)
def test_identity_claims_report_entra_groups_overage(overage_claims: dict) -> None:
    identity = identity_claims_for_event({"sub": "subject", **overage_claims})

    assert identity["groups"] is None
    assert identity["groups_overage"] is True


@pytest.mark.asyncio
async def test_sso_sign_in_event_carries_filtered_verified_claims() -> None:
    with patch("src.services.events.builtins._emit", new_callable=AsyncMock) as emit:
        await emit_user_signed_in(
            USER,
            method="sso",
            provider="microsoft",
            id_token_claims={"sub": "subject", "groups": ["g1"], "nonce": "n"},
        )

    topic, body = emit.await_args.args
    assert topic == "user.signed_in"
    assert emit.await_args.kwargs == {
        "organization_id": USER.organization_id,
        "triggered_by": str(USER.id),
    }
    assert body["organization"] == {"id": str(USER.organization_id), "name": None}
    assert body["actor"] == SELF_ACTOR
    assert body["user"] == EVENT_USER
    assert body["sign_in"] == {"method": "sso", "provider": "microsoft"}
    assert body["identity"] == {
        "claims": {"sub": "subject", "groups": ["g1"]},
        "groups": ["g1"],
        "groups_overage": False,
    }


@pytest.mark.asyncio
async def test_password_sign_in_event_has_no_identity() -> None:
    with patch("src.services.events.builtins._emit", new_callable=AsyncMock) as emit:
        await emit_user_signed_in(USER, method="password")

    _topic, body = emit.await_args.args
    assert body["sign_in"] == {"method": "password", "provider": None}
    assert body["identity"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "actor_kwargs", "expected_actor"),
    [
        (
            "admin",
            {
                "actor_user_id": UUID("00000000-0000-0000-0000-000000000009"),
                "actor_email": "admin@example.com",
                "actor_name": "Admin",
            },
            {
                "type": "user",
                "id": "00000000-0000-0000-0000-000000000009",
                "email": "admin@example.com",
                "name": "Admin",
            },
        ),
        ("sso_provisioning", {}, SELF_ACTOR),
        ("registration", {}, SELF_ACTOR),
        (
            "bootstrap",
            {},
            {
                "type": "system",
                "id": SYSTEM_USER_ID,
                "email": SYSTEM_USER_EMAIL,
                "name": "Bifrost",
            },
        ),
    ],
)
async def test_user_created_event_names_method_and_actor(
    method, actor_kwargs, expected_actor
) -> None:
    provider = "google" if method == "sso_provisioning" else None
    with patch("src.services.events.builtins._emit", new_callable=AsyncMock) as emit:
        await emit_user_created(USER, method=method, provider=provider, **actor_kwargs)

    topic, body = emit.await_args.args
    assert topic == "user.created"
    assert emit.await_args.kwargs["organization_id"] == USER.organization_id
    assert body["actor"] == expected_actor
    assert body["user"] == EVENT_USER
    assert body["creation"] == {"method": method, "provider": provider}


@pytest.mark.asyncio
async def test_user_event_emit_failure_never_raises() -> None:
    with patch(
        "src.services.events.emit_event",
        new=AsyncMock(side_effect=RuntimeError("database unavailable")),
    ):
        await emit_user_signed_in(USER, method="passkey")
        await emit_user_created(USER, method="registration")
