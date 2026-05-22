from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.repositories.codex_gateway import (
    CodexGatewayKeyMaterial,
    CodexGatewayRepository,
)


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, *, one=None, values=None):
        self._one = one
        self._values = values or []

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return _Scalars(self._values)


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def repository(mock_session):
    return CodexGatewayRepository(mock_session)


@pytest.mark.asyncio
async def test_create_gateway_key_hashes_secret_and_returns_key_material(
    repository,
    mock_session,
):
    user_id = uuid4()
    project_id = uuid4()

    result = await repository.create_gateway_key(
        user_id=user_id,
        project_id=project_id,
        name="developer workstation",
        allowed_models=["gpt-5.1-codex"],
        daily_limit=100,
    )

    assert isinstance(result, CodexGatewayKeyMaterial)
    assert result.plaintext_key.startswith("bfck_")
    assert result.record.user_id == user_id
    assert result.record.project_id == project_id
    assert result.record.key_hash != result.plaintext_key
    assert result.record.key_hash.startswith("$")
    assert result.record.allowed_models == ["gpt-5.1-codex"]
    assert result.record.daily_limit == 100
    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_lookup_gateway_key_uses_hash_and_ignores_revoked_keys(
    repository,
    mock_session,
):
    plaintext = "bfck_test_plaintext"
    key_hash = repository.hash_gateway_key(plaintext)
    active_record = MagicMock(key_hash=key_hash, revoked_at=None, status="active")
    revoked_record = MagicMock(
        key_hash=key_hash, revoked_at=datetime.now(timezone.utc), status="revoked"
    )
    mock_session.execute.return_value = _Result(values=[revoked_record, active_record])

    result = await repository.get_active_gateway_key_by_plaintext(plaintext)

    assert result is active_record
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_active_upstream_account_for_user_excludes_revoked_accounts(
    repository,
    mock_session,
):
    user_id = uuid4()
    active_account = MagicMock(user_id=user_id, revoked_at=None)
    mock_session.execute.return_value = _Result(one=active_account)

    result = await repository.get_active_upstream_account_for_user(user_id)

    assert result is active_account
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_create_upstream_account_stores_encrypted_tokens(
    repository,
    mock_session,
):
    user_id = uuid4()

    account = await repository.create_upstream_account(
        user_id=user_id,
        upstream_subject="chatgpt-user-123",
        upstream_email="dev@example.test",
        upstream_workspace_id="workspace-midtown",
        access_token="access-token-secret",
        refresh_token="refresh-token-secret",
        scopes=["openid", "profile"],
    )

    assert account.user_id == user_id
    assert account.upstream_subject == "chatgpt-user-123"
    assert account.encrypted_access_token != "access-token-secret"
    assert account.encrypted_refresh_token != "refresh-token-secret"
    assert account.scopes == ["openid", "profile"]
    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_create_request_log_excludes_prompt_and_response_by_default(
    repository,
    mock_session,
):
    user_id = uuid4()
    gateway_key_id = uuid4()
    oauth_account_id = uuid4()

    log = await repository.create_request_log(
        request_id="req_bifrost_123",
        user_id=user_id,
        gateway_key_id=gateway_key_id,
        oauth_account_id=oauth_account_id,
        endpoint="/v1/responses",
        model="gpt-5.1-codex",
        streaming=True,
        status_code=200,
        latency_ms=123,
        policy_decision="allow",
        request_metadata={
            "client_type": "codex-cli",
            "prompt": "do not store this",
            "response": "do not store this either",
        },
    )

    assert log.request_id == "req_bifrost_123"
    assert log.request_metadata == {"client_type": "codex-cli"}
    assert log.captured_prompt is None
    assert log.captured_response is None
    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_create_request_log_can_capture_sensitive_payloads_explicitly(
    repository,
    mock_session,
):
    user_id = uuid4()

    log = await repository.create_request_log(
        request_id="req_bifrost_123",
        user_id=user_id,
        endpoint="/v1/responses",
        model="gpt-5.1-codex",
        status_code=200,
        policy_decision="allow",
        captured_prompt="operator-approved prompt",
        captured_response="operator-approved response",
        capture_sensitive_payloads=True,
    )

    assert log.captured_prompt == "operator-approved prompt"
    assert log.captured_response == "operator-approved response"
