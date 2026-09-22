"""Service tokens are org-scoped, non-superuser, short-lived engine kin.

See docs/plans/2026-09-20-services-credential-design.md (D1).
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.core.constants import SYSTEM_USER_ID
from src.core.security import (
    SERVICE_TOKEN_LIFETIME_SECONDS,
    decode_token,
    mint_service_token,
)


def _mint(**overrides):
    args = {
        "service_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "attempt_id": "11111111-1111-4111-8111-111111111111",
        "organization_id": "22222222-2222-4222-8222-222222222222",
        "solution_id": None,
        "global_repo_access": False,
    }
    args.update(overrides)
    return mint_service_token(**args)


def test_service_token_is_access_typed_with_engine_claims():
    token, _ = _mint()
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["sub"] == SYSTEM_USER_ID
    assert payload["is_superuser"] is False
    assert payload["org_id"] == "22222222-2222-4222-8222-222222222222"
    assert payload["engine_execution_id"] == "11111111-1111-4111-8111-111111111111"
    assert payload["service_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert payload["service_attempt_id"] == "11111111-1111-4111-8111-111111111111"
    assert "email" in payload


def test_service_token_lifetime_is_short_and_renewable():
    _, expires_at = _mint()
    remaining = datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)
    assert timedelta(seconds=SERVICE_TOKEN_LIFETIME_SECONDS - 10) < remaining
    assert remaining <= timedelta(seconds=SERVICE_TOKEN_LIFETIME_SECONDS)


def test_service_token_carries_signed_solution_scope():
    token, _ = _mint(
        solution_id="33333333-3333-4333-8333-333333333333",
        global_repo_access=True,
    )
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["engine_solution_id"] == "33333333-3333-4333-8333-333333333333"
    assert payload["engine_global_repo_access"] is True


def test_service_token_rejects_missing_org():
    with pytest.raises(ValueError, match="organization_id"):
        _mint(organization_id="")
