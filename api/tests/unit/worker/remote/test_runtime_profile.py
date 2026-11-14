"""Runtime identity is authenticated before canonical worker admission."""
from dataclasses import replace
import json
import time
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from src.worker.remote.host import RemoteControlHost
from src.worker.remote.protocol import AuthenticationError, Hello, ProtocolError, SignedHello, sign_hello, verify_hello


def hello(**changes):
    return Hello(launcher_id=uuid4(), incarnation_id=uuid4(), job_uid=None, pod_uid=None,
                 hostname="runtime-profile-test", form="static", issued_at=int(time.time()),
                 nonce="n" * 32, hello_sequence=1, **changes)


def test_unsigned_profile_is_not_silently_ignored():
    legacy = hello()
    raw = sign_hello("test-secret", legacy).to_dict()
    raw["runtime_profile"] = "pyodide-v1"
    with pytest.raises(AuthenticationError):
        verify_hello("test-secret", SignedHello.from_dict(raw), now=legacy.issued_at)


def test_explicit_profile_is_bound_to_signature():
    declared = hello(runtime_profile="linux-process-v1")
    signed = sign_hello("test-secret", declared)
    assert verify_hello("test-secret", signed, now=declared.issued_at) == declared
    for altered in (replace(signed, runtime_profile="pyodide-v1"), replace(signed, runtime_profile=None)):
        with pytest.raises(AuthenticationError):
            verify_hello("test-secret", altered, now=declared.issued_at)


def test_legacy_linux_hello_omits_profile_and_round_trips():
    legacy = hello()
    signed = sign_hello("test-secret", legacy)
    assert "runtime_profile" not in signed.to_dict()
    assert "runtime_profile" not in json.loads(legacy.canonical_bytes())
    assert verify_hello("test-secret", SignedHello.from_dict(signed.to_dict()), now=legacy.issued_at) == legacy


@pytest.mark.parametrize("profile", ["", "x" * 65, "bad/profile", [], 1, True])
def test_malformed_profiles_are_rejected(profile):
    with pytest.raises(ProtocolError):
        hello(runtime_profile=profile)


@pytest.mark.asyncio
@pytest.mark.parametrize("unsigned", [False, True], ids=["signed-unsupported", "unsigned-shadow"])
async def test_unsupported_profile_cannot_acquire_owner_or_admit_worker(unsigned):
    declared = hello(**({} if unsigned else {"runtime_profile": "pyodide-v1"}))
    frame = {"event": "hello", "hello": sign_hello("test-secret", declared).to_dict(),
             "sample": {"current": 1, "limit": 100}, "requirements": {}}
    if unsigned:
        frame["runtime_profile"] = "pyodide-v1"
    socket = Mock(accept=AsyncMock(), receive_text=AsyncMock(return_value=json.dumps(frame)), close=AsyncMock())
    with (
        patch("src.worker.remote.host.get_settings", return_value=Mock(secret_key="test-secret")),
        patch("src.worker.remote.host.acquire_remote_owner", new_callable=AsyncMock) as acquire,
        patch("src.worker.remote.host.admit_worker", new_callable=AsyncMock) as admit,
        patch("src.worker.remote.host.admit_external_worker", new_callable=AsyncMock) as external,
        patch("src.worker.remote.host.get_liveness_db_context") as database,
    ):
        with pytest.raises(ProtocolError):
            await RemoteControlHost().connect(socket)
    socket.close.assert_awaited_once_with(code=1008)
    acquire.assert_not_awaited()
    admit.assert_not_awaited()
    external.assert_not_awaited()
    database.assert_not_called()
