from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.services.application_source_resolver import ResolvedApplicationSource
from src.services.solutions.app_build import (
    ReproducibleDependencyBuildError,
    SolutionAppBuilder,
)


def _locked_source() -> dict[str, Any]:
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {
                "dependencies": {
                    "bifrost": "file:./old-bifrost-sdk.tgz",
                    "react": "18.3.1",
                }
            },
            "node_modules/bifrost": {
                "version": "1.0.0",
                "resolved": "file:old-bifrost-sdk.tgz",
                "integrity": "sha512-old-sdk",
            },
            "node_modules/react": {
                "version": "18.3.1",
                "resolved": "https://registry.npmjs.org/react/-/react-18.3.1.tgz",
                "integrity": "sha512-react",
                "peer": True,
            },
        },
    }


def test_automatic_build_requires_retained_lockfile(tmp_path) -> None:
    with pytest.raises(ReproducibleDependencyBuildError, match="package-lock.json"):
        SolutionAppBuilder()._run_vite_build(tmp_path, require_lockfile=True)


def test_automatic_build_refreshes_only_sdk_lock_entry_then_uses_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    lock_path = tmp_path / "package-lock.json"
    lock_path.write_text(json.dumps(_locked_source()))
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"dependencies": {"bifrost": "file:./bifrost-sdk.tgz", "react": "18.3.1"}}
        )
    )
    (tmp_path / "dist").mkdir()
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[:2] == ["npm", "install"]:
            lock = json.loads(lock_path.read_text())
            lock["packages"][""]["dependencies"]["bifrost"] = "file:./bifrost-sdk.tgz"
            lock["packages"]["node_modules/bifrost"] = {
                "version": "file:bifrost-sdk.tgz",
                "resolved": "file:bifrost-sdk.tgz",
            }
            lock["packages"]["node_modules/react"]["version"] = "19.0.0"
            lock["packages"]["node_modules/react"].pop("peer")
            lock["packages"]["node_modules/npm-normalized-metadata"] = {
                "version": "1.0.0",
                "inBundle": True,
            }
            lock_path.write_text(json.dumps(lock))
        if argv[:2] == ["npm", "ci"]:
            vite = tmp_path / "node_modules" / ".bin" / "vite"
            vite.parent.mkdir(parents=True)
            vite.write_text("locked Vite executable")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("src.services.solutions.app_build.subprocess.run", fake_run)

    assert SolutionAppBuilder()._run_vite_build(tmp_path, require_lockfile=True) == {}
    final_lock = json.loads(lock_path.read_text())
    assert (
        final_lock["packages"]["node_modules/react"]
        == _locked_source()["packages"]["node_modules/react"]
    )
    assert "node_modules/npm-normalized-metadata" not in final_lock["packages"]
    assert calls == [
        [
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "bifrost@file:./bifrost-sdk.tgz",
        ],
        ["npm", "ci", "--no-audit", "--no-fund"],
        [str(tmp_path / "node_modules" / ".bin" / "vite"), "build", "--base", "/"],
    ]


def test_automatic_build_refuses_to_fetch_a_missing_build_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(SolutionAppBuilder, "_refresh_sdk_lockfile", lambda *_: None)
    calls = []

    def installed_without_vite(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("src.services.solutions.app_build.subprocess.run", installed_without_vite)

    with pytest.raises(ReproducibleDependencyBuildError, match="Vite"):
        SolutionAppBuilder()._run_vite_build(tmp_path, require_lockfile=True)

    assert calls == [["npm", "ci", "--no-audit", "--no-fund"]]


def test_automatic_build_rejects_package_manifest_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "package-lock.json"
    original_bytes = json.dumps(_locked_source()).encode()
    lock_path.write_bytes(original_bytes)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "bifrost": "file:./bifrost-sdk.tgz",
                    "react": "19.0.0",
                }
            }
        )
    )
    calls: list[list[str]] = []

    def record_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("src.services.solutions.app_build.subprocess.run", record_run)

    with pytest.raises(
        ReproducibleDependencyBuildError, match="package.json.*package-lock.json"
    ):
        SolutionAppBuilder()._refresh_sdk_lockfile(tmp_path)

    assert lock_path.read_bytes() == original_bytes
    assert calls == []


@pytest.mark.parametrize(
    ("original_mutation", "candidate_mutation", "message"),
    [
        (
            lambda lock: lock["packages"].__setitem__("node_modules/bifrost", "bad"),
            lambda lock: None,
            "SDK package entry",
        ),
        (
            lambda lock: lock["packages"].__setitem__("node_modules/bifrost", None),
            lambda lock: None,
            "SDK package entry",
        ),
        (
            lambda lock: None,
            lambda lock: lock["packages"].pop("node_modules/bifrost"),
            "SDK package entry",
        ),
        (
            lambda lock: None,
            lambda lock: lock["packages"]["node_modules/bifrost"].__setitem__(
                "dependencies", {"sdk-descendant": "1.0.0"}
            ),
            "dependency descendants",
        ),
    ],
)
def test_automatic_build_rejects_invalid_sdk_lock_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    original_mutation,
    candidate_mutation,
    message: str,
) -> None:
    lock_path = tmp_path / "package-lock.json"
    original = _locked_source()
    original_mutation(original)
    original_bytes = json.dumps(original).encode()
    lock_path.write_bytes(original_bytes)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"dependencies": {"bifrost": "file:./bifrost-sdk.tgz", "react": "18.3.1"}}
        )
    )

    def fake_run(_argv, **_kwargs):
        candidate = _locked_source()
        candidate["packages"][""]["dependencies"]["bifrost"] = (
            "file:./bifrost-sdk.tgz"
        )
        candidate["packages"]["node_modules/bifrost"] = {
            "version": "2.0.0",
            "resolved": "file:bifrost-sdk.tgz",
        }
        candidate_mutation(candidate)
        lock_path.write_text(json.dumps(candidate))
        return subprocess.CompletedProcess(_argv, 0)

    monkeypatch.setattr("src.services.solutions.app_build.subprocess.run", fake_run)

    with pytest.raises(ReproducibleDependencyBuildError, match=message):
        SolutionAppBuilder()._refresh_sdk_lockfile(tmp_path)

    assert lock_path.read_bytes() == original_bytes


def test_automatic_build_adds_sdk_entries_to_a_platform_injected_lockfile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = _locked_source()
    original["packages"][""]["dependencies"].pop("bifrost")
    original["packages"].pop("node_modules/bifrost")
    lock_path = tmp_path / "package-lock.json"
    lock_path.write_text(json.dumps(original))
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"dependencies": {"bifrost": "file:./bifrost-sdk.tgz", "react": "18.3.1"}}
        )
    )

    def fake_run(_argv, **_kwargs):
        candidate = _locked_source()
        candidate["packages"][""]["dependencies"]["bifrost"] = (
            "file:./bifrost-sdk.tgz"
        )
        candidate["packages"]["node_modules/bifrost"] = {
            "version": "2.0.0",
            "resolved": "file:bifrost-sdk.tgz",
        }
        lock_path.write_text(json.dumps(candidate))
        return subprocess.CompletedProcess(_argv, 0)

    monkeypatch.setattr("src.services.solutions.app_build.subprocess.run", fake_run)

    SolutionAppBuilder()._refresh_sdk_lockfile(tmp_path)

    refreshed = json.loads(lock_path.read_text())
    assert refreshed["packages"][""]["dependencies"]["bifrost"] == (
        "file:./bifrost-sdk.tgz"
    )
    assert refreshed["packages"]["node_modules/bifrost"] == {
        "version": "2.0.0",
        "resolved": "file:bifrost-sdk.tgz",
    }
    assert refreshed["packages"]["node_modules/react"] == original["packages"][
        "node_modules/react"
    ]


def test_automatic_build_grafts_the_sdk_legacy_lock_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = _locked_source()
    original["lockfileVersion"] = 2
    original["dependencies"] = {
        "bifrost": {
            "version": "1.0.0",
            "resolved": "file:old-bifrost-sdk.tgz",
        },
        "react": {
            "version": "18.3.1",
            "resolved": "https://registry.npmjs.org/react/-/react-18.3.1.tgz",
        },
    }
    lock_path = tmp_path / "package-lock.json"
    lock_path.write_text(json.dumps(original))
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"dependencies": {"bifrost": "file:./bifrost-sdk.tgz", "react": "18.3.1"}}
        )
    )

    def fake_run(_argv, **_kwargs):
        candidate = _locked_source()
        candidate["lockfileVersion"] = 2
        candidate["packages"][""]["dependencies"]["bifrost"] = (
            "file:./bifrost-sdk.tgz"
        )
        candidate["packages"]["node_modules/bifrost"] = {
            "version": "2.0.0",
            "resolved": "file:bifrost-sdk.tgz",
        }
        candidate["dependencies"] = {
            "bifrost": {
                "version": "2.0.0",
                "resolved": "file:bifrost-sdk.tgz",
            },
            "react": {"version": "19.0.0"},
        }
        lock_path.write_text(json.dumps(candidate))
        return subprocess.CompletedProcess(_argv, 0)

    monkeypatch.setattr("src.services.solutions.app_build.subprocess.run", fake_run)

    SolutionAppBuilder()._refresh_sdk_lockfile(tmp_path)

    refreshed = json.loads(lock_path.read_text())
    assert refreshed["dependencies"]["bifrost"] == {
        "version": "2.0.0",
        "resolved": "file:bifrost-sdk.tgz",
    }
    assert refreshed["dependencies"]["react"] == original["dependencies"]["react"]


def _pack_local_package(tmp_path, *, name: str, version: str, marker: str) -> Path:
    source = tmp_path / f"{name}-{version}"
    source.mkdir()
    (source / "package.json").write_text(json.dumps({"name": name, "version": version}))
    (source / "index.js").write_text(f"module.exports = {marker!r};\n")
    packed = tmp_path / "packed"
    packed.mkdir(exist_ok=True)
    subprocess.run(
        [
            "npm",
            "pack",
            "--pack-destination",
            str(packed),
        ],
        cwd=source,
        check=True,
        capture_output=True,
        timeout=60,
    )
    return next(packed.glob(f"{name}-{version}.tgz"))


def test_automatic_build_replaces_old_local_sdk_lock_without_changing_other_deps(
    tmp_path,
) -> None:
    """A real npm fixture proves the old SDK archive is replaced before npm ci."""
    old_sdk = _pack_local_package(
        tmp_path, name="bifrost", version="1.0.0", marker="old SDK"
    )
    new_sdk = _pack_local_package(
        tmp_path, name="bifrost", version="2.0.0", marker="new SDK"
    )
    locked_dependency = _pack_local_package(
        tmp_path,
        name="locked-dependency",
        version="1.0.0",
        marker="pinned dependency",
    )
    workdir = tmp_path / "app"
    workdir.mkdir()
    shutil.copyfile(old_sdk, workdir / "bifrost-sdk.tgz")
    shutil.copyfile(locked_dependency, workdir / "locked-dependency.tgz")
    (workdir / "package.json").write_text(
        json.dumps(
            {
                "name": "locked-app",
                "private": True,
                "dependencies": {
                    "bifrost": "file:./bifrost-sdk.tgz",
                    "locked-dependency": "file:./locked-dependency.tgz",
                },
            }
        )
    )
    subprocess.run(
        [
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=workdir,
        check=True,
        capture_output=True,
        timeout=60,
    )
    lock_path = workdir / "package-lock.json"
    old_lock = json.loads(lock_path.read_text())
    old_locked_dependency = old_lock["packages"]["node_modules/locked-dependency"]

    shutil.copyfile(new_sdk, workdir / "bifrost-sdk.tgz")
    SolutionAppBuilder()._refresh_sdk_lockfile(workdir)
    refreshed_lock_bytes = lock_path.read_bytes()
    subprocess.run(
        ["npm", "ci", "--no-audit", "--no-fund"],
        cwd=workdir,
        check=True,
        capture_output=True,
        timeout=60,
    )

    refreshed_lock = json.loads(lock_path.read_text())
    assert lock_path.read_bytes() == refreshed_lock_bytes
    assert (
        refreshed_lock["packages"]["node_modules/locked-dependency"]
        == old_locked_dependency
    )
    assert "new SDK" in (workdir / "node_modules/bifrost/index.js").read_text()
    assert (
        "pinned dependency"
        in (workdir / "node_modules/locked-dependency/index.js").read_text()
    )


def _app(*, active_deployment_id, fingerprint="old-fp"):
    return SimpleNamespace(
        id=uuid4(),
        solution_id=None,
        app_model="standalone_v2",
        active_deployment_id=active_deployment_id,
        deployed_at=None,
        sdk_package_version="1.0.0",
        sdk_fingerprint=fingerprint,
        sdk_contract_version=1,
        sdk_binding_api_major=None,
        sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _source() -> ResolvedApplicationSource:
    return ResolvedApplicationSource(
        files={"package.json": b"{}", "index.html": b"<div id='root'></div>"},
        dependencies={},
        source_kind="independent",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fingerprint,major", [("wrong", 1), ("target", 2)])
async def test_target_sdk_mismatch_before_compile_never_builds_or_activates(
    monkeypatch: pytest.MonkeyPatch,
    fingerprint: str,
    major: int,
) -> None:
    from src.services import application_build

    app = _app(active_deployment_id=uuid4())

    class Builder:
        def compile_dist(self, *_args, **_kwargs):
            raise AssertionError("a mismatched target must stop before compilation")

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="2.0.0", fingerprint=fingerprint, contract_version=2, binding_api_major=major,
        ),
    )

    with pytest.raises(PlatformJobFailure) as raised:
        await application_build.rebuild_application_from_source(
            application=app,
            deployment_id=uuid4(),
            source=_source(),
            target_sdk_fingerprint="target",
            target_sdk_contract_version=2,
            target_sdk_binding_api_major=1,
        )

    assert raised.value.code == "app_sdk_target_mismatch"
    assert app.active_deployment_id is not None


@pytest.mark.asyncio
async def test_target_sdk_mismatch_before_activation_cleans_new_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import application_build

    old_id, new_id = uuid4(), uuid4()
    app = _app(active_deployment_id=old_id)
    events: list[tuple[str, object]] = []
    metadata = iter(
        [
            SimpleNamespace(
                package_version="2.0.0", fingerprint="target", contract_version=2, binding_api_major=None
            ),
            SimpleNamespace(
                package_version="2.0.0", fingerprint="different", contract_version=2, binding_api_major=None
            ),
        ]
    )

    class Builder:
        def compile_dist(self, *_args, **kwargs):
            assert kwargs == {}
            events.append(("compile", None))
            return {"index.html": b"built"}

        async def upload_deployment(self, *_args):
            events.append(("upload", None))

        async def delete_deployment(self, _app_id, deployment_id):
            events.append(("delete_dist", deployment_id))

    class SourceArtifacts:
        async def write_deployment_source(self, *_args):
            events.append(("write_source", None))

        async def delete_deployment_source(self, _app_id, deployment_id):
            events.append(("delete_source", deployment_id))

    class DB:
        async def get(self, *_args):
            raise AssertionError("target must be checked before activation lookup")

    @asynccontextmanager
    async def db_context():
        yield DB()

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )
    monkeypatch.setattr(application_build, "get_db_context", lambda: db_context())
    monkeypatch.setattr(
        application_build, "current_sdk_metadata", lambda: next(metadata)
    )

    with pytest.raises(PlatformJobFailure) as raised:
        await application_build.rebuild_application_from_source(
            application=app,
            deployment_id=new_id,
            source=_source(),
            target_sdk_fingerprint="target",
            target_sdk_contract_version=2,
        )

    assert raised.value.code == "app_sdk_target_mismatch"
    assert app.active_deployment_id == old_id
    assert events == [
        ("compile", None),
        ("upload", None),
        ("write_source", None),
        ("delete_source", new_id),
        ("delete_dist", new_id),
    ]


@pytest.mark.asyncio
async def test_automatic_upgrade_keeps_previous_artifacts_and_respects_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import application_build

    old_id, new_id = uuid4(), uuid4()
    app = _app(active_deployment_id=old_id)
    events: list[tuple[str, object]] = []

    class Builder:
        def compile_dist(self, *_args, **kwargs):
            assert kwargs["require_lockfile"] is True
            return {"index.html": b"built"}

        async def upload_deployment(self, *_args):
            events.append(("upload", None))

        async def delete_deployment(self, _app_id, deployment_id):
            events.append(("delete_dist", deployment_id))

    class SourceArtifacts:
        async def write_deployment_source(self, *_args):
            events.append(("write_source", None))

        async def delete_deployment_source(self, _app_id, deployment_id):
            events.append(("delete_source", deployment_id))

    class DB:
        async def get(self, *_args):
            return app

        async def flush(self):
            events.append(("flush", None))

    class SettingsService:
        def __init__(self, _db):
            pass

        async def get_settings(self):
            return SimpleNamespace(enabled=True)

    @asynccontextmanager
    async def db_context():
        yield DB()

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )
    monkeypatch.setattr(
        application_build, "AppSdkUpgradeSettingsService", SettingsService
    )
    monkeypatch.setattr(application_build, "get_db_context", lambda: db_context())
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="2.0.0", fingerprint="target", contract_version=2, binding_api_major=None
        ),
    )

    await application_build.rebuild_application_from_source(
        application=app,
        deployment_id=new_id,
        source=_source(),
        target_sdk_fingerprint="target",
        target_sdk_contract_version=2,
        automatic_upgrade=True,
        retain_previous_deployment=True,
    )

    assert app.active_deployment_id == new_id
    assert ("delete_source", old_id) not in events
    assert ("delete_dist", old_id) not in events


@pytest.mark.asyncio
async def test_automatic_upgrade_disabled_before_activation_keeps_old_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import application_build

    old_id, new_id = uuid4(), uuid4()
    app = _app(active_deployment_id=old_id)
    events: list[tuple[str, object]] = []

    class Builder:
        def compile_dist(self, *_args, **_kwargs):
            return {"index.html": b"built"}

        async def upload_deployment(self, *_args):
            events.append(("upload", None))

        async def delete_deployment(self, _app_id, deployment_id):
            events.append(("delete_dist", deployment_id))

    class SourceArtifacts:
        async def write_deployment_source(self, *_args):
            events.append(("write_source", None))

        async def delete_deployment_source(self, _app_id, deployment_id):
            events.append(("delete_source", deployment_id))

    class SettingsService:
        def __init__(self, _db):
            pass

        async def get_settings(self):
            return SimpleNamespace(enabled=False)

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(application_build, "SolutionAppBuilder", Builder)
    monkeypatch.setattr(
        application_build, "ApplicationSourceArtifactStorage", SourceArtifacts
    )
    monkeypatch.setattr(
        application_build, "AppSdkUpgradeSettingsService", SettingsService
    )
    monkeypatch.setattr(application_build, "get_db_context", lambda: db_context())
    monkeypatch.setattr(
        application_build,
        "current_sdk_metadata",
        lambda: SimpleNamespace(
            package_version="2.0.0", fingerprint="target", contract_version=2, binding_api_major=None
        ),
    )

    with pytest.raises(PlatformJobFailure) as raised:
        await application_build.rebuild_application_from_source(
            application=app,
            deployment_id=new_id,
            source=_source(),
            target_sdk_fingerprint="target",
            target_sdk_contract_version=2,
            automatic_upgrade=True,
        )

    assert raised.value.code == "automatic_sdk_upgrades_disabled"
    assert app.active_deployment_id == old_id
    assert events == [
        ("upload", None),
        ("write_source", None),
        ("delete_source", new_id),
        ("delete_dist", new_id),
    ]
