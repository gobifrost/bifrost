"""Unit tests for sandbox runner provider configuration."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.security import decrypt_secret
from src.models.contracts.sandbox_runner import (
    DEFAULT_CLOUDFLARE_SCRIPT_NAME,
    DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
    SandboxRunnerCloudflareConfig,
    SandboxRunnerConfigSave,
    SandboxRunnerLocalConfig,
)
from src.services.sandbox_runner_config import (
    SANDBOX_RUNNER_CONFIG_CATEGORY,
    SANDBOX_RUNNER_CONFIG_KEY,
    SandboxRunnerConfigService,
)


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


def _set_row(mock_session, row):
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    mock_session.execute.return_value = result


def _system_config(value_json):
    row = MagicMock()
    row.id = uuid4()
    row.category = SANDBOX_RUNNER_CONFIG_CATEGORY
    row.key = SANDBOX_RUNNER_CONFIG_KEY
    row.organization_id = None
    row.value_json = value_json
    row.created_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    return row


@pytest.mark.asyncio
async def test_save_cloudflare_encrypts_and_masks_api_token(mock_session):
    _set_row(mock_session, None)

    service = SandboxRunnerConfigService(mock_session)
    public = await service.save_config(
        SandboxRunnerConfigSave(
            provider="cloudflare",
            callback_base_url="https://bifrost.example.com/",
            enabled=True,
            cloudflare=SandboxRunnerCloudflareConfig(
                account_id="acct_123",
                api_token="cf-secret-token",
            ),
        ),
        updated_by="admin@example.com",
    )

    mock_session.add.assert_called_once()
    saved = mock_session.add.call_args[0][0]
    assert saved.category == SANDBOX_RUNNER_CONFIG_CATEGORY
    assert saved.key == SANDBOX_RUNNER_CONFIG_KEY
    assert saved.organization_id is None
    assert saved.value_json["callback_base_url"] == "https://bifrost.example.com"
    cloudflare = saved.value_json["cloudflare"]
    assert cloudflare["script_name"] == DEFAULT_CLOUDFLARE_SCRIPT_NAME
    assert cloudflare["workflow_name"] == DEFAULT_CLOUDFLARE_WORKFLOW_NAME
    assert cloudflare["encrypted_api_token"] != "cf-secret-token"
    assert "cf-secret-token" not in str(saved.value_json)
    assert decrypt_secret(cloudflare["encrypted_api_token"]) == "cf-secret-token"

    assert public.cloudflare is not None
    assert public.cloudflare.api_token_set is True
    assert public.provisioned is False
    assert public.connected is False
    assert not hasattr(public.cloudflare, "api_token")


@pytest.mark.asyncio
async def test_runtime_status_can_only_be_set_through_internal_service(mock_session):
    row = _system_config(
        {
            "provider": "cloudflare",
            "enabled": False,
            "callback_base_url": "https://bifrost.example.com",
            "provisioned": False,
            "connected": False,
            "cloudflare": {
                "account_id": "acct_123",
                "encrypted_api_token": "encrypted",
                "script_name": DEFAULT_CLOUDFLARE_SCRIPT_NAME,
                "workflow_name": DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
            },
            "local": None,
        }
    )
    _set_row(mock_session, row)

    public = await SandboxRunnerConfigService(mock_session).set_runtime_status(
        provisioned=True,
        connected=True,
        updated_by="admin@example.com",
    )

    assert row.value_json["provisioned"] is True
    assert row.value_json["connected"] is True
    assert row.updated_by == "admin@example.com"
    assert public.provisioned is True
    assert public.connected is True


@pytest.mark.asyncio
async def test_connection_change_resets_proven_runtime_status(mock_session):
    row = _system_config(
        {
            "provider": "cloudflare",
            "enabled": True,
            "callback_base_url": "https://old.example.com",
            "provisioned": True,
            "connected": True,
            "cloudflare": {
                "account_id": "acct_123",
                "encrypted_api_token": "encrypted",
                "script_name": DEFAULT_CLOUDFLARE_SCRIPT_NAME,
                "workflow_name": DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
            },
            "local": None,
        }
    )
    _set_row(mock_session, row)

    public = await SandboxRunnerConfigService(mock_session).save_config(
        SandboxRunnerConfigSave(
            provider="cloudflare",
            enabled=True,
            callback_base_url="https://new.example.com",
            cloudflare=SandboxRunnerCloudflareConfig(account_id="acct_123"),
        )
    )

    assert public.provisioned is False
    assert public.connected is False


@pytest.mark.asyncio
async def test_save_preserves_existing_cloudflare_token_when_omitted(mock_session):
    service = SandboxRunnerConfigService(mock_session)
    first_encrypted = service._build_cloudflare_payload(
        SandboxRunnerConfigSave(
            provider="cloudflare",
            cloudflare=SandboxRunnerCloudflareConfig(api_token="original-token"),
        ),
        None,
    )["encrypted_api_token"]
    row = _system_config(
        {
            "provider": "cloudflare",
            "enabled": False,
            "callback_base_url": "https://old.example.com",
            "provisioned": False,
            "connected": False,
            "cloudflare": {
                "account_id": "old-account",
                "encrypted_api_token": first_encrypted,
                "script_name": "old-script",
                "workflow_name": "old-workflow",
            },
            "local": None,
        }
    )
    _set_row(mock_session, row)

    await service.save_config(
        SandboxRunnerConfigSave(
            provider="cloudflare",
            callback_base_url="https://new.example.com",
            cloudflare=SandboxRunnerCloudflareConfig(
                account_id="new-account",
                script_name="new-script",
                workflow_name="new-workflow",
            ),
        )
    )

    assert row.value_json["cloudflare"]["encrypted_api_token"] == first_encrypted
    assert decrypt_secret(row.value_json["cloudflare"]["encrypted_api_token"]) == "original-token"
    assert row.value_json["cloudflare"]["account_id"] == "new-account"
    assert row.value_json["cloudflare"]["script_name"] == "new-script"


@pytest.mark.asyncio
async def test_get_decrypted_internal_config_returns_secret_only_in_internal_method(mock_session):
    service = SandboxRunnerConfigService(mock_session)
    encrypted = service._build_cloudflare_payload(
        SandboxRunnerConfigSave(
            provider="cloudflare",
            cloudflare=SandboxRunnerCloudflareConfig(api_token="private-token"),
        ),
        None,
    )["encrypted_api_token"]
    _set_row(
        mock_session,
        _system_config(
            {
                "provider": "cloudflare",
                "enabled": True,
                "callback_base_url": "https://bifrost.example.com",
                "provisioned": True,
                "connected": True,
                "cloudflare": {
                    "account_id": "acct_123",
                    "encrypted_api_token": encrypted,
                    "script_name": DEFAULT_CLOUDFLARE_SCRIPT_NAME,
                    "workflow_name": DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
                },
                "local": None,
            }
        ),
    )

    public = await service.get_config()
    internal = await service.get_decrypted_internal_config()

    assert public is not None
    assert public.cloudflare is not None
    assert public.cloudflare.api_token_set is True
    assert "private-token" not in public.model_dump_json()
    assert internal is not None
    assert internal["cloudflare"]["api_token"] == "private-token"
    assert "encrypted_api_token" not in internal["cloudflare"]


@pytest.mark.asyncio
async def test_local_config_generates_runner_secret_and_masks_it(mock_session):
    _set_row(mock_session, None)

    service = SandboxRunnerConfigService(mock_session)
    public = await service.save_config(
        SandboxRunnerConfigSave(
            provider="local",
            callback_base_url="http://localhost:3000",
            local=SandboxRunnerLocalConfig(endpoint_url="http://localhost:9137"),
        )
    )

    saved = mock_session.add.call_args[0][0]
    local = saved.value_json["local"]
    assert local["endpoint_url"] == "http://localhost:9137"
    assert local["encrypted_runner_secret"]
    assert public.local is not None
    assert public.local.runner_secret_set is True
    assert "runner_secret" not in public.local.model_dump()


@pytest.mark.asyncio
async def test_readiness_reports_actionable_blockers(mock_session):
    _set_row(
        mock_session,
        _system_config(
            {
                "provider": "cloudflare",
                "enabled": False,
                "callback_base_url": None,
                "provisioned": False,
                "connected": False,
                "cloudflare": {
                    "account_id": "acct_123",
                    "encrypted_api_token": None,
                    "script_name": DEFAULT_CLOUDFLARE_SCRIPT_NAME,
                    "workflow_name": DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
                },
                "local": None,
            }
        ),
    )

    readiness = await SandboxRunnerConfigService(mock_session).get_readiness(ai_configured=False)

    assert readiness.ready is False
    assert readiness.configured is True
    assert readiness.provider == "cloudflare"
    assert readiness.credentials_configured is False
    assert [blocker.code for blocker in readiness.blockers] == [
        "ai_not_configured",
        "credentials_missing",
        "callback_missing",
        "not_provisioned",
        "not_connected",
        "not_enabled",
    ]


@pytest.mark.asyncio
async def test_readiness_ready_when_all_checks_pass(mock_session):
    service = SandboxRunnerConfigService(mock_session)
    encrypted = service._build_local_payload(
        SandboxRunnerConfigSave(
            provider="local",
            local=SandboxRunnerLocalConfig(
                endpoint_url="http://localhost:9137",
                runner_secret="local-secret",
            ),
        ),
        None,
    )["encrypted_runner_secret"]
    _set_row(
        mock_session,
        _system_config(
            {
                "provider": "local",
                "enabled": True,
                "callback_base_url": "http://localhost:3000",
                "provisioned": True,
                "connected": True,
                "cloudflare": None,
                "local": {
                    "endpoint_url": "http://localhost:9137",
                    "encrypted_runner_secret": encrypted,
                },
            }
        ),
    )

    readiness = await service.get_readiness(ai_configured=True)

    assert readiness.ready is True
    assert readiness.blockers == []
    assert readiness.credentials_configured is True
    assert readiness.callback_configured is True


@pytest.mark.asyncio
async def test_dispatch_readiness_does_not_require_ai_but_requires_live_provider(
    mock_session,
):
    service = SandboxRunnerConfigService(mock_session)
    encrypted = service._build_cloudflare_payload(
        SandboxRunnerConfigSave(
            provider="cloudflare",
            cloudflare=SandboxRunnerCloudflareConfig(api_token="private-token"),
        ),
        None,
    )["encrypted_api_token"]
    row = _system_config(
        {
            "provider": "cloudflare",
            "enabled": True,
            "callback_base_url": "https://bifrost.example.com",
            "provisioned": True,
            "connected": True,
            "cloudflare": {
                "account_id": "acct_123",
                "encrypted_api_token": encrypted,
                "script_name": DEFAULT_CLOUDFLARE_SCRIPT_NAME,
                "workflow_name": DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
            },
            "local": None,
        }
    )
    _set_row(mock_session, row)

    assert await service.is_dispatch_ready() is True
    row.value_json["connected"] = False
    assert await service.is_dispatch_ready() is False
