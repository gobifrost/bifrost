import subprocess
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.app_build_errors import AppBuildMemoryError
from src.jobs.platform.application_deploy import (
    ApplicationDeployPayload,
    run_application_deploy,
)
from src.jobs.platform.base import PlatformJobFailure
from src.services.application_source_archive import (
    InvalidApplicationSource,
    read_application_source_zip,
)


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_deploy_source_requires_vite_root(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "source.zip", {"src/main.tsx": b"export {}"})
    with pytest.raises(InvalidApplicationSource, match="package.json and index.html"):
        read_application_source_zip(archive)


def test_deploy_source_is_read_without_persisting_a_tree(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>", "src/main.tsx": b"x"},
    )
    assert read_application_source_zip(archive)["src/main.tsx"] == b"x"
    assert not (tmp_path / "src").exists()


def test_deploy_source_is_sanitized_for_build_and_retention(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "source.zip",
        {
            "package.json": b"{}",
            "index.html": b"<div id='root'>",
            "src/main.tsx": b"x",
            "node_modules/pkg/index.js": b"bad",
            "src/node_modules/pkg/index.js": b"bad",
            "dist/index.html": b"old",
            "packages/app/dist/index.html": b"old",
            "build/app.js": b"old",
            ".vite/meta.json": b"old",
            "src/.vite/meta.json": b"old",
            ".git/config": b"bad",
            ".next/server.js": b"old",
            "apps/web/.next/server.js": b"old",
            ".turbo/cache": b"old",
            "coverage/report.json": b"old",
            ".cache/tool": b"old",
            "out/index.html": b"old",
            ".env": b"SECRET=1",
            ".env.local": b"SECRET=2",
            "packages/app/.env.local": b"SECRET=3",
        },
    )

    files = read_application_source_zip(archive)

    assert sorted(files) == ["index.html", "package.json", "src/main.tsx"]


def test_deploy_source_rejects_traversal(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "source.zip", {"package.json": b"{}", "../x": b"bad"})

    with pytest.raises(InvalidApplicationSource, match="Unsafe path"):
        read_application_source_zip(archive)


def test_deploy_source_rejects_duplicate_normalized_members(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("package.json", b"{}")
        zf.writestr("index.html", b"<div id='root'>")
        zf.writestr("src/./main.tsx", b"first")
        zf.writestr("src/main.tsx", b"second")

    with pytest.raises(InvalidApplicationSource, match="Duplicate path"):
        read_application_source_zip(archive)


@pytest.mark.parametrize(
    "read_error", [RuntimeError("read failed"), zipfile.BadZipFile("bad crc")]
)
def test_deploy_source_translates_member_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_error: Exception
) -> None:
    class Info:
        filename = "package.json"
        file_size = 2

        def is_dir(self) -> bool:
            return False

    class Archive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def infolist(self):
            return [Info()]

        def read(self, _info):
            raise read_error

    monkeypatch.setattr("src.services.application_source_archive.zipfile.ZipFile", Archive)

    with pytest.raises(InvalidApplicationSource, match="valid zip file"):
        read_application_source_zip(tmp_path / "source.zip")


@pytest.mark.asyncio
async def test_deploy_atomically_activates_then_removes_old_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>"},
    )
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

    class Storage:
        def __init__(self, _job_id):
            pass

        async def copy_to_path(self, path: Path, *, expected_sha256: str) -> None:
            events.append(("copy", expected_sha256))
            path.write_bytes(source.read_bytes())

        async def delete(self) -> None:
            events.append(("delete_source", None))

    class Builder:
        def compile_dist(self, application_id, source_files, _dependencies):
            events.append(("compile", application_id))
            assert "package.json" in source_files
            return {"index.html": b"built"}

        async def upload_deployment(self, application_id, deployment_id, dist):
            events.append(("upload", (application_id, deployment_id, dist)))

        async def delete_deployment(self, application_id, deployment_id):
            events.append(("delete_artifact", (application_id, deployment_id)))

    class SourceArtifacts:
        async def write_deployment_source(self, application_id, deployment_id, path):
            events.append(("retain_source", (application_id, deployment_id)))
            with zipfile.ZipFile(path) as archive:
                assert archive.namelist() == ["index.html", "package.json"]

        async def delete_deployment_source(self, application_id, deployment_id):
            events.append(("delete_retained_source", (application_id, deployment_id)))

    class DB:
        async def get(self, _model, requested_id):
            assert requested_id == app_id
            return app

        async def flush(self):
            events.append(("flush", app.active_deployment_id))

    @asynccontextmanager
    async def db_context():
        yield DB()

    class Context:
        job_id = uuid4()

        async def report(self, message: str, *, percent: int):
            events.append(("report", (message, percent)))

    real_to_thread = __import__("asyncio").to_thread

    async def tracked_to_thread(func, *args, **kwargs):
        events.append(("to_thread", getattr(func, "__name__", type(func).__name__)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.ApplicationDeployStorage", Storage
    )
    monkeypatch.setattr(
        "src.services.application_build.SolutionAppBuilder", Builder
    )
    monkeypatch.setattr(
        "src.services.application_build.ApplicationSourceArtifactStorage",
        SourceArtifacts,
    )
    monkeypatch.setattr(
        "src.services.application_build.get_db_context", db_context
    )
    monkeypatch.setattr(
        "src.services.application_build.current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="1.2.3",
            fingerprint="current-fp",
            contract_version=7,
        ),
    )
    monkeypatch.setattr(
        "src.services.application_build.asyncio.to_thread", tracked_to_thread
    )

    result = await run_application_deploy(
        Context(),
        ApplicationDeployPayload(
            application_id=app_id,
            deployment_id=new_id,
            input_sha256="sha",
        ),
    )

    assert result["deployment_id"] == str(new_id)
    assert app.active_deployment_id == new_id
    assert app.deployed_at is not None
    assert app.sdk_package_version == "1.2.3"
    assert app.sdk_fingerprint == "current-fp"
    assert app.sdk_contract_version == 7
    assert app.sdk_built_at is not None
    assert ("delete_artifact", (app_id, old_id)) in events
    assert ("delete_retained_source", (app_id, old_id)) in events
    assert ("delete_artifact", (app_id, new_id)) not in events
    assert ("delete_retained_source", (app_id, new_id)) not in events
    assert events[-1] == ("delete_source", None)
    assert events.index(("upload", (app_id, new_id, {"index.html": b"built"}))) < events.index(
        ("retain_source", (app_id, new_id))
    )
    assert events.index(("retain_source", (app_id, new_id))) < events.index(
        ("flush", new_id)
    )
    assert events.index(("flush", new_id)) < events.index(
        ("delete_artifact", (app_id, old_id))
    )
    assert events.index(("to_thread", "prepare_application_source_archive")) < events.index(
        ("compile", app_id)
    )


@pytest.mark.asyncio
async def test_failed_build_keeps_active_artifact_and_cleans_transient_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>"},
    )
    app_id, old_id, new_id = uuid4(), uuid4(), uuid4()
    app = SimpleNamespace(active_deployment_id=old_id)
    deleted: list[tuple[str, object]] = []

    class Storage:
        def __init__(self, _job_id):
            pass

        async def copy_to_path(self, path: Path, *, expected_sha256: str) -> None:
            path.write_bytes(source.read_bytes())

        async def delete(self) -> None:
            deleted.append(("source", None))

    class Builder:
        def compile_dist(self, *_args, **_kwargs):
            raise RuntimeError("broken build")

        async def delete_deployment(self, application_id, deployment_id):
            deleted.append(("artifact", (application_id, deployment_id)))

    class SourceArtifacts:
        async def delete_deployment_source(self, application_id, deployment_id):
            deleted.append(("retained_source", (application_id, deployment_id)))

    class Context:
        job_id = uuid4()

        async def report(self, _message: str, *, percent: int):
            pass

    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.ApplicationDeployStorage", Storage
    )
    monkeypatch.setattr(
        "src.services.application_build.SolutionAppBuilder", Builder
    )
    monkeypatch.setattr(
        "src.services.application_build.ApplicationSourceArtifactStorage",
        SourceArtifacts,
    )

    with pytest.raises(PlatformJobFailure, match="broken build"):
        await run_application_deploy(
            Context(),
            ApplicationDeployPayload(
                application_id=app_id,
                deployment_id=new_id,
                input_sha256="sha",
            ),
        )

    assert app.active_deployment_id == old_id
    assert deleted == [
        ("retained_source", (app_id, new_id)),
        ("artifact", (app_id, new_id)),
        ("source", None),
    ]


@pytest.mark.asyncio
async def test_memory_failed_build_surfaces_nonretryable_memory_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The independent-App platform job keeps the actionable OOM guidance."""
    source = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>"},
    )
    app_id, deployment_id = uuid4(), uuid4()

    class Storage:
        def __init__(self, _job_id):
            pass

        async def copy_to_path(self, path: Path, *, expected_sha256: str) -> None:
            path.write_bytes(source.read_bytes())

        async def delete(self) -> None:
            pass

    class Builder:
        def compile_dist(self, *_args, **_kwargs):
            raise AppBuildMemoryError()

        async def delete_deployment(self, *_args, **_kwargs) -> None:
            pass

    class SourceArtifacts:
        async def delete_deployment_source(self, *_args, **_kwargs) -> None:
            pass

    class Context:
        job_id = uuid4()

        async def report(self, _message: str, *, percent: int) -> None:
            pass

    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.ApplicationDeployStorage", Storage
    )
    monkeypatch.setattr("src.services.application_build.SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        "src.services.application_build.ApplicationSourceArtifactStorage",
        SourceArtifacts,
    )

    with pytest.raises(PlatformJobFailure) as failure:
        await run_application_deploy(
            Context(),
            ApplicationDeployPayload(
                application_id=app_id,
                deployment_id=deployment_id,
                input_sha256="sha",
            ),
        )

    assert failure.value.code == "memory_pressure"
    assert failure.value.retryable is False
    assert "increase the build container's memory limit" in failure.value.message


@pytest.mark.asyncio
async def test_sdk_metadata_oom_surfaces_nonretryable_memory_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>"},
    )
    app_id, deployment_id = uuid4(), uuid4()

    class Storage:
        def __init__(self, _job_id):
            pass

        async def copy_to_path(self, path: Path, *, expected_sha256: str) -> None:
            path.write_bytes(source.read_bytes())

        async def delete(self) -> None:
            pass

    class Builder:
        def compile_dist(self, *_args, **_kwargs):
            raise AssertionError("build must not start after SDK metadata OOM")

        async def delete_deployment(self, *_args, **_kwargs) -> None:
            pass

    class SourceArtifacts:
        async def delete_deployment_source(self, *_args, **_kwargs) -> None:
            pass

    class Context:
        job_id = uuid4()

        async def report(self, _message: str, *, percent: int) -> None:
            pass

    def metadata_oom():
        raise subprocess.CalledProcessError(
            1,
            ["npx", "esbuild"],
            stderr=b"FATAL ERROR: JavaScript heap out of memory",
        )

    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.ApplicationDeployStorage", Storage
    )
    monkeypatch.setattr("src.services.application_build.SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        "src.services.application_build.ApplicationSourceArtifactStorage",
        SourceArtifacts,
    )
    monkeypatch.setattr(
        "src.services.application_build.current_sdk_metadata", metadata_oom
    )

    with pytest.raises(PlatformJobFailure) as failure:
        await run_application_deploy(
            Context(),
            ApplicationDeployPayload(
                application_id=app_id,
                deployment_id=deployment_id,
                input_sha256="sha",
            ),
        )

    assert failure.value.code == "memory_pressure"
    assert failure.value.retryable is False
    assert "increase the build container's memory limit" in failure.value.message


@pytest.mark.asyncio
async def test_retained_source_failure_preserves_old_pointer_and_provenance_and_cleans_new_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>"},
    )
    app_id, old_id, new_id = uuid4(), uuid4(), uuid4()
    old_built_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    app = SimpleNamespace(
        id=app_id,
        solution_id=None,
        app_model="standalone_v2",
        active_deployment_id=old_id,
        deployed_at=None,
        sdk_package_version="old",
        sdk_fingerprint="old-fp",
        sdk_contract_version=1,
        sdk_built_at=old_built_at,
    )
    events: list[tuple[str, object]] = []

    class Storage:
        def __init__(self, _job_id):
            pass

        async def copy_to_path(self, path: Path, *, expected_sha256: str) -> None:
            path.write_bytes(source.read_bytes())

        async def delete(self) -> None:
            events.append(("delete_source", None))

    class Builder:
        def compile_dist(self, *_args):
            return {"index.html": b"built"}

        async def upload_deployment(self, application_id, deployment_id, dist):
            events.append(("upload", (application_id, deployment_id, dist)))

        async def delete_deployment(self, application_id, deployment_id):
            events.append(("delete_artifact", (application_id, deployment_id)))

    class SourceArtifacts:
        async def write_deployment_source(self, application_id, deployment_id, path):
            events.append(("retain_source", (application_id, deployment_id)))
            raise RuntimeError("source store down")

        async def delete_deployment_source(self, application_id, deployment_id):
            events.append(("delete_retained_source", (application_id, deployment_id)))

    class Context:
        job_id = uuid4()

        async def report(self, _message: str, *, percent: int):
            pass

    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.ApplicationDeployStorage", Storage
    )
    monkeypatch.setattr(
        "src.services.application_build.SolutionAppBuilder", Builder
    )
    monkeypatch.setattr(
        "src.services.application_build.ApplicationSourceArtifactStorage",
        SourceArtifacts,
    )

    with pytest.raises(RuntimeError, match="source store down"):
        await run_application_deploy(
            Context(),
            ApplicationDeployPayload(
                application_id=app_id,
                deployment_id=new_id,
                input_sha256="sha",
            ),
        )

    assert app.active_deployment_id == old_id
    assert app.sdk_package_version == "old"
    assert app.sdk_fingerprint == "old-fp"
    assert app.sdk_contract_version == 1
    assert app.sdk_built_at == old_built_at
    assert events == [
        ("upload", (app_id, new_id, {"index.html": b"built"})),
        ("retain_source", (app_id, new_id)),
        ("delete_retained_source", (app_id, new_id)),
        ("delete_artifact", (app_id, new_id)),
        ("delete_source", None),
    ]
