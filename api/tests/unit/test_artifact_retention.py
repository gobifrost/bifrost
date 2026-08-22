from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs.platform.system_maintenance import (
    ARTIFACT_RETENTION_CLEANUP_DEFINITION,
    EmptyMaintenancePayload,
    run_artifact_retention_cleanup,
)
from src.jobs.schedulers.artifact_retention import (
    cleanup_expired_chat_artifacts_schedule,
)
from src.models.contracts.artifact_retention import ArtifactRetentionSettings
from src.models.orm import Artifact, SystemConfig
from src.services.artifact_retention import (
    ArtifactRetentionSettingsService,
    cleanup_expired_chat_artifacts,
)


def _execute_result(*rows):
    result = MagicMock()
    result.scalars.return_value.first.return_value = rows[0] if rows else None
    result.scalars.return_value.all.return_value = list(rows)
    return result


@pytest.mark.asyncio
async def test_retention_settings_default_to_disabled_cleanup() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result())

    settings = await ArtifactRetentionSettingsService(db).get_settings()

    assert settings == ArtifactRetentionSettings(enabled=False, retention_days=90)


@pytest.mark.asyncio
async def test_retention_settings_are_stored_in_system_config() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result())
    db.add = MagicMock()
    db.flush = AsyncMock()

    settings = await ArtifactRetentionSettingsService(db).update_settings(
        ArtifactRetentionSettings(enabled=True, retention_days=30),
        updated_by="admin@example.com",
    )

    assert settings.enabled is True
    added = db.add.call_args.args[0]
    assert isinstance(added, SystemConfig)
    assert added.category == "artifact_retention"
    assert added.key == "chat"
    assert added.value_json == {"enabled": True, "retention_days": 30}
    assert added.organization_id is None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_noops_when_retention_is_disabled() -> None:
    config = SystemConfig(
        category="artifact_retention",
        key="chat",
        value_json={"enabled": False, "retention_days": 1},
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result(config))

    deleted, failed = await cleanup_expired_chat_artifacts(db)

    assert (deleted, failed) == (0, 0)
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_cleanup_deletes_expired_artifact_objects_and_rows() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    config = SystemConfig(
        category="artifact_retention",
        key="chat",
        value_json={"enabled": True, "retention_days": 30},
    )
    expired = Artifact(
        id=uuid4(),
        organization_id=uuid4(),
        created_by_user_id=uuid4(),
        s3_key="_artifacts/conversation/report.pdf",
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=10,
        created_at=now - timedelta(days=31),
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[_execute_result(config), _execute_result(expired)]
    )
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    storage = AsyncMock()

    with patch(
        "src.services.artifact_retention.get_file_storage_service",
        return_value=storage,
    ):
        deleted, failed = await cleanup_expired_chat_artifacts(db, now=now)

    assert (deleted, failed) == (1, 0)
    storage.delete_raw_from_s3.assert_awaited_once_with(expired.s3_key)
    db.delete.assert_awaited_once_with(expired)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_platform_job_handler_runs_cleanup_and_reports_result() -> None:
    db = AsyncMock()
    db.__aenter__.return_value = db
    db.__aexit__.return_value = None
    context = AsyncMock()

    with (
        patch("src.jobs.platform.system_maintenance.get_db_context", return_value=db),
        patch.object(
            ArtifactRetentionSettingsService,
            "get_settings",
            AsyncMock(
                return_value=ArtifactRetentionSettings(enabled=True, retention_days=14)
            ),
        ),
        patch(
            "src.services.artifact_retention.cleanup_expired_chat_artifacts",
            AsyncMock(return_value=(2, 0)),
        ),
    ):
        result = await run_artifact_retention_cleanup(
            context,
            EmptyMaintenancePayload(),
        )

    assert result == {
        "enabled": True,
        "retention_days": 14,
        "deleted_count": 2,
        "failed_count": 0,
    }
    context.report.assert_any_await("Finding expired artifacts", percent=5)
    context.report.assert_any_await("Artifact retention cleanup complete", percent=100)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_enqueues_registered_platform_job() -> None:
    job_id = uuid4()

    with patch(
        "src.jobs.platform.system_maintenance.enqueue_automatic_artifact_retention_cleanup",
        AsyncMock(
            return_value=MagicMock(
                summary="Durable job enqueued",
                platform_job_id=job_id,
            )
        ),
    ) as enqueue:
        outcome = await cleanup_expired_chat_artifacts_schedule()

    enqueue.assert_awaited_once()
    assert outcome.summary == "Durable job enqueued"
    assert outcome.platform_job_id == job_id


def test_artifact_retention_platform_job_is_registered() -> None:
    from src.jobs.platform.registry import get_platform_job_definition

    definition = get_platform_job_definition("artifact.retention_cleanup")

    assert definition is ARTIFACT_RETENTION_CLEANUP_DEFINITION
    assert definition.policy.max_attempts == 2
    assert definition.policy.max_concurrency == 1
