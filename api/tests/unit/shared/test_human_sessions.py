"""Unit coverage for human-session refresh-token parsing and hashing."""

from uuid import UUID

import pytest

from shared import human_sessions
from shared.human_sessions import HumanSessionUnavailable, _jti_hash, _refresh_claim_uuid


def test_refresh_jti_hash_does_not_store_the_raw_jti() -> None:
    jti = "refresh-token-jti"

    digest = _jti_hash(jti)

    assert digest != jti
    assert len(digest) == 64
    assert digest == _jti_hash(jti)


def test_refresh_claim_requires_a_uuid() -> None:
    with pytest.raises(HumanSessionUnavailable, match="reauthentication is required"):
        _refresh_claim_uuid({}, "sid", "reauthentication is required")


def test_refresh_claim_returns_the_claim_uuid() -> None:
    session_id = UUID("5b6087c7-1af5-4b7a-9c7a-93bf3e33dc56")

    assert _refresh_claim_uuid({"sid": str(session_id)}, "sid", "invalid") == session_id


@pytest.mark.asyncio
async def test_legacy_refresh_token_requires_reauthentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def decode_legacy_refresh(_token: str, expected_type: str | None = None) -> dict[str, object]:
        assert expected_type == "refresh"
        return {"sub": "5b6087c7-1af5-4b7a-9c7a-93bf3e33dc56", "jti": "old-jti"}

    monkeypatch.setattr(
        human_sessions,
        "decode_token",
        decode_legacy_refresh,
    )

    with pytest.raises(HumanSessionUnavailable, match="predates durable sessions"):
        await human_sessions.rotate_human_session("legacy-refresh-token")


@pytest.mark.asyncio
async def test_session_scoped_logout_refuses_a_missing_session_id() -> None:
    with pytest.raises(HumanSessionUnavailable, match="durable session is required"):
        await human_sessions.revoke_human_session(
            UUID("5b6087c7-1af5-4b7a-9c7a-93bf3e33dc56"),
            None,
        )
