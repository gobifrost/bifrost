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
REQUEST = 512 * 2**20
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
    redis_url="redis://localhost:1/0",
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
        memory_limit_bytes=STANDARD, memory_request_bytes=REQUEST,
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
        claim, SETTINGS, SPEC,
        memory_request_bytes=row.memory_request_bytes,
        memory_limit_bytes=row.memory_limit_bytes,
    )
    remote = {**manifest, "metadata": {**manifest["metadata"], "uid": "job-uid"}}
    client.create_job.return_value = remote
    controller = module.KubernetesBuildController(client, SETTINGS, SPEC)  # type: ignore[arg-type]

    async def reconcile_job(job_id):  # type: ignore[no-untyped-def]
        await controller.reconcile_job(job_id)

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


OOM_KILLED = [{"status": {
    "phase": "Failed", "containerStatuses": [{"state": {
        "terminated": {"reason": "OOMKilled"},
    }}],
}}]


async def test_an_oom_seen_after_the_lease_expired_is_learned(fixture):
    """A runner killed at its limit stops renewing its lease; resuming after
    the lease expired must still learn the OOM, not report a lost runner."""
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.runner_started_at = datetime.now(timezone.utc)
    f.row.lease_expires_at -= timedelta(minutes=1)
    f.remote["spec"]["suspend"] = False
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = OOM_KILLED
    await f.reconcile_job(f.row.id)
    assert f.loss.call_args.kwargs["error_code"] == "memory_pressure"
    assert f.loss.call_args.kwargs["out_of_memory"] is True


async def test_an_oom_seen_after_the_job_was_removed_is_learned(fixture):
    f = fixture
    f.row.kubernetes_job_uid = "job-uid"
    f.row.runner_started_at = datetime.now(timezone.utc)
    f.row.error_code = "runner_lost"
    f.row.error_message = "The build runner stopped renewing its execution lease."
    f.client.get_job.return_value = None
    f.client.list_pods.return_value = OOM_KILLED
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
    f.row.memory_limit_bytes = f.row.memory_request_bytes = 3328 * 2**20
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


async def test_a_standard_claim_stays_standard_when_worker_memory_changes(fixture):
    """The claim recorded the pod's request and limit: whatever Worker
    memory is when the Job is created, a standard claim stays Burstable."""
    f = fixture
    f.row.memory_limit_bytes = 3072 * 2**20
    await f.reconcile_job(f.row.id)
    resources = f.client.create_job.call_args.args[0]["spec"]["template"]["spec"][
        "containers"
    ][0]["resources"]
    assert resources["requests"]["memory"] == "512Mi"
    assert resources["limits"]["memory"] == "3072Mi"


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
    from src.services.worker_pool import manifest

    resolve = AsyncMock(return_value=SPEC)
    monkeypatch.setattr(manifest, "resolve_worker_job_spec", resolve)
    seen = []

    class Controller:
        def __init__(self, client, settings, spec):  # type: ignore[no-untyped-def]
            seen.append(spec)

    monkeypatch.setattr(module, "KubernetesBuildController", Controller)
    shutdown = asyncio.Event()
    shutdown.set()

    await module.kubernetes_build_loop(shutdown)

    resolve.assert_awaited_once_with(client, SETTINGS)
    assert seen == [SPEC]
    client.close.assert_awaited_once()


async def test_the_build_loop_retries_a_kubernetes_api_error_reading_its_pod(monkeypatch):
    """A transient API error at loop start does not disable the controller:
    resolution is retried with backoff, then kept for the loop's life."""
    import asyncio

    from src.jobs.platform.kubernetes_client import KubernetesApiError
    from src.services.worker_pool import manifest

    client = AsyncMock()
    client.namespace = "bifrost"
    monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(module, "KubernetesJobClient", lambda settings: client)
    monkeypatch.setattr(manifest, "SPEC_RETRY_INITIAL_SECONDS", 0.01)
    resolve = AsyncMock(side_effect=[KubernetesApiError("GET", "/pods", 503), SPEC])
    monkeypatch.setattr(manifest, "resolve_worker_job_spec", resolve)
    shutdown = asyncio.Event()
    passes = []

    class Controller:
        def __init__(self, client, settings, spec):  # type: ignore[no-untyped-def]
            self.spec = spec

        async def reconcile(self):  # type: ignore[no-untyped-def]
            passes.append(self.spec)
            shutdown.set()

    monkeypatch.setattr(module, "KubernetesBuildController", Controller)

    await asyncio.wait_for(module.kubernetes_build_loop(shutdown), timeout=10)

    assert resolve.await_count == 2
    assert passes == [SPEC]
    client.close.assert_awaited_once()


async def test_the_build_loop_idles_when_its_pod_gives_no_job_identity(monkeypatch):
    import asyncio

    from src.jobs.platform.kubernetes_client import KubernetesConfigurationError
    from src.services.worker_pool import manifest

    client = AsyncMock()
    monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(module, "KubernetesJobClient", lambda settings: client)
    resolve = AsyncMock(side_effect=KubernetesConfigurationError("No image is known for Jobs"))
    monkeypatch.setattr(manifest, "resolve_worker_job_spec", resolve)
    shutdown = asyncio.Event()
    loop = asyncio.create_task(module.kubernetes_build_loop(shutdown))
    await asyncio.sleep(0.05)
    assert not loop.done()
    shutdown.set()
    await asyncio.wait_for(loop, timeout=10)

    resolve.assert_awaited_once()
    client.close.assert_awaited_once()


async def test_a_pass_with_a_request_above_worker_memory_claims_nothing(monkeypatch):
    from src.jobs.platform.kubernetes_client import KubernetesConfigurationError

    _pass_db(monkeypatch, placed=[])
    policy = KubernetesPolicy(isolated_jobs_enabled=True, worker_memory_mib=256)
    monkeypatch.setattr(module, "load_kubernetes_policy", AsyncMock(return_value=policy))
    monkeypatch.setattr(
        module, "load_job_sizing", AsyncMock(return_value=JobSizing.of(policy, 0.85, 0))
    )
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(module, "claim_platform_job", claim)

    with pytest.raises(KubernetesConfigurationError, match="request_mib"):
        await module.KubernetesBuildController(AsyncMock(), SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]
    claim.assert_not_awaited()


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

    busy = await module.KubernetesBuildController(client, SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]

    assert busy is False  # nothing placed, nothing claimed: wait for a wake-up
    claim.assert_awaited_once_with(
        backend="kubernetes",
        isolated=IsolatedPods(
            namespace="bifrost", max_pods=5, sizing=sizing, standard_request_bytes=REQUEST
        ),
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

    assert await controller.reconcile() is True  # a placed pod is followed closely

    returned.assert_awaited_once()
    reconciled.assert_awaited_once_with(placed)
    claim.assert_not_awaited()


async def test_a_build_an_older_release_claimed_is_still_supervised(fixture):
    """During a rolling upgrade an older replica claims without recording the
    memory request; its failed pod must still be handled, like the migration
    treats such rows (the request is the limit)."""
    f = fixture
    f.row.memory_request_bytes = None
    f.row.kubernetes_job_uid = "job-uid"
    f.remote["spec"]["suspend"] = False
    f.client.get_job.return_value = f.remote
    f.client.list_pods.return_value = [{"status": {"phase": "Failed"}}]
    await f.reconcile_job(f.row.id)
    assert f.loss.call_args.kwargs["error_code"] == "runner_exited"


def _loop_with_controller(monkeypatch, outcomes):  # type: ignore[no-untyped-def]
    """Run the build loop with a stub controller whose passes return
    ``outcomes`` in turn (then False); returns (pass times, wake event)."""
    import asyncio
    import time

    from src.services.worker_pool import manifest

    client = AsyncMock()
    monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(module, "KubernetesJobClient", lambda settings: client)
    monkeypatch.setattr(manifest, "resolve_worker_job_spec", AsyncMock(return_value=SPEC))
    monkeypatch.setattr(module, "BUILD_POLL_SECONDS", 0.01)
    monkeypatch.setattr(module, "BUILD_SWEEP_SECONDS", 30)
    woken: list[asyncio.Event] = []

    async def listen(redis_url, *events):  # type: ignore[no-untyped-def]
        woken.extend(events)
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "listen_for_pool_wake", listen)
    passes: list[float] = []
    queued = list(outcomes)

    class Controller:
        def __init__(self, client, settings, spec):  # type: ignore[no-untyped-def]
            pass

        async def reconcile(self):  # type: ignore[no-untyped-def]
            passes.append(time.monotonic())
            return queued.pop(0) if queued else False

    monkeypatch.setattr(module, "KubernetesBuildController", Controller)
    return passes, woken


async def _until(condition, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.005)


async def test_an_idle_build_loop_waits_for_a_wake_up(monkeypatch):
    """With no builds in Kubernetes the controller does not poll; an enqueue's
    wake-up starts the next pass."""
    import asyncio

    passes, woken = _loop_with_controller(monkeypatch, [])
    shutdown = asyncio.Event()
    loop = asyncio.create_task(module.kubernetes_build_loop(shutdown))
    try:
        await _until(lambda: len(passes) == 1 and len(woken) == 1)
        await asyncio.sleep(0.1)  # ten poll intervals
        assert len(passes) == 1

        woken[0].set()
        await _until(lambda: len(passes) == 2)
    finally:
        shutdown.set()
        await asyncio.wait_for(loop, timeout=5)


async def test_a_build_loop_following_builds_polls(monkeypatch):
    import asyncio

    passes, _ = _loop_with_controller(monkeypatch, [True, True, True])
    shutdown = asyncio.Event()
    loop = asyncio.create_task(module.kubernetes_build_loop(shutdown))
    try:
        await _until(lambda: len(passes) == 4)  # three busy passes, then idle
        await asyncio.sleep(0.1)
        assert len(passes) == 4
    finally:
        shutdown.set()
        await asyncio.wait_for(loop, timeout=5)
