"""External capacity uses immutable resources and canonical Postgres admission."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from shared.models import WorkerCapacityProfile, WorkerCapacityReservation
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.enums import ExecutionStatus
from src.services.worker_pool import claims, external_controller
from src.services.worker_pool.external_client import CapacityProtocolError, ExternalCapacityClient
from src.services.worker_pool.external_controller import ExternalWorkerPoolController
from tests.unit.services.worker_pool.test_worker_pool import (
    GIB, POLICY, SETTINGS, _accept_pool_run, _clean as _clean,
    _store_policy, db as db_fixture,
)

db = db_fixture
TOKEN = "synthetic-capacity-token-" + "x" * 32


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
                resource["state"] = "deleted"
        return httpx.Response(200, json=resource)


@pytest_asyncio.fixture
async def capacity(db, monkeypatch):
    provider = Provider()
    policy = replace(POLICY, largest_worker_mib=2048)
    await _store_policy(db.sessions, policy)
    monkeypatch.setattr(external_controller, "get_db_context", db.context)
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
    ) is None
    for version in (None, "previous-worker"):
        assert await claims.admit_external_worker(
            worker_id, resource_uid=uid, launcher_id=launcher_id, version=version, hostname="wrong-build",
        ) is None
    assert await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=launcher_id, version="test-worker", hostname="physical-worker",
    ) == 0
    assert await claims.admit_external_worker(
        worker_id, resource_uid=uid, launcher_id=uuid4(), version="test-worker", hostname="replacement",
    ) is None
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.remote_launcher_id == launcher_id
        assert worker.version == "test-worker"
        assert worker.hostname == "physical-worker"


async def test_provider_rollout_fences_an_old_reservation_instead_of_relabeling_it(db, capacity):
    await _accept_pool_run()
    [old_id] = await capacity.pool.launch_for_demand(capacity.policy)
    capacity.provider.profile = capacity.provider.profile.model_copy(update={"worker_version": "next-worker"})
    capacity.pool.profile = capacity.provider.profile
    capacity.pool.version = "next-worker"
    await capacity.pool.reconcile_worker(old_id, capacity.policy)
    assert capacity.provider.resources == {}
    assert capacity.provider.starts == []
    async with db.context() as session:
        old = await session.get(ExecutionWorker, old_id)
        assert (old.state, old.version, old.end_reason) == ("lost", "test-worker", "provider_version_changed")
    [new_id] = await capacity.pool.launch_for_demand(capacity.policy)
    assert new_id != old_id
    async with db.context() as session:
        assert (await session.get(ExecutionWorker, new_id)).version == "next-worker"


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
    )
    # This controller remains alive while the external provider changes builds.
    capacity.pool.live = external_controller.LiveControllers(
        ids=frozenset({capacity.pool.id}), versions=frozenset({"next-worker"}),
    )

    assert await capacity.pool.replace_outdated(replace(capacity.policy, warm_workers=0)) == [worker_id]

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert (worker.state, worker.end_reason) == ("draining", "rotated")
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
    await claims.admit_external_worker(worker_id, resource_uid=uid, launcher_id=uuid4(), version="test-worker", hostname="physical-worker")
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    async with db.context() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "ready"


async def test_lost_provider_resource_fences_its_running_work_before_cleanup(db, capacity):
    execution_id = UUID(await _accept_pool_run())
    [worker_id] = await capacity.pool.launch_for_demand(capacity.policy)
    await capacity.pool.reconcile_worker(worker_id, capacity.policy)
    resource = capacity.provider.resources[str(worker_id)]
    await claims.admit_external_worker(
        worker_id, resource_uid=resource["resource_uid"], launcher_id=uuid4(), version="test-worker", hostname="physical-worker",
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
            ExecutionWorker(form=form, state="draining", slots=0, memory_limit_bytes=2 * GIB)
            for form in ("job", "external")
        ])
    await _accept_pool_run()
    assert await capacity.pool.launch_for_demand(capacity.policy) == []


async def test_external_workers_preserve_the_shared_warm_minimum(db, capacity):
    await _store_policy(db.sessions, replace(capacity.policy, warm_workers=1))
    async with db.context() as session:
        first = ExecutionWorker(form="external", state="ready", slots=0)
        session.add(first)
        await session.flush()
        first_id = first.id
    assert not await claims.begin_idle_drain(first_id)
    async with db.context() as session:
        session.add(ExecutionWorker(form="external", state="ready", slots=0))
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
