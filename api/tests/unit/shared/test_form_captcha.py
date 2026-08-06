from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import altcha
import pytest

from shared.form_captcha import (
    FormCaptchaError,
    create_form_captcha_challenge,
    derive_form_captcha_secret,
    redeem_form_captcha_solution,
    verify_form_captcha_solution,
)


def _solve(challenge_dict: dict) -> str:
    challenge = altcha.Challenge.from_dict(challenge_dict)
    solution = altcha.solve_challenge(challenge)
    assert solution is not None
    return altcha.Payload(challenge, solution).to_base64()


def test_secret_derivation_is_deterministic_and_domain_separated():
    first = derive_form_captcha_secret("a" * 32)

    assert first == derive_form_captcha_secret("a" * 32)
    assert first != derive_form_captcha_secret("b" * 32)
    assert first != b"a" * 32


def test_solution_is_bound_to_exact_form_and_session():
    challenge = create_form_captcha_challenge(
        master_secret="a" * 32,
        form_id="form-1",
        session_id="session-1",
        session_expires_at=None,
    )
    payload = _solve(challenge)

    verified = verify_form_captcha_solution(
        payload=payload,
        master_secret="a" * 32,
        form_id="form-1",
        session_id="session-1",
    )
    assert verified.challenge_id

    for form_id, session_id in (
        ("form-2", "session-1"),
        ("form-1", "session-2"),
    ):
        with pytest.raises(FormCaptchaError, match="invalid or expired"):
            verify_form_captcha_solution(
                payload=payload,
                master_secret="a" * 32,
                form_id=form_id,
                session_id=session_id,
            )


def test_solution_rejects_missing_tampered_and_wrong_secret():
    challenge = create_form_captcha_challenge(
        master_secret="a" * 32,
        form_id="form-1",
        session_id="session-1",
        session_expires_at=None,
    )
    payload = _solve(challenge)

    with pytest.raises(FormCaptchaError, match="required"):
        verify_form_captcha_solution(
            payload=None,
            master_secret="a" * 32,
            form_id="form-1",
            session_id="session-1",
        )
    for invalid_payload, secret in (("not-base64", "a" * 32), (payload, "b" * 32)):
        with pytest.raises(FormCaptchaError, match="invalid or expired"):
            verify_form_captcha_solution(
                payload=invalid_payload,
                master_secret=secret,
                form_id="form-1",
                session_id="session-1",
            )


def test_solution_rejects_an_expired_challenge():
    challenge = create_form_captcha_challenge(
        master_secret="a" * 32,
        form_id="form-1",
        session_id="session-1",
        session_expires_at=None,
        now=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    payload = _solve(challenge)

    with pytest.raises(FormCaptchaError, match="invalid or expired"):
        verify_form_captcha_solution(
            payload=payload,
            master_secret="a" * 32,
            form_id="form-1",
            session_id="session-1",
        )


def test_challenge_expiry_does_not_outlive_the_form_session():
    now = datetime.now(timezone.utc)
    session_expires_at = int((now + timedelta(seconds=30)).timestamp())

    challenge = create_form_captcha_challenge(
        master_secret="a" * 32,
        form_id="form-1",
        session_id="session-1",
        session_expires_at=session_expires_at,
        now=now,
    )

    assert challenge["parameters"]["expiresAt"] == session_expires_at


@pytest.mark.asyncio
async def test_solution_can_only_be_redeemed_once(monkeypatch):
    challenge = create_form_captcha_challenge(
        master_secret="a" * 32,
        form_id="form-1",
        session_id="session-1",
        session_expires_at=None,
    )
    payload = _solve(challenge)
    calls: list[str] = []

    class FakeRedis:
        async def set(self, key, value, *, ex, nx):
            assert value == "1"
            assert ex > 0
            assert nx is True
            calls.append(key)
            return len(calls) == 1

    @asynccontextmanager
    async def fake_redis():
        yield FakeRedis()

    monkeypatch.setattr("src.core.cache.redis_client.get_redis", fake_redis)

    await redeem_form_captcha_solution(
        payload=payload,
        master_secret="a" * 32,
        form_id="form-1",
        session_id="session-1",
    )
    with pytest.raises(FormCaptchaError, match="already been used"):
        await redeem_form_captcha_solution(
            payload=payload,
            master_secret="a" * 32,
            form_id="form-1",
            session_id="session-1",
        )
    assert calls[0].startswith("bifrost:form:captcha:session-1:")
