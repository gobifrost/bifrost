"""External capacity uses immutable resources and canonical Postgres admission."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select

from shared.models import WorkerCapacityProfile, WorkerCapacityReservation
from shared.worker_compute import WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES, WORKER_IMAGE_HANDLER_NAMES
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.platform_jobs import PlatformJob
from src.models.enums import ExecutionStatus
from src.services import work_dispatch
from src.services.worker_pool import claims, controller, external_controller, external_policy
from src.services.worker_pool.external_client import CapacityProtocolError, ExternalCapacityClient
from src.services.worker_pool.external_controller import ExternalWorkerPoolController
from tests.unit.services.worker_pool.test_worker_pool import (
    GIB, POLICY, SETTINGS, _accept_pool_run, _clean as _clean, _job_worker,
    _store_policy, db as db_fixture,
)

db = db_fixture
TOKEN = "synthetic-capacity-token-" + "x" * 32
ACTUAL_HANDLERS = list(WORKER_IMAGE_HANDLER_NAMES)
ACTUAL_COMPUTE = list(WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES)


def test_capacity_profile_is_a_v1_hardware_envelope() -> None:
    profile = {
        "protocol_version": 1,
        "scope": "test-provider",
        "worker_version": "test-worker",
        "memory_limit_bytes": 2 * GIB,
        "max_workers": 2,
        "max_lifetime_seconds": 21600,
    }
    assert WorkerCapacityProfile.model_validate(profile).protocol_version == 1
    for invalid in (
        {**profile, "execution_handlers": ["workflow.execute"]},
        {**profile, "compute_capabilities": ["app.compile.v2"]},
        {**profile, "unexpected": True},
        {**profile, "protocol_version": 2},
    ):
        with pytest.raises(ValidationError):
            WorkerCapacityProfile.model_validate(invalid)


def test_external_capacity_profile_build_must_match_the_api(monkeypatch) -> None:
    monkeypatch.setattr(controller, "get_version", lambda: "api-build")
    profile = WorkerCapacityProfile(
        protocol_version=1, scope="test-provider", worker_version="worker-build",
        memory_limit_bytes=2 * GIB, max_workers=2, max_lifetime_seconds=21600,
    )

    with pytest.raises(CapacityProtocolError, match="must match the running API build"):
        ExternalWorkerPoolController(AsyncMock(), SETTINGS, profile)


class Provider:
    def __init__(self):
        self.profile = WorkerCapacityProfile(
            protocol_version=1, scope="test-provider", worker_version="test-worker",
            memory_limit_bytes=2 * GIB, max_workers=2, max_lifetime_seconds=21600,
        )
        self.resources = {}
        self.requests = []
        self.starts = []
        self.before_start: Callable[[UUID], Awaitable[None]] | None = None
        self.lose_start_response = False
        self.fail_deletions = False

    async def handle(self, request):
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json=self.profile.model_dump())
        assert request.headers["X-Bifrost-Capacity-Scope"] == self.profile.scope
        worker_id = request.url.path.split("/")[3]
        resource = self.resources.get(worker_id)
        if request.method == "GET":
            return httpx.Response(404) if resource is None else httpx.Response(200, json=resource)
        body = json.loads(request.content)
        if request.method == "PUT":
            reservation = WorkerCapacityReservation.model_validate(body)
            assert str(reservation.worker_id) == worker_id
            if resource is None:
                resource = {
                    "scope": self.profile.scope, "worker_id": worker_id,
                    "worker_version": reservation.worker_version,
                    "resource_uid": f"resource-{UUID(worker_id).hex}", "state": "prepared",
                }
                self.resources[worker_id] = resource
        else:
            assert resource is not None
            if body["resource_uid"] != resource["resource_uid"]:
                return httpx.Response(409)
            if request.method == "POST":
                if self.before_start is not None:
                    await self.before_start(UUID(worker_id))
                if resource["state"] == "prepared":
                    self.starts.append(worker_id)
                    resource["state"] = "running"
                if self.lose_start_response:
                    self.lose_start_response = False
                    raise httpx.ReadTimeout("synthetic lost response", request=request)
            elif request.method == "DELETE":
                if self.fail_deletions:
                    raise httpx.ReadTimeout("synthetic provider outage", request=request)
                resource["state"] = "deleted"
        return httpx.Response(200, json=resource)


@pytest_asyncio.fixture
async def capacity(db, monkeypatch):
    provider = Provider()
    policy = replace(POLICY, largest_worker_mib=2048)
    await _store_policy(db.sessions, policy)
    monkeypatch.setattr(external_controller, "get_db_context", db.context)
    monkeypatch.setattr(controller, "get_version", lambda: provider.profile.worker_version)
    monkeypatch.setattr(external_policy, "get_version", lambda: provider.profile.worker_version)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)) as http:
        client = ExternalCapacityClient("https://capacity.invalid", TOKEN, http=http)
        pool = ExternalWorkerPoolController(client, SETTINGS, await client.profile())
        yield SimpleNamespace(provider=provider, client=client, pool=pool, policy=policy)


async def test_prepared_uid_is_committed_before_start(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)

    async def before_start(starting_id):
        assert starting_id == worker_id
        async with db.context() as session:
            worker = await session.scalar(select(ExecutionWorker).where(
                ExecutionWorker.id == worker_id,
            ).with_for_update(nowait=True))
            assert worker.state == "starting"
            assert worker.capacity_resource_uid == capacity.provider.resources[str(worker_id)]["resource_uid"]

    capacity.provider.before_start = before_start
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    assert capacity.provider.starts == [str(worker_id)]


async def test_external_reservation_uses_the_worker_image_contract(db, capacity):
    await _accept_pool_run()

    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker is not None
        assert worker.supported_handlers == list(WORKER_IMAGE_HANDLER_NAMES)
        assert worker.supported_compute == list(WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES)


async def test_external_warm_pool_does_not_count_kubernetes_capacity(db, capacity):
    kubernetes_worker = (await _job_worker(db.sessions, state="ready")).id
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, kubernetes_worker)
        worker.supported_handlers = ["workflow.execute"]
        worker.memory_budget_bytes = GIB
        await session.commit()

    [worker_id] = await capacity.pool.launch_for_demand(
        replace(capacity.policy, warm_workers=1, max_workers=2)
    )

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
    assert worker is not None
    assert (worker.form, worker.capacity_scope) == ("external", "test-provider")


async def test_external_capacity_plans_worker_image_supported_agent_and_platform_demand(db, capacity):
    async with db.context() as session:
        work_dispatch.stage_dispatch(
            session, family="agent_run", subject_id=uuid4(), handler="agent.run",
            handler_version=1, message={}, payload={}, memory_request_bytes=64 * 1024**2,
        )
        session.add(PlatformJob(
            job_type="solution.deploy", payload={}, requested_by_user_id=str(uuid4()),
            requested_by_email="test@example.com", requested_by_name="Test", title="Solution deploy",
            memory_required_bytes=64 * 1024**2,
        ))

    assert len(await capacity.pool.launch_for_demand(capacity.policy)) == 2


async def test_external_app_compile_demand_uses_the_worker_image_compute_contract(db, capacity):
    async with db.context() as session:
        session.add(PlatformJob(
            job_type="application.deploy", payload={}, requested_by_user_id=str(uuid4()),
            requested_by_email="test@example.com", requested_by_name="Test", title="App deploy",
            memory_required_bytes=64 * 1024**2,
        ))

    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker is not None
        assert worker.supported_compute == ["app.compile.v2"]


async def test_lost_start_response_is_observed_without_resending_the_start(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    capacity.provider.lose_start_response = True
    with pytest.raises(httpx.ReadTimeout):
        await capacity.pool.reconcile_worker(worker_id, capacity.policy)

    await capacity.pool.reconcile_worker(worker_id, capacity.policy)

    assert capacity.provider.starts == [str(worker_id)]
    assert sum(method == "POST" for method, _path in capacity.provider.requests) == 1
    assert len(capacity.provider.resources) == 1


async def test_two_controllers_adopt_the_same_prepared_resource(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    other = ExternalWorkerPoolController(capacity.client, SETTINGS, capacity.provider.profile)

    await asyncio.gather(
        capacity.pool.reconcile_worker(worker_id, capacity.policy),
        other.reconcile_worker(worker_id, capacity.policy),
    )

    assert len(capacity.provider.resources) == 1
    assert capacity.provider.starts == [str(worker_id)]
    assert await other.launch_for_demand(capacity.policy) == []


async def test_external_admission_binds_only_the_first_process(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    uid = capacity.provider.resources[str(worker_id)]["resource_uid"]
    launcher_id = uuid4()

    assert await claims.admit_external_worker(
        worker_id, resource_uid="wrong", launcher_id=launcher_id, version="test-worker", hostname="unbound",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
    ) is None
    for version in (None, "previous-worker"):
        assert await claims.admit_external_worker(
            worker_id, resource_uid=uid, launcher_id=launcher_id, version=version, hostname="wrong-build",
            actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
        ) is None
    assert await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=launcher_id, version="test-worker", hostname="missing-compute",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=[],
    ) is None
    assert await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=launcher_id, version="test-worker", hostname="physical-worker",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
    ) == worker_id
    assert await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=uuid4(), version="test-worker", hostname="replacement",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
    ) is None
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.remote_launcher_id == launcher_id
        assert worker.version == "test-worker"
        assert worker.hostname == "physical-worker"


async def test_provider_build_change_is_rejected_before_it_relabels_a_reservation(db, capacity):
    await _accept_pool_run()
    [old_id] = await capacity.pool.launch_for_demand(capacity.policy)
    capacity.provider.profile = capacity.provider.profile.model_copy(update={"worker_version": "next-worker"})
    with pytest.raises(CapacityProtocolError, match="must match the running API build"):
        await capacity.pool.reconcile()
    assert capacity.provider.resources == {}
    assert capacity.provider.starts == []
    async with db.context() as session:
        old = await session.get(ExecutionWorker, old_id)
        assert (old.state, old.version, old.end_reason) == ("reserved", "test-worker", None)


async def test_recovered_preparation_cannot_bind_or_start_a_different_build(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    # Preparation committed at the provider, but its response was lost before
    # the canonical resource UID could be bound.
    await capacity.client.prepare(WorkerCapacityReservation(
        worker_id=worker_id, worker_version="test-worker", memory_limit_bytes=2 * GIB,
        max_lifetime_seconds=SETTINGS.pool_max_lifetime_seconds, drain_grace_seconds=30,
    ), capacity.provider.profile.scope)
    capacity.provider.resources[str(worker_id)]["worker_version"] = "wrong-worker"

    with pytest.raises(CapacityProtocolError, match="build changed"):
        await capacity.pool.reconcile_worker(worker_id, capacity.policy)

    assert capacity.provider.starts == []
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.state == "reserved"
        assert worker.capacity_resource_uid is None


async def test_build_mismatch_does_not_prevent_exact_cleanup_after_fencing(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    resource = capacity.provider.resources[str(worker_id)]
    resource["worker_version"] = "wrong-worker"
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        worker.state = "lost"
        await session.commit()

    await capacity.pool.reconcile_worker(worker_id, capacity.policy)

    assert resource["state"] == "deleted"
    assert capacity.provider.starts == [str(worker_id)]


async def test_provider_rollout_rotates_an_admitted_worker_from_the_same_controller(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    uid = capacity.provider.resources[str(worker_id)]["resource_uid"]
    await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=uuid4(), version="test-worker", hostname="old-build",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
    )
    # This controller remains alive while the external provider changes builds.
    capacity.pool.live = external_controller.LiveControllers(
        ids=frozenset({capacity.pool.id}), versions=frozenset({"next-worker"}),
    )
    kubernetes_worker = (await _job_worker(db.sessions, state="ready")).id
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, kubernetes_worker)
        worker.version = "old-kubernetes-build"
        worker.launched_by = uuid4()
        await session.commit()

    assert await capacity.pool.replace_outdated(replace(capacity.policy, warm_workers=0)) == [worker_id]

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert (worker.state, worker.end_reason) == ("draining", "rotated")
        foreign = await session.get(ExecutionWorker, kubernetes_worker)
        assert (foreign.state, foreign.end_reason) == ("ready", None)
    assert capacity.provider.resources[str(worker_id)]["state"] == "running"


async def test_provider_cannot_replace_an_existing_resource_uid(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    capacity.provider.resources[str(worker_id)]["resource_uid"] = "replacement"

    with pytest.raises(CapacityProtocolError, match="UID changed"):
        await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    assert not any(method == "DELETE" for method, _path in capacity.provider.requests)


async def test_policy_exceeding_provider_capability_cannot_reserve(db, capacity):
    await _accept_pool_run()
    with pytest.raises(CapacityProtocolError, match="capabilities"):
        await capacity.pool.launch_for_demand(replace(capacity.policy, max_workers=3))
    async with db.context() as session:
        assert list(await session.scalars(select(ExecutionWorker.id).where(ExecutionWorker.form == "external"))) == []


async def test_provider_failure_never_ends_a_fresh_heartbeat_as_silent(db, capacity):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    uid = capacity.provider.resources[str(worker_id)]["resource_uid"]
    await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=uuid4(), version="test-worker", hostname="physical-worker",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
    )
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    async with db.context() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "ready"


async def test_starting_worker_tolerates_eventual_provider_visibility(db, capacity):
    settings = SimpleNamespace(**vars(SETTINGS), pool_provisioning_grace_seconds=60)
    pool = ExternalWorkerPoolController(capacity.client, settings, capacity.provider.profile)
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(capacity.policy)
    await pool.reconcile_worker(worker_id, capacity.policy)
    del capacity.provider.resources[str(worker_id)]

    await pool.reconcile_worker(worker_id, capacity.policy)

    async with db.context() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "starting"


@pytest.mark.parametrize("terminal_state", ["stopped", "deleted"])
async def test_starting_worker_fences_explicit_terminal_provider_state(
    db, capacity, terminal_state,
):
    settings = SimpleNamespace(**vars(SETTINGS), pool_provisioning_grace_seconds=60)
    pool = ExternalWorkerPoolController(capacity.client, settings, capacity.provider.profile)
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(capacity.policy)
    await pool.reconcile_worker(worker_id, capacity.policy)
    capacity.provider.resources[str(worker_id)]["state"] = terminal_state

    await pool.reconcile_worker(worker_id, capacity.policy)

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert (worker.state, worker.end_reason) == ("lost", "provider_resource_stopped")


async def test_start_timeout_is_absolute_during_provider_visibility_grace(db, capacity):
    settings = SimpleNamespace(
        **{
            **vars(SETTINGS),
            "pool_provisioning_grace_seconds": 3600,
            "pool_start_timeout_seconds": 300,
        }
    )
    pool = ExternalWorkerPoolController(capacity.client, settings, capacity.provider.profile)
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(capacity.policy)
    await pool.reconcile_worker(worker_id, capacity.policy)
    del capacity.provider.resources[str(worker_id)]
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        worker.created_at = controller._now() - timedelta(seconds=settings.pool_start_timeout_seconds)
        await session.commit()

    await pool.reconcile_worker(worker_id, capacity.policy)

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert (worker.state, worker.end_reason) == ("lost", "start_timeout")


async def test_lost_provider_resource_fences_its_running_work_before_cleanup(db, capacity):
    execution_id = UUID(await _accept_pool_run())
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    resource = capacity.provider.resources[str(worker_id)]
    await claims.admit_external_worker(
        worker_id, resource_uid=resource["resource_uid"], launcher_id=uuid4(), version="test-worker", hostname="physical-worker",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
    )
    await claims.heartbeat_worker(
        worker_id, active_runs=0, runs_handled=0, package_generation=0,
        memory_pressured=False, memory_budget_bytes=GIB, memory_reserved_bytes=0,
        active_handler_counts={},
    )
    assert await claims.claim_next(worker_id, fits_bytes=GIB, budget_bytes=GIB) is not None
    resource["state"] = "stopped"

    await capacity.pool.reconcile_worker(worker_id, capacity.policy)

    async with db.context() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "lost"
        assert (await session.get(Execution, execution_id)).status == ExecutionStatus.FAILED
    assert resource["state"] == "stopped", "physical cleanup follows canonical fencing"
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    assert resource["state"] == "deleted"


async def test_managed_provider_forms_share_the_worker_ceiling(db, capacity):
    async with db.context() as session:
        session.add_all([
            ExecutionWorker(form=form, state="draining", memory_limit_bytes=2 * GIB)
            for form in ("job", "external")
        ])
    await _accept_pool_run()
    assert await capacity.pool.launch_for_demand(capacity.policy) == []


async def test_external_workers_preserve_the_shared_warm_minimum(db, capacity):
    await _store_policy(db.sessions, replace(capacity.policy, warm_workers=1))
    async with db.context() as session:
        first = ExecutionWorker(form="external", state="ready")
        session.add(first)
        await session.flush()
        first_id = first.id
    assert not await claims.begin_idle_drain(first_id)
    async with db.context() as session:
        session.add(ExecutionWorker(form="external", state="ready"))
    assert await claims.begin_idle_drain(first_id)


@pytest.mark.parametrize("endpoint", [
    "http://capacity.invalid", "https://user:secret@capacity.invalid",
    "https://capacity.invalid/?token=x", "https://capacity.invalid:broken",
])
def test_unsafe_provider_urls_are_rejected(endpoint):
    with pytest.raises(ValueError, match="HTTPS"):
        ExternalCapacityClient.check_configuration(endpoint, TOKEN)


@pytest.mark.parametrize("token", ["short", "x" * 40 + "\n", "\N{LATIN SMALL LETTER E WITH ACUTE}" * 40])
def test_invalid_bearer_tokens_fail_without_echoing_the_value(token):
    with pytest.raises(ValueError) as error:
        ExternalCapacityClient.check_configuration("https://capacity.invalid", token)
    assert token not in str(error.value)


async def test_provider_redirects_never_forward_the_bearer_to_another_origin():
    requests = []

    def redirect(request):
        requests.append(request.url.host)
        return httpx.Response(307, headers={"Location": "https://other.invalid/v1/capabilities"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as http:
        client = ExternalCapacityClient("https://capacity.invalid", TOKEN, http=http)
        with pytest.raises(httpx.HTTPStatusError):
            await client.profile()
    assert requests == ["capacity.invalid"]


@pytest.mark.parametrize("terminal_state", ["lost", "retired"])
async def test_cleanup_survives_provider_outage_beyond_one_hour_and_controller_restart(
    db, capacity, monkeypatch, terminal_state,
):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    resource = capacity.provider.resources[str(worker_id)]
    ended_at = controller._now()
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        worker.state, worker.ended_at = terminal_state, ended_at
        await session.commit()
    monkeypatch.setattr(capacity.pool, "launch_for_demand", AsyncMock(return_value=[]))
    monkeypatch.setattr(capacity.pool, "replace_outdated", AsyncMock())
    capacity.provider.fail_deletions = True
    await capacity.pool.reconcile()
    later = ended_at + timedelta(hours=2)
    monkeypatch.setattr(controller, "_now", lambda: later)
    monkeypatch.setattr(external_controller, "_now", lambda: later)
    await capacity.pool.reconcile()
    deletions = [method for method, path in capacity.provider.requests
                 if method == "DELETE" and path.endswith(str(worker_id))]
    assert len(deletions) == 2, "cleanup must remain eligible throughout the outage"
    capacity.provider.fail_deletions = False
    restarted = ExternalWorkerPoolController(capacity.client, SETTINGS, capacity.provider.profile)
    monkeypatch.setattr(restarted, "launch_for_demand", AsyncMock(return_value=[]))
    monkeypatch.setattr(restarted, "replace_outdated", AsyncMock())
    await restarted.reconcile()
    assert resource["state"] == "deleted"
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.state == terminal_state
        assert worker.capacity_resource_uid == resource["resource_uid"]
        assert worker.capacity_resource_deleted_at == later
    before = sum(path.endswith(str(worker_id)) for _, path in capacity.provider.requests)
    await restarted.reconcile()
    assert sum(path.endswith(str(worker_id)) for _, path in capacity.provider.requests) == before


async def test_absence_during_delayed_prepare_does_not_confirm_cleanup(db, capacity, monkeypatch):
    await _accept_pool_run()
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    preparing, release = asyncio.Event(), asyncio.Event()
    prepare = capacity.client.prepare

    async def delayed_prepare(reservation, scope):
        preparing.set()
        await release.wait()
        return await prepare(reservation, scope)

    monkeypatch.setattr(capacity.client, "prepare", delayed_prepare)
    pending = asyncio.create_task(capacity.pool.reconcile_worker(worker_id, capacity.policy))
    try:
        await preparing.wait()
        await capacity.pool._record_loss(worker_id, None, "provider_version_changed", False)
        restarted = ExternalWorkerPoolController(capacity.client, SETTINGS, capacity.provider.profile)
        await restarted.reconcile_worker(worker_id, capacity.policy)
        async with db.context() as session:
            worker = await session.get(ExecutionWorker, worker_id)
            assert worker.capacity_resource_deleted_at is None, "absence cannot fence a delayed prepare"
    finally:
        release.set()
        await asyncio.gather(pending)

    assert capacity.provider.resources[str(worker_id)]["state"] == "prepared"
    assert capacity.provider.starts == []
    monkeypatch.setattr(restarted, "launch_for_demand", AsyncMock(return_value=[]))
    monkeypatch.setattr(restarted, "replace_outdated", AsyncMock())
    await restarted.reconcile()

    assert capacity.provider.resources[str(worker_id)]["state"] == "deleted"
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.state == "lost"
        assert worker.capacity_resource_deleted_at is not None


async def test_fixed_size_provider_measures_one_cold_worker_before_admission(db, capacity):
    from src.services import work_dispatch
    from tests.unit.services.worker_pool.test_worker_pool import MIB, _claim, _report_budget, _set_request

    capacity.provider.profile = capacity.provider.profile.model_copy(update={"memory_limit_bytes": GIB})
    capacity.pool.profile = capacity.provider.profile
    policy = replace(capacity.policy, worker_memory_mib=1024, largest_worker_mib=1024)
    await _store_policy(db.sessions, policy)
    run_id = await _accept_pool_run()
    await _set_request(db.sessions, run_id, 128 * MIB)
    other = ExternalWorkerPoolController(capacity.client, SETTINGS, capacity.provider.profile)
    results = await asyncio.gather(
        capacity.pool.launch_for_demand(policy), other.launch_for_demand(policy),
    )
    assert sorted(len(ids) for ids in results) == [0, 1]
    worker_id = next(ids[0] for ids in results if ids)
    await capacity.pool.reconcile_worker(worker_id, policy)
    assert len(capacity.provider.starts) == 1
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.memory_limit_bytes == GIB
        assert worker.reserved_for_dispatch_id is None
        assert await work_dispatch.largest_budget(session) is None
    assert await _claim(worker_id) is None
    uid = capacity.provider.resources[str(worker_id)]["resource_uid"]
    assert await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=uuid4(),
        version=capacity.provider.profile.worker_version, hostname="cold-provider-worker",
        actual_handlers=ACTUAL_HANDLERS, actual_compute=ACTUAL_COMPUTE,
    ) is not None
    await _report_budget(worker_id, 512 * MIB)
    await capacity.pool.reconcile()
    async with db.context() as session:
        assert await work_dispatch.largest_budget(session) == 512 * MIB
    assert await _claim(worker_id, fits=512 * MIB, budget=512 * MIB) is not None
