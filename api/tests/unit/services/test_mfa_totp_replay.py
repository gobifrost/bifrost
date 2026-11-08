"""TOTP time-step tracking rejects replayed codes within the validity window."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pyotp
import pytest

from src.core.security import encrypt_secret
from src.services import mfa_service as mfa_module
from src.services.mfa_service import MFAService, _totp_counter_for_code

NOW = 1_700_000_000
STEP = int(NOW // 30)


def test_counter_identifies_the_matching_step_and_rejects_non_codes():
    totp = pyotp.TOTP(pyotp.random_base32())

    current = totp.at(STEP * 30)
    previous = totp.at((STEP - 1) * 30)

    assert _totp_counter_for_code(totp, current, 1, now=NOW) == STEP
    assert _totp_counter_for_code(totp, previous, 1, now=NOW) == STEP - 1
    assert _totp_counter_for_code(totp, "not-a-code", 1, now=NOW) is None
    assert _totp_counter_for_code(totp, "", 1, now=NOW) is None
    assert _totp_counter_for_code(totp, None, 1, now=NOW) is None  # type: ignore[arg-type]
    # Non-ASCII digits must not raise inside secrets.compare_digest.
    assert _totp_counter_for_code(totp, "１２３４５６", 1, now=NOW) is None
    assert _totp_counter_for_code(totp, "12345a", 1, now=NOW) is None
    assert _totp_counter_for_code(totp, "12345", 1, now=NOW) is None


@pytest.mark.asyncio
async def test_replaying_an_accepted_code_is_rejected(monkeypatch):
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    monkeypatch.setattr(mfa_module.time, "time", lambda: NOW)
    method = SimpleNamespace(
        encrypted_secret=encrypt_secret(secret),
        last_used_counter=None,
        last_used_at=None,
    )
    service = MFAService(SimpleNamespace(flush=AsyncMock()))
    service._get_active_totp = AsyncMock(return_value=method)
    code = totp.at(STEP * 30)

    assert await service.verify_totp_code(uuid4(), code) is True
    assert method.last_used_counter == STEP

    # Same code again: rejected without moving the counter.
    assert await service.verify_totp_code(uuid4(), code) is False
    assert method.last_used_counter == STEP

    # An older step is also rejected.
    older = totp.at((STEP - 1) * 30)
    assert await service.verify_totp_code(uuid4(), older) is False


@pytest.mark.asyncio
async def test_invalid_code_does_not_advance_the_counter(monkeypatch):
    secret = pyotp.random_base32()
    monkeypatch.setattr(mfa_module.time, "time", lambda: NOW)
    method = SimpleNamespace(
        encrypted_secret=encrypt_secret(secret),
        last_used_counter=None,
        last_used_at=None,
    )
    service = MFAService(SimpleNamespace(flush=AsyncMock()))
    service._get_active_totp = AsyncMock(return_value=method)

    assert await service.verify_totp_code(uuid4(), "not-a-code") is False
    assert method.last_used_counter is None
