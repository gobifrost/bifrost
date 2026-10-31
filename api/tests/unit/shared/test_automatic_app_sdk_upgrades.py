"""Automatic upgrade selection and once-per-target admission on the shared job store."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from shared import application_sdk_upgrades as upgrades
from shared.app_sdk_upgrade_settings import AppSdkUpgradeSettingsService
from shared.models import AppSdkUpgradeSettings
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.services.application_sdk_status import CurrentApplicationSdkMetadata


TARGET = CurrentApplicationSdkMetadata(
    package_version="1.2.3", fingerprint="target-fingerprint", contract_version=9001,
    automatic_upgrade_from=frozenset({9000}),
)

HOSTED_TARGET = CurrentApplicationSdkMetadata(
    package_version="1.2.3",
    fingerprint="hosted-target-fingerprint",
    contract_version=9001,
    automatic_upgrade_from=frozenset({9000}),
    binding_api_major=1,
    supported_binding_api_majors=frozenset({1}),
)


def _application(**changes):
    values = dict(
        id=uuid4(), name="Automatic SDK Test", slug=f"auto-sdk-{uuid4().hex}",
        app_model="standalone_v2",
        active_deployment_id=uuid4(), sdk_package_version="1.0.0",
        sdk_fingerprint="old-fingerprint", sdk_contract_version=9000,
        sdk_built_at=datetime.now(timezone.utc), solution_id=None,
        organization_id=None, repo_path=None,
    )
    return Application(**(values | changes))


@pytest.mark.parametrize("changes", [
    {"sdk_contract_version": 1}, {"sdk_contract_version": 9001}, {"sdk_contract_version": 9002},
    {"sdk_contract_version": None}, {"sdk_built_at": None}, {"sdk_fingerprint": None},
    {"sdk_package_version": None}, {"active_deployment_id": None},
    {"app_model": "inline_v1"},
])
def test_only_reviewed_predecessors_with_complete_provenance_are_eligible(changes):
    assert not upgrades.automatic_upgrade_eligible(_application(**changes), TARGET)


def test_explicit_source_compatible_transition_is_eligible():
    assert upgrades.automatic_upgrade_eligible(_application(), TARGET)


@pytest.mark.parametrize(
    "changes",
    [
        {"sdk_binding_api_major": 1},
        {"sdk_binding_api_major": 1, "sdk_contract_version": 9001},
        {"sdk_binding_api_major": 1, "sdk_fingerprint": HOSTED_TARGET.fingerprint},
    ],
)
def test_hosted_apps_never_auto_rebuild_for_runtime_or_wire_changes(changes):
    assert not upgrades.automatic_upgrade_eligible(_application(**changes), HOSTED_TARGET)


def test_legacy_app_does_not_auto_migrate_to_hosted_binding_without_review():
    assert not upgrades.automatic_upgrade_eligible(_application(), HOSTED_TARGET)


def test_legacy_wire_rule_does_not_authorize_hosted_binding_migration():
    target = CurrentApplicationSdkMetadata(
        package_version="1.2.3",
        fingerprint="hosted-target-fingerprint",
        contract_version=9001,
        automatic_upgrade_from=frozenset({9000}),
        binding_api_major=1,
        supported_binding_api_majors=frozenset({1}),
        automatic_hosted_migration_from=frozenset(),
    )

    assert not upgrades.automatic_upgrade_eligible(_application(), target)


def test_hosted_automatic_target_key_includes_binding_major_and_fingerprint():
    app = _application()

    assert upgrades._automatic_key(app.id, HOSTED_TARGET) == (
        f"automatic:{app.id}:binding-1:hosted-target-fingerprint"
    )


@pytest_asyncio.fixture
async def admission(db_session, monkeypatch):
    """Use real SQL/locks with uncommitted jobs that live workers cannot claim."""
    @asynccontextmanager
    async def context():
        yield db_session

    monkeypatch.setattr(upgrades, "get_db_context", context)
    monkeypatch.setattr(upgrades, "current_sdk_metadata", lambda: TARGET)
    monkeypatch.setattr(upgrades, "ensure_platform_job_notification", AsyncMock())
    monkeypatch.setattr(upgrades, "publish_platform_job_update", AsyncMock())
    monkeypatch.setattr(db_session, "commit", AsyncMock())
    await AppSdkUpgradeSettingsService(db_session).update_settings(
        AppSdkUpgradeSettings(enabled=True), updated_by="test@example.com",
    )
    return db_session


async def _jobs(db):
    return list((await db.scalars(select(PlatformJob).where(
        PlatformJob.job_type == "application.sdk_update",
    ).order_by(PlatformJob.created_at))).all())


async def test_disabled_policy_does_not_compute_or_build_sdk(admission, monkeypatch):
    await AppSdkUpgradeSettingsService(admission).update_settings(
        AppSdkUpgradeSettings(enabled=False), updated_by="test@example.com",
    )
    metadata = AsyncMock(side_effect=AssertionError("disabled policy computed SDK"))
    monkeypatch.setattr(upgrades, "current_sdk_metadata", metadata)

    outcome = await upgrades.enqueue_automatic_app_sdk_upgrades()

    assert "disabled" in outcome.summary
    metadata.assert_not_called()
    assert await _jobs(admission) == []


@pytest.mark.parametrize("status", ["queued", "failed", "succeeded", "cancelled", "requires_action"])
async def test_one_automatic_attempt_per_app_and_target_even_after_restart(admission, status):
    app = _application()
    admission.add(app)
    await admission.flush()

    first = await upgrades.enqueue_automatic_app_sdk_upgrades()
    job, = await _jobs(admission)
    job.status = status
    await admission.flush()
    second = await upgrades.enqueue_automatic_app_sdk_upgrades()

    assert first.summary == "Queued 1 automatic App SDK upgrades"
    assert second.summary == "Queued 0 automatic App SDK upgrades"
    assert len(await _jobs(admission)) == 1
    assert job.requested_by_user_id == "system"
    assert job.resource_lock_key == f"application:{app.id}"
    assert job.payload["automatic_upgrade"] is True
    assert job.payload["target_sdk_fingerprint"] == TARGET.fingerprint
    assert job.payload["target_sdk_contract_version"] == 9001
    assert job.payload["expected_active_deployment_id"] == str(app.active_deployment_id)


async def test_failed_automatic_attempt_can_be_retried_manually(admission):
    app = _application()
    admission.add(app)
    await admission.flush()
    await upgrades.enqueue_automatic_app_sdk_upgrades()
    automatic, = await _jobs(admission)
    automatic.status = "failed"
    await admission.flush()

    result = await upgrades.enqueue_application_sdk_upgrade(
        admission, app, requested_by_user_id=uuid4(), requested_by_email="admin@example.com",
        requested_by_name="Admin", target=TARGET,
    )

    assert result is not None
    manual, reused = result
    assert not reused and manual.id != automatic.id
    assert manual.dedupe_key == str(app.id)
    assert manual.payload["automatic_upgrade"] is False
    assert manual.payload["target_sdk_fingerprint"] == TARGET.fingerprint


async def test_completed_first_batch_cannot_starve_later_apps(admission, monkeypatch):
    monkeypatch.setattr(upgrades, "_BATCH_SIZE", 2)
    admission.add_all([_application() for _ in range(3)])
    await admission.flush()
    await upgrades.enqueue_automatic_app_sdk_upgrades()
    for job in await _jobs(admission):
        job.status = "failed"
    await admission.flush()

    await upgrades.enqueue_automatic_app_sdk_upgrades()

    assert len(await _jobs(admission)) == 3


async def test_active_manual_upgrade_defers_automatic_attempt_without_recording_failure(admission):
    app = _application()
    admission.add(app)
    await admission.flush()
    result = await upgrades.enqueue_application_sdk_upgrade(
        admission, app, requested_by_user_id=uuid4(), requested_by_email="admin@example.com",
        requested_by_name="Admin", target=TARGET,
    )
    assert result is not None
    manual, _ = result
    await upgrades.enqueue_automatic_app_sdk_upgrades()
    assert len(await _jobs(admission)) == 1
    manual.status = "failed"
    await admission.flush()

    await upgrades.enqueue_automatic_app_sdk_upgrades()

    assert len(await _jobs(admission)) == 2


async def test_explicit_hosted_major_change_requires_source_migration(admission):
    app = _application(sdk_binding_api_major=1)
    admission.add(app)
    await admission.flush()
    target = CurrentApplicationSdkMetadata(
        package_version="2.0.0",
        fingerprint="binding-two-fingerprint",
        contract_version=9002,
        binding_api_major=2,
        supported_binding_api_majors=frozenset({1, 2}),
    )

    with pytest.raises(upgrades.AppSdkUpgradeConflict, match="public SDK major"):
        await upgrades.enqueue_application_sdk_upgrade(
            admission,
            app,
            requested_by_user_id=uuid4(),
            requested_by_email="admin@example.com",
            requested_by_name="Admin",
            target=target,
        )
