import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.jobs.platform.application_deploy import (
    ApplicationDeployPayload,
    _read_source_zip,
    run_application_deploy,
)
from src.jobs.platform.base import PlatformJobFailure


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_deploy_source_requires_vite_root(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "source.zip", {"src/main.tsx": b"export {}"})
    with pytest.raises(PlatformJobFailure, match="package.json and index.html"):
        _read_source_zip(archive)


def test_deploy_source_is_read_without_persisting_a_tree(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>", "src/main.tsx": b"x"},
    )
    assert _read_source_zip(archive)["src/main.tsx"] == b"x"
    assert not (tmp_path / "src").exists()


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

    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.ApplicationDeployStorage", Storage
    )
    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.SolutionAppBuilder", Builder
    )
    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.get_db_context", db_context
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
    assert ("delete_artifact", (app_id, old_id)) in events
    assert ("delete_artifact", (app_id, new_id)) not in events
    assert events[-1] == ("delete_source", None)


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

    class Context:
        job_id = uuid4()

        async def report(self, _message: str, *, percent: int):
            pass

    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.ApplicationDeployStorage", Storage
    )
    monkeypatch.setattr(
        "src.jobs.platform.application_deploy.SolutionAppBuilder", Builder
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
        ("source", None),
        ("artifact", (app_id, new_id)),
    ]
