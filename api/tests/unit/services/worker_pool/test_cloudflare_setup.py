"""Cloudflare capacity setup persists only redacted, generation-bound state."""

from __future__ import annotations

from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import CloudflareCapacityConnectRequest
from src.config import Settings
from src.core.principal import UserPrincipal
from src.models.orm.config import SystemConfig
from src.models.orm.platform_jobs import PlatformJob
from src.services.worker_pool.cloudflare_setup import enqueue_cloudflare_setup
from src.services.worker_pool.connection import CATEGORY, KEY, CapacityConnectionError


API_KEY = "cloudflare-api-key-that-must-never-be-public"


async def test_setup_rejects_redirecting_worker_credentials_to_another_origin(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(CapacityConnectionError, match="configured public HTTPS URL"):
        await enqueue_cloudflare_setup(
            db_session,
            CloudflareCapacityConnectRequest(
                api_key=SecretStr(API_KEY), bifrost_api_origin="https://other.example.test",
            ),
            user=UserPrincipal(user_id=uuid4(), email="admin@example.test", organization_id=None),
            settings=Settings(public_url="https://bifrost.example.test"),
        )
    assert await db_session.scalar(select(SystemConfig).where(
        SystemConfig.category == CATEGORY, SystemConfig.key == KEY,
    )) is None


async def test_setup_stages_encrypted_key_and_generation_only_job_payload_in_one_session(
    db_session: AsyncSession,
) -> None:
    user = UserPrincipal(user_id=uuid4(), email="admin@example.test", organization_id=None)
    request = CloudflareCapacityConnectRequest(
        api_key=SecretStr(API_KEY),
        bifrost_api_origin="https://bifrost.example.test",
        max_workers=2,
    )

    accepted, job = await enqueue_cloudflare_setup(
        db_session, request, user=user, settings=Settings(public_url="https://bifrost.example.test"),
    )

    row = await db_session.scalar(select(SystemConfig).where(
        SystemConfig.organization_id.is_(None),
        SystemConfig.category == CATEGORY,
        SystemConfig.key == KEY,
    ))
    persisted = await db_session.get(PlatformJob, job.id)
    assert row is not None and persisted is job
    assert API_KEY.encode() not in row.value_bytes
    assert persisted.job_type == "cloudflare.capacity.apply"
    assert set(persisted.payload) == {"generation"}
    assert persisted.payload["generation"] == row.value_json["generation"]
    assert persisted.resource_type == "worker_capacity"
    assert persisted.resource_id == "cloudflare"
    assert persisted.action_url == "/settings/kubernetes-executions"
    public = accepted.model_dump_json()
    assert API_KEY not in public
    assert "capacity_token" not in public
    assert accepted.connection.last_job_id == job.id


async def test_setup_rejects_replacement_while_its_canonical_apply_job_is_active(
    db_session: AsyncSession,
) -> None:
    user = UserPrincipal(user_id=uuid4(), email="admin@example.test", organization_id=None)
    request = CloudflareCapacityConnectRequest(
        api_key=SecretStr(API_KEY), bifrost_api_origin="https://bifrost.example.test",
    )
    settings = Settings(public_url="https://bifrost.example.test")
    await enqueue_cloudflare_setup(db_session, request, user=user, settings=settings)

    with pytest.raises(CapacityConnectionError, match="already in progress"):
        await enqueue_cloudflare_setup(
            db_session,
            CloudflareCapacityConnectRequest(
                api_key=SecretStr("replacement-cloudflare-api-key"),
                bifrost_api_origin="https://bifrost.example.test",
            ),
            user=user,
            settings=settings,
        )
