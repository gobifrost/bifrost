"""Typed durable service-attempt token minting."""

from datetime import datetime, timedelta, timezone

from src.core.security import SERVICE_TOKEN_LIFETIME_SECONDS, decode_token, mint_service_token


def _mint():
    return mint_service_token(
        service_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        attempt_id="11111111-1111-4111-8111-111111111111",
    )


def test_service_token_identifies_only_its_attempt_and_service():
    token, _ = _mint()

    payload = decode_token(token, expected_type="access")

    assert payload is not None
    assert payload["sub"] == "11111111-1111-4111-8111-111111111111"
    assert payload["credential_kind"] == "service_attempt"
    assert payload["service_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    for claim in ("email", "is_superuser", "org_id", "engine_execution_id", "service_attempt_id"):
        assert claim not in payload


def test_service_token_lifetime_is_short_and_renewable():
    _, expires_at = _mint()
    remaining = datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)

    assert timedelta(seconds=SERVICE_TOKEN_LIFETIME_SECONDS - 10) < remaining
    assert remaining <= timedelta(seconds=SERVICE_TOKEN_LIFETIME_SECONDS)
