from datetime import datetime, timezone
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.jobs.platform.base import PlatformJobFailure


def test_sdk_update_definition_is_registered_with_expected_policy() -> None:
    from shared.models import ApplicationSdkUpdatePayloadV1
    from src.jobs.platform.application_sdk_update import (
        APPLICATION_SDK_UPDATE_DEFINITION,
        ApplicationSdkUpdatePayload,
    )
    from src.jobs.platform.registry import get_platform_job_definition

    definition = get_platform_job_definition("application.sdk_update")

    assert definition is APPLICATION_SDK_UPDATE_DEFINITION
    assert definition.job_type == "application.sdk_update"
    assert definition.payload_version == 2
    assert definition.payload_model is ApplicationSdkUpdatePayload
    assert definition.payload_model_for_version(1) is ApplicationSdkUpdatePayloadV1
    assert definition.payload_model_for_version(3) is None
    assert definition.policy.timeout_seconds == 20 * 60
    assert definition.policy.max_attempts == 1
    assert definition.policy.max_concurrency == 1
    assert definition.policy.min_memory_headroom_mb == 512


@pytest.mark.asyncio
async def test_sdk_update_resolves_source_and_uses_shared_build_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform import application_sdk_update as job
    from src.services.application_source_resolver import ResolvedApplicationSource

    app_id, old_id, new_id = uuid4(), uuid4(), uuid4()
    built_at = datetime(2026, 9, 12, tzinfo=timezone.utc)
    app = SimpleNamespace(
        id=app_id,
        name="My App",
        app_model="standalone_v2",
        solution_id=None,
        active_deployment_id=old_id,
        sdk_package_version="1.0.0",
        sdk_fingerprint="old-fp",
        sdk_contract_version=1,
        sdk_built_at=built_at,
    )
    events: list[tuple[str, object]] = []

    class DB:
        async def get(self, _model, requested_id):
            assert requested_id == app_id
            return app

    class Context:
        job_id = uuid4()

        async def report(self, message: str, *, percent: int | None = None):
            events.append(("report", (message, percent)))

    @asynccontextmanager
    async def db_context():
        yield DB()

    async def resolve_source(application):
        events.append(("resolve", application.id))
        assert application is app
        return ResolvedApplicationSource(
            files={"package.json": b"{}", "index.html": b"<div id='root'></div>"},
            dependencies={"react": "^18.3.1"},
            source_kind="independent",
        )

    async def rebuild_application(**kwargs):
        events.append(("build", kwargs))
        assert kwargs["application"] is app
        assert kwargs["deployment_id"] == new_id
        assert kwargs["enforce_expected_state"] is True
        assert kwargs["expected_active_deployment_id"] == old_id
        assert kwargs["expected_sdk_fingerprint"] == "old-fp"
        assert kwargs["source"].source_kind == "independent"
        assert kwargs["target_sdk_fingerprint"] == "target-fp"
        assert kwargs["target_sdk_contract_version"] == 3
        assert kwargs["automatic_upgrade"] is False
        assert kwargs["retain_previous_deployment"] is False
        return {"application_id": str(app_id), "deployment_id": str(new_id)}

    monkeypatch.setattr(job, "get_db_context", lambda: db_context())
    monkeypatch.setattr(job, "resolve_application_source", resolve_source)
    monkeypatch.setattr(job, "rebuild_application_from_source", rebuild_application)

    result = await job.run_application_sdk_update(
        Context(),
        job.ApplicationSdkUpdatePayload(
            application_id=app_id,
            deployment_id=new_id,
            expected_active_deployment_id=old_id,
            expected_sdk_package_version="1.0.0",
            expected_sdk_fingerprint="old-fp",
            expected_sdk_contract_version=1,
            expected_sdk_built_at=built_at,
            target_sdk_fingerprint="target-fp",
            target_sdk_contract_version=3,
        ),
    )

    assert result == {"application_id": str(app_id), "deployment_id": str(new_id)}
    assert [name for name, _ in events] == [
        "report",
        "report",
        "resolve",
        "report",
        "build",
        "report",
    ]
    assert [value for name, value in events if name == "report"] == [
        ("Loading App", None),
        ("Resolving retained source", None),
        ("Rebuilding App", None),
        ("App SDK updated", 100),
    ]


@pytest.mark.asyncio
async def test_legacy_sdk_update_uses_original_manual_build_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared.models import ApplicationSdkUpdatePayloadV1
    from src.jobs.platform import application_sdk_update as job
    from src.services.application_source_resolver import ResolvedApplicationSource

    app_id, deployment_id = uuid4(), uuid4()
    app = SimpleNamespace(id=app_id, app_model="standalone_v2", solution_id=None)
    build_args: dict[str, object] = {}

    class DB:
        async def get(self, _model, requested_id):
            assert requested_id == app_id
            return app

    class Context:
        async def report(self, *_args, **_kwargs):
            pass

    @asynccontextmanager
    async def db_context():
        yield DB()

    async def resolve_source(_application):
        return ResolvedApplicationSource(
            files={"package.json": b"{}"}, dependencies={}, source_kind="independent"
        )

    async def rebuild_application(**kwargs):
        build_args.update(kwargs)
        return {"application_id": str(app_id), "deployment_id": str(deployment_id)}

    monkeypatch.setattr(job, "get_db_context", lambda: db_context())
    monkeypatch.setattr(job, "resolve_application_source", resolve_source)
    monkeypatch.setattr(job, "rebuild_application_from_source", rebuild_application)

    await job.run_application_sdk_update(
        Context(),
        ApplicationSdkUpdatePayloadV1(
            application_id=app_id,
            deployment_id=deployment_id,
        ),
    )

    assert build_args["application"] is app
    assert build_args["deployment_id"] == deployment_id
    assert build_args["enforce_expected_state"] is True
    assert "target_sdk_fingerprint" not in build_args
    assert "target_sdk_contract_version" not in build_args
    assert "automatic_upgrade" not in build_args
    assert "retain_previous_deployment" not in build_args


def test_legacy_sdk_update_payload_rejects_automatic_target_fields() -> None:
    from pydantic import ValidationError

    from shared.models import ApplicationSdkUpdatePayloadV1

    with pytest.raises(ValidationError):
        ApplicationSdkUpdatePayloadV1(
            application_id=uuid4(),
            automatic_upgrade=True,
            target_sdk_fingerprint="target-fingerprint",
            target_sdk_contract_version=3,
        )


@pytest.mark.asyncio
async def test_sdk_update_source_unavailable_is_platform_job_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform import application_sdk_update as job
    from src.services.application_source_resolver import ApplicationSourceUnavailable

    app_id, deployment_id = uuid4(), uuid4()
    app = SimpleNamespace(id=app_id, app_model="standalone_v2", solution_id=None)

    class DB:
        async def get(self, *_args):
            return app

    class Context:
        job_id = uuid4()

        async def report(self, *_args, **_kwargs):
            pass

    @asynccontextmanager
    async def db_context():
        yield DB()

    async def resolve_source(_application):
        raise ApplicationSourceUnavailable("source_unavailable", "Retained source missing.")

    monkeypatch.setattr(job, "get_db_context", lambda: db_context())
    monkeypatch.setattr(job, "resolve_application_source", resolve_source)

    with pytest.raises(PlatformJobFailure) as exc:
        await job.run_application_sdk_update(
            Context(),
            job.ApplicationSdkUpdatePayload(
                application_id=app_id,
                deployment_id=deployment_id,
                target_sdk_fingerprint="target-fp",
                target_sdk_contract_version=3,
            ),
        )

    assert exc.value.code == "source_unavailable"
    assert "Retained source missing" in exc.value.message


@pytest.mark.asyncio
async def test_shared_rebuild_preserves_active_on_failure_and_cleans_new_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import application_build
    from src.services.application_source_resolver import ResolvedApplicationSource

    app_id, old_id, new_id = uuid4(), uuid4(), uuid4()
    app = SimpleNamespace(
        id=app_id,
        solution_id=None,
        app_model="standalone_v2",
        active_deployment_id=old_id,
        deployed_at=None,
        sdk_package_version="old",
        sdk_fingerprint="old-fp",
        sdk_contract_version=1,
        sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    events: list[tuple[str, object]] = []

    class Builder:
        def compile_dist(self, *_args):
            return {"index.html": b"built"}

        async def upload_deployment(self, *_args):
            events.append(("upload", None))
            raise RuntimeError("upload failed")

        async def delete_deployment(self, application_id, deployment_id):
            events.append(("delete_dist", (application_id, deployment_id)))

    class SourceArtifacts:
        async def delete_deployment_source(self, application_id, deployment_id):
            events.append(("delete_source", (application_id, deployment_id)))

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="1.2.3",
            fingerprint="new-fp",
            contract_version=7,
        ),
    )

    with pytest.raises(RuntimeError, match="upload failed"):
        await application_build.rebuild_application_from_source(
            application=app,
            deployment_id=new_id,
            source=ResolvedApplicationSource(
                files={"package.json": b"{}", "index.html": b"<div id='root'></div>"},
                dependencies={},
                source_kind="independent",
            ),
        )

    assert app.active_deployment_id == old_id
    assert app.sdk_fingerprint == "old-fp"
    assert events == [
        ("upload", None),
        ("delete_source", (app_id, new_id)),
        ("delete_dist", (app_id, new_id)),
    ]


@pytest.mark.asyncio
async def test_shared_rebuild_does_not_persist_solution_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import application_build
    from src.services.application_source_resolver import ResolvedApplicationSource

    app_id, old_id, new_id = uuid4(), uuid4(), uuid4()
    solution_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        solution_id=solution_id,
        app_model="standalone_v2",
        active_deployment_id=old_id,
        deployed_at=None,
        sdk_package_version=None,
        sdk_fingerprint=None,
        sdk_contract_version=None,
        sdk_built_at=None,
    )
    events: list[tuple[str, object]] = []

    class Builder:
        def compile_dist(self, *_args):
            return {"index.html": b"built"}

        async def upload_deployment(self, application_id, deployment_id, dist):
            events.append(("upload", (application_id, deployment_id, dist)))

        async def delete_deployment(self, application_id, deployment_id):
            events.append(("delete_dist", (application_id, deployment_id)))

    class SourceArtifacts:
        async def write_deployment_source(self, *_args):
            raise AssertionError("Solution source must stay in the Solution archive")

        async def delete_deployment_source(self, application_id, deployment_id):
            events.append(("delete_source", (application_id, deployment_id)))

    class DB:
        async def execute(self, _stmt):
            events.append(("core_update", app.active_deployment_id))
            app.active_deployment_id = new_id
            app.sdk_fingerprint = "new-fp"
            return SimpleNamespace(rowcount=1)

        async def flush(self):
            events.append(("flush", app.active_deployment_id))

    @asynccontextmanager
    async def db_context():
        yield DB()

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )
    monkeypatch.setattr(application_build, "get_db_context", lambda: db_context())
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="1.2.3",
            fingerprint="new-fp",
            contract_version=7,
        ),
    )

    result = await application_build.rebuild_application_from_source(
        application=app,
        deployment_id=new_id,
        source=ResolvedApplicationSource(
            files={"package.json": b"{}", "index.html": b"<div id='root'></div>"},
            dependencies={},
            source_kind="solution",
        ),
    )

    assert result["deployment_id"] == str(new_id)
    assert app.active_deployment_id == new_id
    assert app.sdk_fingerprint == "new-fp"
    assert ("delete_dist", (app_id, old_id)) in events
    assert ("delete_source", (app_id, old_id)) not in events


@pytest.mark.asyncio
async def test_shared_rebuild_requires_fresh_activation_row_and_cleans_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import application_build
    from src.services.application_source_resolver import ResolvedApplicationSource

    app_id, old_id, new_id = uuid4(), uuid4(), uuid4()
    deleted_app = SimpleNamespace(
        id=app_id,
        solution_id=None,
        app_model="standalone_v2",
        active_deployment_id=old_id,
        deployed_at=None,
        sdk_package_version="old",
        sdk_fingerprint="old-fp",
        sdk_contract_version=1,
        sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    events: list[tuple[str, object]] = []

    class Builder:
        def compile_dist(self, *_args):
            return {"index.html": b"built"}

        async def upload_deployment(self, application_id, deployment_id, _dist):
            events.append(("upload", (application_id, deployment_id)))

        async def delete_deployment(self, application_id, deployment_id):
            events.append(("delete_dist", (application_id, deployment_id)))

    class SourceArtifacts:
        async def write_deployment_source(self, application_id, deployment_id, _path):
            events.append(("write_source", (application_id, deployment_id)))

        async def delete_deployment_source(self, application_id, deployment_id):
            events.append(("delete_source", (application_id, deployment_id)))

    class DB:
        async def get(self, _model, requested_id):
            assert requested_id == app_id
            return None

        async def flush(self):
            raise AssertionError("missing activation row must not be flushed")

    @asynccontextmanager
    async def db_context():
        yield DB()

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )
    monkeypatch.setattr(application_build, "get_db_context", lambda: db_context())
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="1.2.3",
            fingerprint="new-fp",
            contract_version=7,
        ),
    )

    with pytest.raises(PlatformJobFailure) as exc:
        await application_build.rebuild_application_from_source(
            application=deleted_app,
            deployment_id=new_id,
            source=ResolvedApplicationSource(
                files={"package.json": b"{}", "index.html": b"<div id='root'></div>"},
                dependencies={},
                source_kind="independent",
            ),
        )

    assert exc.value.code == "app_not_deployable"
    assert deleted_app.active_deployment_id == old_id
    assert events == [
        ("upload", (app_id, new_id)),
        ("write_source", (app_id, new_id)),
        ("delete_source", (app_id, new_id)),
        ("delete_dist", (app_id, new_id)),
    ]


@pytest.mark.asyncio
async def test_shared_rebuild_activates_solution_app_with_core_update(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.models.orm.applications import Application
    from src.models.orm.solutions import Solution
    from src.services import application_build
    from src.services.application_source_resolver import ResolvedApplicationSource
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    app_id, old_id, new_id = uuid4(), uuid4(), uuid4()
    solution = Solution(id=uuid4(), slug="sdk-solution", name="SDK Solution")
    db_session.add(solution)
    await db_session.flush()
    app = Application(
        id=app_id,
        name="Managed App",
        slug="managed-app",
        repo_path="apps/managed-app",
        solution_id=solution.id,
        app_model="standalone_v2",
        active_deployment_id=old_id,
        sdk_package_version="old",
        sdk_fingerprint="old-fp",
        sdk_contract_version=1,
        sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(app)
    await db_session.commit()

    events: list[tuple[str, object]] = []

    class Builder:
        def compile_dist(self, *_args):
            return {"index.html": b"built"}

        async def upload_deployment(self, application_id, deployment_id, _dist):
            events.append(("upload", (application_id, deployment_id)))

        async def delete_deployment(self, application_id, deployment_id):
            events.append(("delete_dist", (application_id, deployment_id)))

    class SourceArtifacts:
        async def delete_deployment_source(self, application_id, deployment_id):
            events.append(("delete_source", (application_id, deployment_id)))

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )

    @asynccontextmanager
    async def db_context():
        yield db_session

    monkeypatch.setattr(application_build, "get_db_context", lambda: db_context())
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="1.2.3",
            fingerprint="new-fp",
            contract_version=7,
        ),
    )

    result = await application_build.rebuild_application_from_source(
        application_id=app_id,
        deployment_id=new_id,
        source=ResolvedApplicationSource(
            files={"package.json": b"{}", "index.html": b"<div id='root'></div>"},
            dependencies={},
            source_kind="solution",
        ),
    )

    row = (
        await db_session.execute(
            select(Application.active_deployment_id, Application.sdk_fingerprint).where(
                Application.id == app_id
            )
        )
    ).one()
    assert result["deployment_id"] == str(new_id)
    assert row == (new_id, "new-fp")
    assert ("delete_dist", (app_id, old_id)) in events


@pytest.mark.asyncio
async def test_shared_rebuild_stale_solution_pointer_cleans_new_artifacts(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.models.orm.applications import Application
    from src.models.orm.solutions import Solution
    from src.services import application_build
    from src.services.application_source_resolver import ResolvedApplicationSource
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    app_id, old_id, raced_id, new_id = uuid4(), uuid4(), uuid4(), uuid4()
    solution = Solution(id=uuid4(), slug="stale-solution", name="Stale Solution")
    db_session.add(solution)
    await db_session.flush()
    app = Application(
        id=app_id,
        name="Managed App",
        slug="stale-managed-app",
        repo_path="apps/stale-managed-app",
        solution_id=solution.id,
        app_model="standalone_v2",
        active_deployment_id=raced_id,
        sdk_package_version="old",
        sdk_fingerprint="old-fp",
        sdk_contract_version=1,
        sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(app)
    await db_session.commit()

    events: list[tuple[str, object]] = []

    class Builder:
        def compile_dist(self, *_args):
            return {"index.html": b"built"}

        async def upload_deployment(self, application_id, deployment_id, _dist):
            events.append(("upload", (application_id, deployment_id)))

        async def delete_deployment(self, application_id, deployment_id):
            events.append(("delete_dist", (application_id, deployment_id)))

    class SourceArtifacts:
        async def delete_deployment_source(self, application_id, deployment_id):
            events.append(("delete_source", (application_id, deployment_id)))

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )

    @asynccontextmanager
    async def db_context():
        yield db_session

    monkeypatch.setattr(application_build, "get_db_context", lambda: db_context())
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="1.2.3",
            fingerprint="new-fp",
            contract_version=7,
        ),
    )

    with pytest.raises(PlatformJobFailure) as exc:
        await application_build.rebuild_application_from_source(
            application_id=app_id,
            deployment_id=new_id,
            source=ResolvedApplicationSource(
                files={"package.json": b"{}", "index.html": b"<div id='root'></div>"},
                dependencies={},
                source_kind="solution",
            ),
            expected_active_deployment_id=old_id,
            expected_sdk_package_version="old",
            expected_sdk_fingerprint="old-fp",
            expected_sdk_contract_version=1,
            expected_sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            enforce_expected_state=True,
        )

    active_id = await db_session.scalar(
        select(Application.active_deployment_id).where(Application.id == app_id)
    )
    assert exc.value.code == "app_sdk_update_stale"
    assert active_id == raced_id
    assert ("delete_dist", (app_id, new_id)) in events
