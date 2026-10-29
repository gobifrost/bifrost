"""Settings -> Kubernetes: stored policy, the one-time import, placement."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.jobs.platform.application_publish import APPLICATION_PUBLISH_DEFINITION
from src.jobs.platform.solution_deploy import (
    SOLUTION_DEPLOY_DEFINITION,
    SolutionDeployPayload,
)
from src.jobs.platform.solution_git_sync import (
    SOLUTION_GIT_SYNC_DEFINITION,
    SolutionGitSyncPayload,
)
from src.models.orm.config import SystemConfig
from src.services import kubernetes_control
from src.services import kubernetes_execution as kube
from src.services import platform_jobs as service


async def _clear(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        await session.execute(
            delete(SystemConfig).where(
                SystemConfig.category == kube.KUBERNETES_CONFIG_CATEGORY,
                SystemConfig.key == kube.KUBERNETES_CONFIG_KEY,
            )
        )
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _no_stored_settings(async_session_factory: async_sessionmaker[AsyncSession]):
    """Every test starts from an install that never stored a setting."""
    await _clear(async_session_factory)
    yield
    await _clear(async_session_factory)


async def _import(
    sessions: async_sessionmaker[AsyncSession], environ: dict[str, str]
) -> list[str]:
    async with sessions() as session:
        return await kube.import_kubernetes_policy_from_env(session, environ)


async def _stored(sessions: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    async with sessions() as session:
        return await kube._stored_payload(session)


async def _policy(sessions: async_sessionmaker[AsyncSession]) -> kube.KubernetesPolicy:
    async with sessions() as session:
        return await kube.load_kubernetes_policy(session)


async def test_an_install_that_stored_nothing_runs_on_defaults(
    db_session: AsyncSession,
) -> None:
    policy = await kube.load_kubernetes_policy(db_session)
    assert policy == kube.KubernetesPolicy()
    assert (
        policy.warm_workers,
        policy.max_workers,
        policy.worker_memory_mib,
        policy.largest_worker_mib,
        policy.idle_seconds,
        policy.isolated_jobs_enabled,
        policy.max_isolated_pods,
    ) == (1, 6, 2048, 8192, 300, False, 2)
    assert policy.enabled_job_types == kube.DEFAULT_REMOTE_JOB_TYPES
    assert policy.max_concurrency == {}


@pytest.mark.parametrize(
    ("variable", "raw", "name", "value"),
    [
        ("BIFROST_POOL_MIN_WORKERS", "2", "warm_workers", 2),
        ("BIFROST_POOL_MAX_WORKERS", "9", "max_workers", 9),
        ("BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB", "3072", "worker_memory_mib", 3072),
        ("BIFROST_POOL_WORKER_MAX_MEMORY_MIB", "16384", "largest_worker_mib", 16384),
        ("BIFROST_POOL_IDLE_SECONDS", "45", "idle_seconds", 45),
        ("BIFROST_PLATFORM_BUILD_BACKEND", "kubernetes", "isolated_jobs_enabled", True),
        ("BIFROST_PLATFORM_BUILD_BACKEND", "local", "isolated_jobs_enabled", False),
        ("BIFROST_KUBERNETES_BUILD_MAX_JOBS", "3", "max_isolated_pods", 3),
    ],
)
async def test_each_setting_imports_its_variable_once(
    async_session_factory: async_sessionmaker[AsyncSession],
    variable: str,
    raw: str,
    name: str,
    value: Any,
) -> None:
    assert await _import(async_session_factory, {variable: raw}) == [name]

    assert getattr(await _policy(async_session_factory), name) == value
    provenance = (await _stored(async_session_factory))["imported_from_env"]
    assert provenance[name]["variable"] == variable


async def test_variable_names_are_case_insensitive_and_empty_values_are_unset(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    imported = await _import(
        async_session_factory,
        {"bifrost_pool_max_workers": "4", "BIFROST_POOL_MIN_WORKERS": "  "},
    )

    assert imported == ["max_workers"]
    policy = await _policy(async_session_factory)
    assert (policy.max_workers, policy.warm_workers) == (4, 1)


async def test_an_invalid_value_is_skipped_and_the_default_applies(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    imported = await _import(
        async_session_factory,
        {
            "BIFROST_POOL_MAX_WORKERS": "many",
            "BIFROST_POOL_IDLE_SECONDS": "0",
            "BIFROST_PLATFORM_BUILD_BACKEND": "cloud",
        },
    )

    assert imported == []
    assert await _stored(async_session_factory) == {}


async def test_a_stored_value_is_never_overwritten(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        await kube.KubernetesExecutionService(session).update_policy(
            {"max_workers": 3}, updated_by="admin@example.com"
        )
        await session.commit()

    imported = await _import(
        async_session_factory,
        {"BIFROST_POOL_MAX_WORKERS": "10", "BIFROST_POOL_MIN_WORKERS": "2"},
    )

    assert imported == ["warm_workers"]
    policy = await _policy(async_session_factory)
    assert (policy.max_workers, policy.warm_workers) == (3, 2)
    # A second start finds everything stored.
    assert await _import(async_session_factory, {"BIFROST_POOL_MAX_WORKERS": "10"}) == []


async def test_a_narrowed_allowlist_stays_narrowed(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _import(
        async_session_factory,
        {
            "BIFROST_KUBERNETES_BUILD_JOB_TYPES": (
                " application.sdk_update ,,solution.deploy,application.publish"
            )
        },
    )

    policy = await _policy(async_session_factory)
    assert policy.enabled_job_types == {"application.sdk_update", "solution.deploy"}
    provenance = (await _stored(async_session_factory))["imported_from_env"]
    assert provenance["enabled_job_types"]["variable"] == "BIFROST_KUBERNETES_BUILD_JOB_TYPES"


async def test_toggles_an_admin_stored_are_not_imported(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        await kube.KubernetesExecutionService(session).set_job_type_enabled(
            "solution.export", False, updated_by="admin@example.com"
        )
        await session.commit()

    assert (
        await _import(
            async_session_factory,
            {"BIFROST_KUBERNETES_BUILD_JOB_TYPES": "application.deploy"},
        )
        == []
    )
    assert (await _policy(async_session_factory)).enabled_job_types == (
        kube.DEFAULT_REMOTE_JOB_TYPES - {"solution.export"}
    )


async def test_replicas_importing_at_once_store_once(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    environ = {
        "BIFROST_POOL_MAX_WORKERS": "8",
        "BIFROST_PLATFORM_BUILD_BACKEND": "kubernetes",
    }

    results = await asyncio.gather(
        *(_import(async_session_factory, environ) for _ in range(3))
    )

    assert sorted(results, key=len) == [
        [],
        [],
        ["max_workers", "isolated_jobs_enabled"],
    ]
    policy = await _policy(async_session_factory)
    assert (policy.max_workers, policy.isolated_jobs_enabled) == (8, True)


async def test_an_admin_edit_clears_that_fields_provenance(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _import(
        async_session_factory,
        {
            "BIFROST_POOL_MAX_WORKERS": "8",
            "BIFROST_POOL_MIN_WORKERS": "2",
            "BIFROST_KUBERNETES_BUILD_JOB_TYPES": "application.deploy",
        },
    )

    async with async_session_factory() as session:
        svc = kube.KubernetesExecutionService(session)
        await svc.update_policy({"max_workers": 5}, updated_by="admin@example.com")
        await svc.set_job_type_enabled("solution.deploy", True, updated_by="admin@example.com")
        await session.commit()

    provenance = (await _stored(async_session_factory))["imported_from_env"]
    assert set(provenance) == {"warm_workers"}


async def test_saving_a_toggle_or_concurrency_keeps_every_other_setting(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _import(
        async_session_factory,
        {"BIFROST_POOL_MAX_WORKERS": "8", "BIFROST_PLATFORM_BUILD_BACKEND": "kubernetes"},
    )
    async with async_session_factory() as session:
        svc = kube.KubernetesExecutionService(session)
        await svc.set_job_type_enabled("application.sdk_update", False, updated_by="admin")
        await svc.set_job_type_concurrency("application.deploy", 3, updated_by="admin")
        await session.commit()

    policy = await _policy(async_session_factory)
    assert (policy.max_workers, policy.isolated_jobs_enabled) == (8, True)
    assert "application.sdk_update" not in policy.enabled_job_types
    assert policy.max_concurrency == {"application.deploy": 3}
    provenance = (await _stored(async_session_factory))["imported_from_env"]
    assert set(provenance) == {"max_workers", "isolated_jobs_enabled"}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"warm_workers": 7}, "Warm workers must not exceed max workers"),
        ({"worker_memory_mib": 9000}, "Worker memory must not exceed the largest worker"),
        ({"max_isolated_pods": 0}, "max_isolated_pods must be between"),
        ({"idle_seconds": None}, "A value is required for: idle_seconds"),
        ({"warm": 1}, "Unknown settings: warm"),
    ],
)
async def test_an_invalid_policy_is_rejected_and_nothing_changes(
    db_session: AsyncSession, changes: dict[str, Any], message: str
) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    with pytest.raises(ValueError, match=message):
        await svc.update_policy(changes, updated_by="admin")
    assert await kube.load_kubernetes_policy(db_session) == kube.KubernetesPolicy()


async def test_a_consistent_change_of_both_fields_is_accepted(
    db_session: AsyncSession,
) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    await svc.update_policy(
        {"warm_workers": 10, "max_workers": 12, "isolated_jobs_enabled": True},
        updated_by="admin",
    )
    policy = await kube.load_kubernetes_policy(db_session)
    assert (policy.warm_workers, policy.max_workers, policy.isolated_jobs_enabled) == (
        10,
        12,
        True,
    )


def _memory_request(monkeypatch: pytest.MonkeyPatch, mib: int) -> None:
    monkeypatch.setattr(
        "src.config.get_settings",
        lambda: SimpleNamespace(pool_worker_memory_request_mib=mib),
    )


async def test_worker_memory_below_the_deployments_request_is_rejected(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker Job whose limit is below its request could never be created."""
    _memory_request(monkeypatch, 1024)
    svc = kube.KubernetesExecutionService(db_session)

    with pytest.raises(
        ValueError,
        match=r"Worker memory must be at least the worker memory request "
        r"\(1024 MiB, BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB\)",
    ):
        await svc.update_policy({"worker_memory_mib": 768}, updated_by="admin")
    assert await kube.load_kubernetes_policy(db_session) == kube.KubernetesPolicy()

    await svc.update_policy({"worker_memory_mib": 1024}, updated_by="admin")
    assert (await kube.load_kubernetes_policy(db_session)).worker_memory_mib == 1024


async def _admin_edits(
    sessions: async_sessionmaker[AsyncSession], changes: dict[str, Any]
) -> None:
    async with sessions() as session:
        await kube.KubernetesExecutionService(session).update_policy(
            changes, updated_by="admin@example.com"
        )
        await session.commit()


async def test_imported_worker_memory_above_the_largest_worker_raises_the_largest(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The old controller sized its largest worker max(Worker memory, largest)."""
    imported = await _import(
        async_session_factory, {"BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB": "16384"}
    )

    assert sorted(imported) == ["largest_worker_mib", "worker_memory_mib"]
    policy = await _policy(async_session_factory)
    assert (policy.worker_memory_mib, policy.largest_worker_mib) == (16384, 16384)
    provenance = (await _stored(async_session_factory))["imported_from_env"]
    assert provenance["largest_worker_mib"]["variable"] == (
        "BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB"
    )
    # Later edits are not rejected for a pair the import stored.
    await _admin_edits(async_session_factory, {"idle_seconds": 60})


async def test_imported_warm_workers_above_max_workers_are_capped(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The old controller never launched more than max workers."""
    imported = await _import(
        async_session_factory,
        {"BIFROST_POOL_MIN_WORKERS": "8", "BIFROST_POOL_MAX_WORKERS": "4"},
    )

    assert sorted(imported) == ["max_workers", "warm_workers"]
    policy = await _policy(async_session_factory)
    assert (policy.warm_workers, policy.max_workers) == (4, 4)
    provenance = (await _stored(async_session_factory))["imported_from_env"]
    assert provenance["warm_workers"]["variable"] == "BIFROST_POOL_MIN_WORKERS"
    await _admin_edits(async_session_factory, {"idle_seconds": 60})


async def test_an_import_conflicting_with_an_admin_value_is_skipped(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _admin_edits(
        async_session_factory, {"largest_worker_mib": 4096, "warm_workers": 5}
    )

    imported = await _import(
        async_session_factory,
        {
            "BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB": "16384",
            "BIFROST_POOL_MAX_WORKERS": "3",
            "BIFROST_POOL_IDLE_SECONDS": "45",
        },
    )

    assert imported == ["idle_seconds"]
    policy = await _policy(async_session_factory)
    assert (
        policy.worker_memory_mib,
        policy.largest_worker_mib,
        policy.warm_workers,
        policy.max_workers,
        policy.idle_seconds,
    ) == (2048, 4096, 5, 6, 45)


async def test_imported_worker_memory_below_the_request_is_skipped(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _memory_request(monkeypatch, 1024)

    imported = await _import(
        async_session_factory, {"BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB": "768"}
    )

    assert imported == []
    assert (await _policy(async_session_factory)).worker_memory_mib == 2048


async def test_execution_toggle_round_trip(db_session: AsyncSession) -> None:
    svc = kube.KubernetesExecutionService(db_session)

    await svc.set_job_type_enabled("application.sdk_update", False, updated_by="test")
    assert await svc.is_remote_enabled("application.sdk_update") is False
    assert await svc.is_remote_enabled("application.deploy") is True

    await svc.set_job_type_enabled("application.sdk_update", True, updated_by="test")
    assert await svc.is_remote_enabled("application.sdk_update") is True


async def test_execution_toggle_rejects_non_build_types(db_session: AsyncSession) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    with pytest.raises(KeyError):
        await svc.set_job_type_enabled("artifact.retention_cleanup", True, updated_by="test")
    with pytest.raises(KeyError):
        await svc.set_job_type_enabled("no.such.job", True, updated_by="test")


async def test_concurrency_override_round_trip(db_session: AsyncSession) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    assert await svc.effective_max_concurrency(APPLICATION_DEPLOY_DEFINITION) == 1

    await svc.set_job_type_concurrency("application.deploy", 3, updated_by="test")
    assert await svc.effective_max_concurrency(APPLICATION_DEPLOY_DEFINITION) == 3

    await svc.set_job_type_concurrency("application.deploy", None, updated_by="test")
    assert await svc.effective_max_concurrency(APPLICATION_DEPLOY_DEFINITION) == 1


async def test_concurrency_override_rejects_bad_values(db_session: AsyncSession) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    with pytest.raises(ValueError):
        await svc.set_job_type_concurrency("application.deploy", 0, updated_by="test")
    with pytest.raises(ValueError):
        await svc.set_job_type_concurrency("application.deploy", 33, updated_by="test")
    with pytest.raises(KeyError):
        await svc.set_job_type_concurrency("artifact.retention_cleanup", 1, updated_by="test")


async def test_the_page_shows_values_provenance_job_types_and_unread_variables(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    environ = {
        "BIFROST_POOL_MAX_WORKERS": "8",
        "BIFROST_MAX_CONCURRENCY": "10",
        "BIFROST_KUBERNETES_JOB_IMAGE": "registry/api:pinned",
    }
    await _import(async_session_factory, environ)

    async with async_session_factory() as session:
        page = await kube.KubernetesExecutionService(session).describe(environ)

    assert page.policy.max_workers == 8
    assert page.policy.isolated_jobs_enabled is False
    assert page.imported_from_env["max_workers"].variable == "BIFROST_POOL_MAX_WORKERS"
    assert page.unread_variables == ["BIFROST_MAX_CONCURRENCY", "BIFROST_POOL_MAX_WORKERS"]
    by_type = {item.job_type: item for item in page.job_types}
    assert set(by_type) == set(kube.DEFAULT_REMOTE_JOB_TYPES)
    assert by_type["application.deploy"].title == "App Deploys"
    assert by_type["solution.export"].title == "Solution Backup Exports"
    assert all(item.enabled and item.default_enabled for item in page.job_types)


async def test_status_reports_whether_this_api_may_manage_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for permitted in (True, False):
        monkeypatch.setattr(kubernetes_control, "_job_access", {"answer": permitted})
        assert (await kube.KubernetesExecutionService.status()).configured is permitted


async def _enqueue_deploy(db_session: AsyncSession) -> str:
    app_id = uuid4()
    job, _ = await service.enqueue_platform_job(
        db_session,
        APPLICATION_DEPLOY_DEFINITION,
        ApplicationDeployPayload(
            application_id=app_id, deployment_id=uuid4(), input_sha256="a" * 64
        ),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Deploying App",
        action_url=None,
    )
    return job.execution_backend


async def test_placement_follows_isolation_and_the_type_toggle(
    db_session: AsyncSession,
) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    assert await _enqueue_deploy(db_session) == "local"  # isolation is off by default

    await svc.update_policy({"isolated_jobs_enabled": True}, updated_by="test")
    assert await _enqueue_deploy(db_session) == "kubernetes"

    await svc.set_job_type_enabled("application.deploy", False, updated_by="test")
    assert await _enqueue_deploy(db_session) == "local"


async def test_default_class_jobs_are_never_isolated(db_session: AsyncSession) -> None:
    await kube.KubernetesExecutionService(db_session).update_policy(
        {"isolated_jobs_enabled": True}, updated_by="test"
    )
    assert (
        await kube.decide_execution_backend(db_session, APPLICATION_PUBLISH_DEFINITION)
        == "local"
    )


@pytest.mark.parametrize(
    ("definition", "payload"),
    [
        (
            SOLUTION_DEPLOY_DEFINITION,
            lambda: SolutionDeployPayload(
                deploy_job_id=uuid4(),
                kind="deploy",
                install_id=uuid4(),
                input_sha256="a" * 64,
                options={},
            ),
        ),
        (
            SOLUTION_GIT_SYNC_DEFINITION,
            lambda: SolutionGitSyncPayload(solution_id=uuid4()),
        ),
    ],
    ids=["solution-deploy", "solution-git-sync"],
)
async def test_solution_build_placement_honors_per_job_setting(
    db_session: AsyncSession, definition, payload
) -> None:
    execution = kube.KubernetesExecutionService(db_session)
    await execution.update_policy({"isolated_jobs_enabled": True}, updated_by="test")
    await execution.set_job_type_enabled(definition.job_type, False, updated_by="test")

    body = payload()
    resource_id = str(body.deploy_job_id if hasattr(body, "deploy_job_id") else body.solution_id)
    job, _ = await service.enqueue_platform_job(
        db_session,
        definition,
        body,
        dedupe_key=resource_id,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution",
        resource_id=resource_id,
        title="Solution build",
        action_url=None,
    )

    assert job.execution_backend == "local"


def test_all_build_class_policies_serialize_by_default() -> None:
    """Every isolatable job type defaults to max_concurrency=1.

    Parallelism is granted explicitly per deployment through the Settings
    override, never by accident in a new definition.
    """
    from src.jobs.platform.registry import list_platform_job_definitions

    build_types = [
        definition.job_type
        for definition in list_platform_job_definitions()
        if definition.policy.execution_class == "build"
    ]
    assert build_types, "expected at least one build-class job type"
    for definition in list_platform_job_definitions():
        if definition.policy.execution_class == "build":
            assert definition.policy.max_concurrency == 1, definition.job_type
