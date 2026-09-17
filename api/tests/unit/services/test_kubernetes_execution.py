"""Kubernetes execution settings: defaults, toggles, placement gates."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.services import kubernetes_execution as kube
from src.services import platform_jobs as service


def _settings(**overrides):
    base = dict(
        platform_build_backend="kubernetes",
        kubernetes_build_namespace="builds",
        kubernetes_build_image="img",
        kubernetes_build_configmap="cm",
        kubernetes_build_secret="sec",
        kubernetes_build_service_account="sa",
        kubernetes_build_job_types="application.deploy,application.sdk_update",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_status_reports_deployment_configuration() -> None:
    assert kube.is_kubernetes_configured(_settings()) is True
    assert (
        kube.is_kubernetes_configured(
            _settings(platform_build_backend="local")
        )
        is False
    )
    assert (
        kube.is_kubernetes_configured(_settings(kubernetes_build_image=""))
        is False
    )


@pytest.mark.asyncio
async def test_execution_defaults_enable_measured_jobs(
    db_session: AsyncSession,
) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    assert await svc.enabled_job_types() == kube.DEFAULT_REMOTE_JOB_TYPES
    assert await svc.is_remote_enabled("application.deploy") is True
    assert await svc.is_remote_enabled("application.publish") is False


@pytest.mark.asyncio
async def test_execution_toggle_round_trip(db_session: AsyncSession) -> None:
    svc = kube.KubernetesExecutionService(db_session)

    await svc.set_job_type_enabled(
        "application.sdk_update", False, updated_by="test"
    )
    assert await svc.is_remote_enabled("application.sdk_update") is False
    assert await svc.is_remote_enabled("application.deploy") is True

    await svc.set_job_type_enabled(
        "application.sdk_update", True, updated_by="test"
    )
    assert await svc.is_remote_enabled("application.sdk_update") is True
    await db_session.commit()


@pytest.mark.asyncio
async def test_execution_toggle_rejects_non_build_types(
    db_session: AsyncSession,
) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    with pytest.raises(KeyError):
        await svc.set_job_type_enabled(
            "artifact.retention_cleanup", True, updated_by="test"
        )
    with pytest.raises(KeyError):
        await svc.set_job_type_enabled("no.such.job", True, updated_by="test")


@pytest.mark.asyncio
async def test_execution_list_reports_both_gates(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service, "get_settings", _settings
    )
    svc = kube.KubernetesExecutionService(db_session)
    settings = await svc.list_job_types(_settings())

    by_type = {item.job_type: item for item in settings.job_types}
    assert set(by_type) == {"application.deploy", "application.sdk_update"}
    assert by_type["application.deploy"].enabled is True
    assert by_type["application.deploy"].default_enabled is True
    assert by_type["application.deploy"].allowed_by_deployment is True
    assert by_type["application.deploy"].title == "App deploys"


@pytest.mark.asyncio
async def test_placement_requires_all_three_gates(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend + allowlist + UI toggle must agree for remote placement."""
    monkeypatch.setattr(
        service, "get_settings", _settings
    )

    async def _enqueue() -> str:
        app_id = uuid4()
        job, _ = await service.enqueue_platform_job(
            db_session,
            APPLICATION_DEPLOY_DEFINITION,
            ApplicationDeployPayload(
                application_id=app_id,
                deployment_id=uuid4(),
                input_sha256="a" * 64,
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

    assert await _enqueue() == "kubernetes"

    svc = kube.KubernetesExecutionService(db_session)
    await svc.set_job_type_enabled(
        "application.deploy", False, updated_by="test"
    )
    await db_session.commit()
    assert await _enqueue() == "local"

    # Restore the default so later suites see a pristine opt-in state.
    await svc.set_job_type_enabled(
        "application.deploy", True, updated_by="test"
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_concurrency_override_round_trip(
    db_session: AsyncSession,
) -> None:
    from src.jobs.platform.application_deploy import (
        APPLICATION_DEPLOY_DEFINITION,
    )

    svc = kube.KubernetesExecutionService(db_session)
    assert (
        await svc.effective_max_concurrency(APPLICATION_DEPLOY_DEFINITION)
        == 1
    )

    await svc.set_job_type_concurrency(
        "application.deploy", 3, updated_by="test"
    )
    assert (
        await svc.effective_max_concurrency(APPLICATION_DEPLOY_DEFINITION)
        == 3
    )

    await svc.set_job_type_concurrency(
        "application.deploy", None, updated_by="test"
    )
    assert (
        await svc.effective_max_concurrency(APPLICATION_DEPLOY_DEFINITION)
        == 1
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_concurrency_override_rejects_bad_values(
    db_session: AsyncSession,
) -> None:
    svc = kube.KubernetesExecutionService(db_session)
    with pytest.raises(ValueError):
        await svc.set_job_type_concurrency(
            "application.deploy", 0, updated_by="test"
        )
    with pytest.raises(ValueError):
        await svc.set_job_type_concurrency(
            "application.deploy", 33, updated_by="test"
        )
    with pytest.raises(KeyError):
        await svc.set_job_type_concurrency(
            "artifact.retention_cleanup", 1, updated_by="test"
        )


def _k8s_settings(configured: bool):
    if not configured:
        return SimpleNamespace(platform_build_backend="local")
    return SimpleNamespace(
        platform_build_backend="kubernetes",
        kubernetes_build_namespace="builds",
        kubernetes_build_image="img",
        kubernetes_build_configmap="cm",
        kubernetes_build_secret="sec",
        kubernetes_build_service_account="sa",
    )


@pytest.mark.asyncio
async def test_detection_announces_each_transition_once(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from sqlalchemy import delete

    from src.models.orm.config import SystemConfig

    await db_session.execute(
        delete(SystemConfig).where(
            SystemConfig.category == kube.KUBERNETES_CONFIG_CATEGORY
        )
    )
    await db_session.commit()

    notifier = AsyncMock()
    notifier.find_admin_notification_by_title.return_value = None
    monkeypatch.setattr(
        "src.services.notification_service.get_notification_service",
        lambda: notifier,
    )

    # Fresh install, never configured: silent, but records the snapshot.
    monkeypatch.setattr(
        kube, "get_settings", partial(_k8s_settings, False)
    )
    assert (
        await kube.announce_detection_if_transitioned(db_session) is None
    )
    await db_session.commit()
    assert notifier.create_notification.await_count == 0

    # First enablement: exactly one notice with explainer metadata.
    monkeypatch.setattr(kube, "get_settings", partial(_k8s_settings, True))
    assert (
        await kube.announce_detection_if_transitioned(db_session)
        == "enabled"
    )
    await db_session.commit()
    assert notifier.create_notification.await_count == 1
    request = notifier.create_notification.await_args.kwargs["request"]
    assert request.title == "Kubernetes execution enabled"
    assert (
        request.metadata["action_url"] == "/settings/kubernetes-executions"
    )
    assert (
        request.metadata["details"]["primary_url"]
        == "/settings/kubernetes-executions"
    )
    assert len(request.metadata["details"]["paragraphs"]) == 3

    # Same state on next startup: silent (dismiss parks until next flip).
    assert (
        await kube.announce_detection_if_transitioned(db_session) is None
    )
    await db_session.commit()
    assert notifier.create_notification.await_count == 1

    # Disablement: exactly one notice the other way.
    monkeypatch.setattr(
        kube, "get_settings", partial(_k8s_settings, False)
    )
    assert (
        await kube.announce_detection_if_transitioned(db_session)
        == "disabled"
    )
    await db_session.commit()
    assert notifier.create_notification.await_count == 2

    # Cleanup so later suites see a pristine state.
    await db_session.execute(
        delete(SystemConfig).where(
            SystemConfig.category == kube.KUBERNETES_CONFIG_CATEGORY
        )
    )
    await db_session.commit()


def test_all_build_class_policies_serialize_by_default() -> None:
    """Every remotely-eligible job type defaults to max_concurrency=1.

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
            assert definition.policy.max_concurrency == 1, (
                definition.job_type
            )
