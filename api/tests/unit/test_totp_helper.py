"""The E2E helper schedules fresh proofs without retrying rejected requests."""

from unittest.mock import Mock

import httpx

from tests.helpers import totp


def test_known_consumed_code_waits_before_single_submission(monkeypatch):
    secret = "known-consumed-secret"
    totp.record_accepted_totp_code(secret, "123456")
    codes = iter(["123456", "654321"])
    monkeypatch.setattr(totp, "generate_totp_code", lambda _: next(codes))
    monkeypatch.setattr(totp.time, "time", lambda: 60.0)
    sleep = Mock()
    monkeypatch.setattr(totp.time, "sleep", sleep)
    client = Mock()
    client.post.return_value = httpx.Response(200)

    response = totp.complete_mfa_login(client, "challenge", secret)

    assert response.status_code == 200
    sleep.assert_called_once_with(30.5)
    client.post.assert_called_once_with(
        "/auth/mfa/login", json={"mfa_token": "challenge", "code": "654321"}
    )
    assert totp.last_accepted_totp_code(secret) == "654321"


def test_rejected_fresh_code_is_returned_without_retry_or_sleep(monkeypatch):
    secret = "fresh-rejected-secret"
    monkeypatch.setattr(totp, "generate_totp_code", lambda _: "111111")
    sleep = Mock()
    monkeypatch.setattr(totp.time, "sleep", sleep)
    client = Mock()
    rejected = httpx.Response(401, json={"detail": "Invalid MFA code"})
    client.post.return_value = rejected

    assert totp.complete_mfa_login(client, "challenge", secret) is rejected
    client.post.assert_called_once()
    sleep.assert_not_called()
    assert totp.last_accepted_totp_code(secret) is None
