"""Durable credential storage for the external capacity provider."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.models.orm.config import SystemConfig
from src.models.orm.platform_jobs import PlatformJob
from src.services.worker_pool.connection import (
    CATEGORY,
    KEY,
    CapacityConnectionError,
    activate_cloudflare_connection,
    load_capacity_provider_connection,
    stage_cloudflare_api_key,
    update_cloudflare_connection_metadata,
)


API_KEY = "cloudflare-api-key-that-must-not-leak"


def _settings(*, url: str | None = None, token: str | None = None) -> Settings:
    return Settings(
        worker_capacity_url=url,
        worker_capacity_token=SecretStr(token) if token is not None else None,
    )


async def test_staged_connection_encrypts_secrets_and_preserves_control_identity_across_rotation(
    db_session: AsyncSession,
) -> None:
    first = await stage_cloudflare_api_key(db_session, API_KEY, updated_by="admin@example.test")
    row = await db_session.scalar(select(SystemConfig).where(
        SystemConfig.organization_id.is_(None),
        SystemConfig.category == CATEGORY,
        SystemConfig.key == KEY,
    ))
    assert row is not None
    assert API_KEY.encode() not in row.value_bytes
    assert API_KEY not in repr(first)
    assert first.capacity_token not in repr(first)
    assert first.capacity_token not in row.value_json.values()

    assert await activate_cloudflare_connection(
        db_session, first.generation, endpoint="https://capacity.example.test",
        account_id="account-a", deployment={"worker_url": "https://worker.example.test"},
    )
    second = await stage_cloudflare_api_key(
        db_session, "cloudflare-replacement-key-that-must-not-leak", updated_by="admin@example.test",
    )

    assert (second.scope, second.capacity_token) == (first.scope, first.capacity_token)
    assert second.metadata["endpoint"] == "https://capacity.example.test"
    assert second.metadata["active_generation"] == str(first.generation)
    assert not await activate_cloudflare_connection(
        db_session, first.generation, endpoint="https://other.example.test",
        account_id="account-a", deployment={},
    )
    active = await load_capacity_provider_connection(db_session, _settings())
    assert active is not None
    assert (active.endpoint, active.token, active.generation) == (
        "https://capacity.example.test", first.capacity_token, first.generation,
    )


@pytest.mark.parametrize("settings", [
    _settings(url="https://capacity.example.test"),
    _settings(token="dedicated-env-token-" + "x" * 40),
])
async def test_connection_rejects_partial_environment_configuration(
    db_session: AsyncSession, settings: Settings,
) -> None:
    with pytest.raises(CapacityConnectionError, match="requires both endpoint"):
        await load_capacity_provider_connection(db_session, settings)


async def test_connection_rejects_environment_and_stored_credentials_without_leaking_them(
    db_session: AsyncSession,
) -> None:
    await stage_cloudflare_api_key(db_session, API_KEY, updated_by="admin@example.test")
    env_token = "dedicated-env-token-" + "x" * 40

    with pytest.raises(CapacityConnectionError) as caught:
        await load_capacity_provider_connection(
            db_session, _settings(url="https://capacity.example.test", token=env_token),
        )
    assert API_KEY not in str(caught.value)
    assert env_token not in str(caught.value)


async def test_malformed_ciphertext_fails_closed_without_disclosing_credential_material(
    db_session: AsyncSession,
) -> None:
    marker = "stored-capacity-token-that-must-not-leak"
    db_session.add(SystemConfig(
        category=CATEGORY, key=KEY, organization_id=None,
        value_json={"generation": str(uuid4()), "scope": "bifrost:test", "enabled": True},
        value_bytes=marker.encode(),
    ))
    await db_session.flush()

    with pytest.raises(CapacityConnectionError) as caught:
        await load_capacity_provider_connection(db_session, _settings())
    assert marker not in str(caught.value)


async def test_active_cloudflare_apply_prevents_key_replacement_but_terminal_job_does_not(
    db_session: AsyncSession,
) -> None:
    await stage_cloudflare_api_key(db_session, API_KEY, updated_by="admin@example.test")
    job = PlatformJob(
        job_type="cloudflare.capacity.apply", payload={}, resource_type="worker_capacity",
        resource_id="cloudflare", requested_by_user_id=str(uuid4()),
        requested_by_email="admin@example.test", requested_by_name="Admin",
        title="Apply Cloudflare capacity", memory_required_bytes=1,
    )
    db_session.add(job)
    await db_session.flush()

    with pytest.raises(CapacityConnectionError, match="already in progress"):
        await stage_cloudflare_api_key(
            db_session, "cloudflare-replacement-key-that-must-not-leak", updated_by="admin@example.test",
        )
    job.status = "failed"
    await db_session.flush()
    replacement = await stage_cloudflare_api_key(
        db_session, "cloudflare-replacement-key-that-must-not-leak", updated_by="admin@example.test",
    )
    assert replacement.generation


async def test_metadata_update_is_generation_fenced_and_refuses_secret_fields(
    db_session: AsyncSession,
) -> None:
    connection = await stage_cloudflare_api_key(db_session, API_KEY, updated_by="admin@example.test")

    assert not await update_cloudflare_connection_metadata(
        db_session, uuid4(), {"status": "failed"},
    )
    with pytest.raises(CapacityConnectionError, match="metadata"):
        await update_cloudflare_connection_metadata(
            db_session, connection.generation, {"account_id": "must-not-overwrite-active-account"},
        )
    assert await update_cloudflare_connection_metadata(
        db_session, connection.generation, {
            "status": "failed", "error": "safe summary", "last_job_id": str(uuid4()),
            "accounts": [{"id": "a" * 32, "name": "Capacity account"}],
            "requested_account_id": "a" * 32,
            "desired_bifrost_api_origin": "https://api.example.test",
            "desired_max_workers": 2,
        },
    )
    with pytest.raises(CapacityConnectionError, match="deployment metadata"):
        await activate_cloudflare_connection(
            db_session, connection.generation, endpoint="https://capacity.example.test",
            account_id="account-a", deployment={"token": "must-not-publish"},
        )
