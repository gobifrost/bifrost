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
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob, IsolatedPods
from src.services.kubernetes_execution import KubernetesPolicy
from src.services.worker_pool.manifest import WorkerJobSpec
from src.services.worker_pool.sizing import JobSizing

STANDARD = 2048 * 2**20
SPEC = WorkerJobSpec(
    namespace="test",
    image="api:test",
    env_from=({"configMapRef": {"name": "config"}}, {"secretRef": {"name": "secret"}}),
    service_account="runner",
)
SETTINGS = SimpleNamespace(
    pool_worker_memory_request_mib=512,
    kubernetes_job_cpu_request="250m",
    kubernetes_job_cpu_limit="1",
    kubernetes_job_pending_timeout_seconds=300,
    memory_pressure_threshold=0.85,
)


@pytest.fixture
def fixture(monkeypatch):
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        memory_required_bytes=256 * 2**20,
        id=uuid4(), lease_token=uuid4(), job_type="application.deploy",
        execution_backend="kubernetes", kubernetes_namespace="test", status="running", timeout_seconds=120,
        kubernetes_job_uid=None, kubernetes_pod_uid=None, runner_started_at=None,
        kubernetes_launch_started_at=now, lease_expires_at=now + timedelta(seconds=30),
        phase="Starting", revision=1, error_code=None, error_message=None,
        memory_limit_bytes=STANDARD,
    )
    row.kubernetes_job_name = (
        f"bifrost-job-{row.id.hex}-{_lease_token_fingerprint(row.lease_token)[:12]}"
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
    claim = ClaimedPlatformJob(row.id, row.lease_token, 120, 0.95, row.memory_required_bytes)
    manifest = build_job_manifest(
        claim, SETTINGS, SPEC, memory_limit_bytes=row.memory_limit_bytes, sized=False
    )
    remote = {**manifest, "metadata": {**manifest["metadata"], "uid": "job-uid"}}
    client.create_job.return_value = remote
    controller = module.KubernetesBuildController(client, SETTINGS, SPEC)  # type: ignore[arg-type]

    async def reconcile_job(job_id):  # type: ignore[no-untyped-def]
        await controller.reconcile_job(job_id, STANDARD)

    return SimpleNamespace(
        row=row, client=client, db=db, controller=controller, remote=remote, loss=loss,
        reconcile_job=reconcile_job,
    )


async def test_new_job_is_bound_durably_before_it_can_start(fixture):
    f = fixture
    await f.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_uid == "job-uid"
    f.db.commit.assert_awaited()
    f.client.start_job.assert_not_awaited()
    assert f.client.create_job.call_args.args[0]["spec"]["suspend"] is True

    f.client.get_job.return_value = f.remote
    await f.reconcile_job(f.row.id)
    f.client.create_job.assert_awaited_once()
    f.client.start_job.assert_awaited_once_with(f.row.kubernetes_job_name, "job-uid")


async def test_restart_adopts_existing_suspended_job_without_creating_another(fixture):
    f = fixture
    f.client.get_job.return_value = f.remote
    await f.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_uid == "job-uid"
    f.client.create_job.assert_not_awaited()
    f.client.start_job.assert_not_awaited()


async def test_never_touches_replacement_with_same_name_and_different_uid(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "original-uid"
    f.client.get_job.return_value = f.remote
    with pytest.raises(RuntimeError, match="UID"):
        await f.reconcile_job(f.row.id)
    f.client.start_job.assert_not_awaited()
    f.client.delete_job.assert_not_awaited()
    f.loss.assert_not_awaited()


async def test_never_adopts_unrelated_job_labels(fixture):
    f = fixture
    f.remote["metadata"]["labels"] = {}
    f.client.get_job.return_value = f.remote
    with pytest.raises(RuntimeError, match="labels"):
        await f.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_uid is None
    f.client.start_job.assert_not_awaited()


async def test_api_outage_preserves_attempt_ownership(fixture):
    f = fixture
    f.client.get_job.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await f.reconcile_job(f.row.id)
    f.loss.assert_not_awaited()
    f.client.create_job.assert_not_awaited()


async def test_unschedulable_deadline_does_not_release_a_live_pod(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.kubernetes_launch_started_at -= timedelta(seconds=301)
    f.remote["spec"]["suspend"] = False
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {"phase": "Pending"}}]
    await f.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once_with(f.row.kubernetes_job_name, "job-uid")
    f.loss.assert_not_awaited()

    f.client.get_job.return_value = None
    f.client.list_pods.return_value = []
    await f.reconcile_job(f.row.id)
    assert f.loss.call_args.kwargs["error_code"] == "capacity_unavailable"


async def test_expired_remote_lease_waits_for_confirmed_pod_stop(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.runner_started_at = datetime.now(timezone.utc)
    f.row.lease_expires_at -= timedelta(minutes=1)
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {"phase": "Running"}}]
    await f.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once()
    f.loss.assert_not_awaited()


async def test_oom_maps_to_shared_memory_pressure_error(fixture):
    """An OOM-killed pod's job learns it needs more than the pod's limit."""
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.remote["spec"]["suspend"] = False
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {
        "phase": "Failed", "containerStatuses": [{"state": {
            "terminated": {"reason": "OOMKilled"},
        }}],
    }}]
    await f.reconcile_job(f.row.id)
    assert f.loss.call_args.kwargs["error_code"] == "memory_pressure"
    assert f.loss.call_args.kwargs["out_of_memory"] is True


async def test_a_pod_that_stopped_otherwise_teaches_no_memory(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.remote["spec"]["suspend"] = False
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {"phase": "Failed"}}]
    await f.reconcile_job(f.row.id)
    assert f.loss.call_args.kwargs["error_code"] == "runner_exited"
    assert f.loss.call_args.kwargs["out_of_memory"] is False


async def test_a_sized_pod_is_created_requesting_its_whole_limit(fixture):
    f = fixture
    f.row.memory_limit_bytes = 3328 * 2**20
    await f.reconcile_job(f.row.id)
    resources = f.client.create_job.call_args.args[0]["spec"]["template"]["spec"][
        "containers"
    ][0]["resources"]
    assert resources["requests"]["memory"] == resources["limits"]["memory"] == "3328Mi"


async def test_a_standard_pod_is_created_burstable(fixture):
    f = fixture
    await f.reconcile_job(f.row.id)
    resources = f.client.create_job.call_args.args[0]["spec"]["template"]["spec"][
        "containers"
    ][0]["resources"]
    assert resources["requests"]["memory"] == "512Mi"
    assert resources["limits"]["memory"] == "2048Mi"


async def test_missing_job_does_not_release_an_orphan_still_running(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.client.list_pods.return_value = [{"status": {"phase": "Running"}}]
    await f.reconcile_job(f.row.id)
    f.loss.assert_not_awaited()
    f.client.create_job.assert_not_awaited()


async def test_terminal_cleanup_uses_bound_uid_and_preserves_result(fixture):
    f = fixture
    f.row.status = "succeeded"
    f.row.kubernetes_job_uid = "job-uid"
    f.client.get_job.return_value = f.remote
    name = f.row.kubernetes_job_name
    await f.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once_with(name, "job-uid")
    assert f.row.kubernetes_job_name == name
    f.client.get_job.return_value = None
    await f.reconcile_job(f.row.id)
    assert f.row.kubernetes_job_name is None
    assert f.row.status == "succeeded"
    f.loss.assert_not_awaited()


async def test_deleting_job_without_pods_must_confirm_job_disappearance(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.status = "cancel_requested"
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = []
    await f.reconcile_job(f.row.id)
    f.client.delete_job.assert_awaited_once()
    f.loss.assert_not_awaited()
    f.client.get_job.return_value = None
    await f.reconcile_job(f.row.id)
    f.loss.assert_awaited_once()


async def test_namespace_change_cannot_misclassify_old_job_as_missing(fixture):
    f = fixture
    f.row.kubernetes_namespace = "original-namespace"
    with pytest.raises(RuntimeError, match="namespace changed"):
        await f.reconcile_job(f.row.id)
    f.client.get_job.assert_not_awaited()
    f.loss.assert_not_awaited()


async def test_build_loop_idles_without_cluster_credentials(monkeypatch):
    """Without cluster access the loop logs and waits instead of raising."""
    import asyncio

    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(kubernetes_job_namespace="test"))
    shutdown = asyncio.Event()
    shutdown.set()

    await asyncio.wait_for(
        module.kubernetes_build_loop(shutdown), timeout=10
    )


async def test_the_build_loop_runs_isolated_pods_as_the_api_pod(monkeypatch):
    """With no Job identity settings, the controller resolves the API pod's
    image and environment once, at loop start, and renders every pod with it."""
    import asyncio

    client = AsyncMock()
    client.namespace = "bifrost"
    monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(module, "KubernetesJobClient", lambda settings: client)
    monkeypatch.setattr(module, "resolve_worker_job_spec", AsyncMock(return_value=SPEC))
    seen = []

    class Controller:
        def __init__(self, client, settings, spec):  # type: ignore[no-untyped-def]
            seen.append(spec)

    monkeypatch.setattr(module, "KubernetesBuildController", Controller)
    shutdown = asyncio.Event()
    shutdown.set()

    await module.kubernetes_build_loop(shutdown)

    module.resolve_worker_job_spec.assert_awaited_once_with(client, SETTINGS)
    assert seen == [SPEC]
    client.close.assert_awaited_once()


def _pass_db(monkeypatch, *, placed):  # type: ignore[no-untyped-def]
    db = MagicMock()
    db.scalars = AsyncMock(return_value=MagicMock(all=lambda: placed))
    db.commit = AsyncMock()

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(module, "get_db_context", context)
    return db


async def test_each_pass_admits_within_the_settings_limits(monkeypatch):
    """Max isolated pods and the pod sizes come from Settings -> Kubernetes;
    the claim stamps the controller's namespace."""
    _pass_db(monkeypatch, placed=[])
    policy = KubernetesPolicy(isolated_jobs_enabled=True, max_isolated_pods=5, worker_memory_mib=3072)
    monkeypatch.setattr(module, "load_kubernetes_policy", AsyncMock(return_value=policy))
    sizing = JobSizing.of(policy, 0.85, 768 * 2**20)
    monkeypatch.setattr(module, "load_job_sizing", AsyncMock(return_value=sizing))
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(module, "claim_platform_job", claim)
    client = AsyncMock()
    client.namespace = "bifrost"

    await module.KubernetesBuildController(client, SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]

    claim.assert_awaited_once_with(
        backend="kubernetes",
        isolated=IsolatedPods(namespace="bifrost", max_pods=5, sizing=sizing),
    )


async def test_with_isolation_off_a_pass_reconciles_placed_pods_and_admits_nothing(monkeypatch):
    placed = uuid4()
    _pass_db(monkeypatch, placed=[placed])
    monkeypatch.setattr(
        module, "load_kubernetes_policy", AsyncMock(return_value=KubernetesPolicy())
    )
    returned = AsyncMock(return_value=[])
    monkeypatch.setattr(module, "_return_unplaced_to_local", returned)
    claim = AsyncMock()
    monkeypatch.setattr(module, "claim_platform_job", claim)
    controller = module.KubernetesBuildController(AsyncMock(), SETTINGS, SPEC)  # type: ignore[arg-type]
    reconciled = AsyncMock()
    monkeypatch.setattr(controller, "reconcile_job", reconciled)

    await controller.reconcile()

    returned.assert_awaited_once()
    reconciled.assert_awaited_once_with(placed, STANDARD)
    claim.assert_not_awaited()
