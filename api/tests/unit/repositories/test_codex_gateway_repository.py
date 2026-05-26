from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.security import decrypt_secret
from src.repositories.codex_gateway import (
    CodexGatewayKeyLimitError,
    CodexGatewayKeyMaterial,
    CodexGatewayRepository,
    MAX_ACTIVE_GATEWAY_KEYS_PER_USER,
)

VALID_GATEWAY_KEY = f"bfck_{'a' * 43}"


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

    def scalar_one(self):
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
    mock_session.execute.side_effect = [_Result(one=user_id), _Result(one=0)]

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
    assert result.record.key_hash.startswith("sha256:")
    assert result.record.allowed_models == ["gpt-5.1-codex"]
    assert result.record.daily_limit == 100
    assert mock_session.execute.await_count == 2
    lock_statement = mock_session.execute.await_args_list[0].args[0]
    compiled_lock = str(lock_statement.compile(compile_kwargs={"literal_binds": False}))
    assert "FOR UPDATE" in compiled_lock
    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_create_gateway_key_enforces_active_key_quota(
    repository,
    mock_session,
):
    user_id = uuid4()
    mock_session.execute.side_effect = [
        _Result(one=user_id),
        _Result(one=MAX_ACTIVE_GATEWAY_KEYS_PER_USER),
    ]

    with pytest.raises(CodexGatewayKeyLimitError):
        await repository.create_gateway_key(
            user_id=user_id,
            project_id=None,
            name="extra key",
        )

    assert mock_session.execute.await_count == 2
    mock_session.add.assert_not_called()
    mock_session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_create_gateway_key_fails_for_unknown_user_before_insert(
    repository,
    mock_session,
):
    mock_session.execute.return_value = _Result(one=None)

    with pytest.raises(ValueError):
        await repository.create_gateway_key(
            user_id=uuid4(),
            project_id=None,
            name="orphan key",
        )

    mock_session.execute.assert_awaited_once()
    mock_session.add.assert_not_called()
    mock_session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_gateway_key_uses_indexed_hash(
    repository,
    mock_session,
):
    plaintext = VALID_GATEWAY_KEY
    key_hash = repository.hash_gateway_key(plaintext)
    active_record = MagicMock(key_hash=key_hash, revoked_at=None, status="active")
    mock_session.execute.return_value = _Result(one=active_record)

    result = await repository.get_active_gateway_key_by_plaintext(plaintext)

    assert result is active_record
    mock_session.execute.assert_called_once()
    statement = mock_session.execute.call_args.args[0]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": False}))
    assert "codex_gateway_keys.key_hash" in compiled


@pytest.mark.asyncio
async def test_lookup_gateway_key_rejects_legacy_bcrypt_hash_without_scan(
    repository,
    mock_session,
):
    legacy_record = MagicMock(
        key_hash="$2b$12$legacybcryptvalue",
        revoked_at=None,
        status="active",
    )
    mock_session.execute.return_value = _Result(one=legacy_record)

    result = await repository.get_active_gateway_key_by_plaintext(VALID_GATEWAY_KEY)

    assert result is None
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_lookup_gateway_key_rejects_malformed_key_before_database_query(
    repository,
    mock_session,
):
    result = await repository.get_active_gateway_key_by_plaintext("not-a-bifrost-key")

    assert result is None
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_list_gateway_keys_for_user_excludes_hashes_and_other_users(
    repository,
    mock_session,
):
    user_id = uuid4()
    keys = [
        MagicMock(user_id=user_id, key_hash="secret-hash-1"),
        MagicMock(user_id=user_id, key_hash="secret-hash-2"),
    ]
    mock_session.execute.return_value = _Result(values=keys)

    result = await repository.list_gateway_keys_for_user(user_id)

    assert result == keys
    mock_session.execute.assert_called_once()
    statement = mock_session.execute.call_args.args[0]
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    assert user_id.hex in str(compiled)


@pytest.mark.asyncio
async def test_revoke_gateway_key_for_user_marks_key_revoked(repository, mock_session):
    user_id = uuid4()
    key_id = uuid4()
    key = MagicMock(user_id=user_id, status="active", revoked_at=None)
    mock_session.execute.return_value = _Result(one=key)

    result = await repository.revoke_gateway_key_for_user(
        key_id=key_id,
        user_id=user_id,
    )

    assert result is key
    assert key.status == "revoked"
    assert key.revoked_at is not None
    mock_session.flush.assert_called_once()


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
async def test_upsert_upstream_account_updates_existing_account_with_encrypted_tokens(
    repository,
    mock_session,
):
    user_id = uuid4()
    existing = MagicMock(
        user_id=user_id,
        provider="chatgpt_codex",
        revoked_at=None,
        encrypted_access_token="old-access",
        encrypted_refresh_token="old-refresh",
    )
    old_encrypted_access_token = existing.encrypted_access_token
    old_encrypted_refresh_token = existing.encrypted_refresh_token
    mock_session.execute.return_value = _Result(one=existing)

    account = await repository.upsert_upstream_account_for_user(
        user_id=user_id,
        upstream_subject="chatgpt-user-123",
        upstream_email="dev@example.test",
        upstream_workspace_id="workspace-midtown",
        access_token="new-access-token",
        refresh_token="new-refresh-token",
        scopes=["openid", "profile"],
    )

    assert account is existing
    assert existing.upstream_subject == "chatgpt-user-123"
    assert existing.upstream_email == "dev@example.test"
    assert existing.upstream_workspace_id == "workspace-midtown"
    assert existing.encrypted_access_token != old_encrypted_access_token
    assert existing.encrypted_refresh_token != old_encrypted_refresh_token
    assert existing.encrypted_access_token != "new-access-token"
    assert existing.encrypted_refresh_token != "new-refresh-token"
    assert decrypt_secret(existing.encrypted_access_token) == "new-access-token"
    assert decrypt_secret(existing.encrypted_refresh_token) == "new-refresh-token"
    assert existing.scopes == ["openid", "profile"]
    mock_session.add.assert_not_called()
    mock_session.flush.assert_called_once()


@pytest.mark.asyncio
async def test_revoke_upstream_account_for_user_marks_active_account_revoked(
    repository,
    mock_session,
):
    user_id = uuid4()
    account = MagicMock(user_id=user_id, provider="chatgpt_codex", revoked_at=None)
    mock_session.execute.return_value = _Result(one=account)

    result = await repository.revoke_upstream_account_for_user(user_id=user_id)

    assert result is account
    assert account.revoked_at is not None
    mock_session.flush.assert_called_once()


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
