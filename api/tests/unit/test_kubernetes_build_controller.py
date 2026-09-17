"""Ownership and recovery contracts at the Kubernetes/PlatformJob boundary."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.jobs.platform.kubernetes_client import (
    _lease_token_fingerprint,
    build_job_manifest,
)
from src.jobs.schedulers import kubernetes_jobs as module
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob


@pytest.fixture
def fixture(monkeypatch):
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=uuid4(), lease_token=uuid4(), job_type="application.deploy",
        execution_backend="kubernetes", kubernetes_namespace="test", status="running", timeout_seconds=120,
        kubernetes_job_uid=None, kubernetes_pod_uid=None, runner_started_at=None,
        kubernetes_launch_started_at=now, lease_expires_at=now + timedelta(seconds=30),
        phase="Starting", revision=1, error_code=None, error_message=None,
    )
    row.kubernetes_job_name = (
        f"bifrost-job-{row.id.hex}-{_lease_token_fingerprint(row.lease_token)[:12]}"
    )
    settings = SimpleNamespace(
        kubernetes_build_namespace="test", kubernetes_build_image="api:test",
        kubernetes_build_configmap="config", kubernetes_build_secret="secret",
        kubernetes_build_service_account="runner", kubernetes_build_memory_request_mib=512,
        kubernetes_build_memory_limit_mib=2048,
        kubernetes_build_cpu_request="250m", kubernetes_build_cpu_limit="1",
        kubernetes_build_pending_timeout_seconds=300, kubernetes_build_max_jobs=2,
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db.execute.return_value = result

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(module, "get_db_context", context)
    monkeypatch.setattr(module, "publish_platform_job_update", AsyncMock())
    loss = AsyncMock()
    monkeypatch.setattr(module, "_handle_runner_loss", loss)
    client = AsyncMock()
    client.namespace = "test"
    client.get_job.return_value = None
    client.list_pods.return_value = []
    claim = ClaimedPlatformJob(row.id, row.lease_token, 120, 0.95)
    manifest = build_job_manifest(claim, settings)
    remote = {**manifest, "metadata": {**manifest["metadata"], "uid": "job-uid"}}
    client.create_job.return_value = remote
    controller = module.KubernetesBuildController(client, settings)
    return SimpleNamespace(row=row, client=client, db=db, controller=controller, remote=remote, loss=loss)


async def test_new_job_is_bound_durably_before_it_can_start(fixture):
    f = fixture
    await f.controller.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_uid == "job-uid"
    f.db.commit.assert_awaited()
    f.client.start_job.assert_not_awaited()
    assert f.client.create_job.call_args.args[0]["spec"]["suspend"] is True

    f.client.get_job.return_value = f.remote
    await f.controller.reconcile_job(f.row.id)
    f.client.create_job.assert_awaited_once()
    f.client.start_job.assert_awaited_once_with(f.row.kubernetes_job_name, "job-uid")


async def test_restart_adopts_existing_suspended_job_without_creating_another(fixture):
    f = fixture
    f.client.get_job.return_value = f.remote
    await f.controller.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_uid == "job-uid"
    f.client.create_job.assert_not_awaited()
    f.client.start_job.assert_not_awaited()


async def test_never_touches_replacement_with_same_name_and_different_uid(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "original-uid"
    f.client.get_job.return_value = f.remote
    with pytest.raises(RuntimeError, match="UID"):
        await f.controller.reconcile_job(f.row.id)
    f.client.start_job.assert_not_awaited()
    f.client.delete_job.assert_not_awaited()
    f.loss.assert_not_awaited()


async def test_never_adopts_unrelated_job_labels(fixture):
    f = fixture
    f.remote["metadata"]["labels"] = {}
    f.client.get_job.return_value = f.remote
    with pytest.raises(RuntimeError, match="labels"):
        await f.controller.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_uid is None
    f.client.start_job.assert_not_awaited()


async def test_api_outage_preserves_attempt_ownership(fixture):
    f = fixture
    f.client.get_job.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await f.controller.reconcile_job(f.row.id)
    f.loss.assert_not_awaited()
    f.client.create_job.assert_not_awaited()


async def test_unschedulable_deadline_does_not_release_a_live_pod(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.kubernetes_launch_started_at -= timedelta(seconds=301)
    f.remote["spec"]["suspend"] = False
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {"phase": "Pending"}}]
    await f.controller.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once_with(f.row.kubernetes_job_name, "job-uid")
    f.loss.assert_not_awaited()

    f.client.get_job.return_value = None
    f.client.list_pods.return_value = []
    await f.controller.reconcile_job(f.row.id)
    assert f.loss.call_args.kwargs["error_code"] == "capacity_unavailable"


async def test_expired_remote_lease_waits_for_confirmed_pod_stop(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.runner_started_at = datetime.now(timezone.utc)
    f.row.lease_expires_at -= timedelta(minutes=1)
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {"phase": "Running"}}]
    await f.controller.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once()
    f.loss.assert_not_awaited()


async def test_oom_maps_to_shared_memory_pressure_error(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.remote["spec"]["suspend"] = False
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {
        "phase": "Failed", "containerStatuses": [{"state": {
            "terminated": {"reason": "OOMKilled"},
        }}],
    }}]
    await f.controller.reconcile_job(f.row.id)
    assert f.loss.call_args.kwargs["error_code"] == "memory_pressure"


async def test_missing_job_does_not_release_an_orphan_still_running(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.client.list_pods.return_value = [{"status": {"phase": "Running"}}]
    await f.controller.reconcile_job(f.row.id)
    f.loss.assert_not_awaited()
    f.client.create_job.assert_not_awaited()


async def test_terminal_cleanup_uses_bound_uid_and_preserves_result(fixture):
    f = fixture
    f.row.status = "succeeded"
    f.row.kubernetes_job_uid = "job-uid"
    f.client.get_job.return_value = f.remote
    name = f.row.kubernetes_job_name
    await f.controller.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once_with(name, "job-uid")
    assert f.row.kubernetes_job_name == name
    f.client.get_job.return_value = None
    await f.controller.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_name is None
    assert f.row.status == "succeeded"
    f.loss.assert_not_awaited()


async def test_deleting_job_without_pods_must_confirm_job_disappearance(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.status = "cancel_requested"
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = []
    await f.controller.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once()
    f.loss.assert_not_awaited()
    f.client.get_job.return_value = None
    await f.controller.reconcile_job(f.row.id)
    f.loss.assert_awaited_once()


async def test_namespace_change_cannot_misclassify_old_job_as_missing(fixture):
    f = fixture
    f.row.kubernetes_namespace = "original-namespace"
    with pytest.raises(RuntimeError, match="namespace changed"):
        await f.controller.reconcile_job(f.row.id)
    f.client.get_job.assert_not_awaited()
    f.loss.assert_not_awaited()


async def test_build_loop_idles_without_cluster_credentials(monkeypatch):
    """A backend-enabled deployment without cluster access must not kill
    the scheduler: the loop logs and waits instead of raising."""
    import asyncio

    settings = SimpleNamespace(
        kubernetes_build_namespace="test",
        kubernetes_build_image="",
        kubernetes_build_configmap="config",
        kubernetes_build_secret="secret",
        kubernetes_build_service_account="runner",
        kubernetes_build_memory_request_mib=512,
        kubernetes_build_memory_limit_mib=2048,
        kubernetes_build_cpu_request="250m",
        kubernetes_build_cpu_limit="1",
        kubernetes_build_pending_timeout_seconds=300,
        kubernetes_build_max_jobs=2,
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    shutdown = asyncio.Event()
    shutdown.set()

    await asyncio.wait_for(
        module.kubernetes_build_loop(shutdown), timeout=10
    )
