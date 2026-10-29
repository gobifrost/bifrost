"""SolutionAppBuilder — server-side vite build → _apps/{app_id}/dist/ (criterion 12).

The builder turns a v2 app's transient src/ into a built dist/ and uploads it to
_apps/{app_id}/dist/. A bundle may instead ship a prebuilt dist/ (disconnected
fast-path), in which case the vite build is skipped entirely. src/ is NEVER
written under _solutions/.
"""
from __future__ import annotations

import subprocess
import uuid
from contextlib import asynccontextmanager

import pytest

import src.services.sdk_package as sdkpkg
from shared.app_build_errors import AppBuildMemoryError
from src.services.solutions import app_build as ab
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import SolutionDeployConflict


@pytest.mark.e2e
async def test_prebuilt_dist_is_used_without_building(monkeypatch):
    """A non-empty prebuilt_dist short-circuits the vite build and is uploaded."""
    built = {"index.html": b"<html>v2</html>", "assets/app-abc.js": b"//js"}
    app_id = uuid.uuid4()

    b = SolutionAppBuilder()

    def _boom(*a, **k):
        raise AssertionError("vite build must not run when prebuilt dist is supplied")

    monkeypatch.setattr(b, "_run_vite_build", _boom)

    out = await b.build(
        app_id=app_id,
        src_files={},
        dependencies={},
        prebuilt_dist=built,
    )
    assert set(out) == set(built)

    # And the uploaded dist is readable back from _apps/{app_id}/dist/.
    index = await b.read_dist(app_id, "index.html")
    assert index == b"<html>v2</html>"


@pytest.mark.e2e
async def test_build_runs_vite_when_no_prebuilt_dist(monkeypatch):
    """With no prebuilt dist, the builder runs vite and uploads the produced dist."""
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()

    captured = {}

    def _fake_vite(workdir, base="/"):
        captured["workdir"] = workdir
        captured["base"] = base
        return {"index.html": b"<html>built</html>"}

    monkeypatch.setattr(b, "_run_vite_build", _fake_vite)

    out = await b.build(
        app_id=app_id,
        src_files={"src/main.tsx": b"console.log(1)", "index.html": b"<html></html>"},
        dependencies={"bifrost": "1.0.0"},
        prebuilt_dist=None,
    )
    assert out == {"index.html": b"<html>built</html>"}
    assert "workdir" in captured
    # base is the serving route so emitted asset URLs resolve there, not root.
    assert captured["base"] == f"/api/applications/{app_id}/dist/"


@pytest.mark.e2e
def test_materialize_vendors_bifrost_sdk(monkeypatch, tmp_path):
    """`_materialize` drops the bifrost SDK tarball into the workspace and adds a
    file: dependency so `import from "bifrost"` resolves during the build (P1-a).
    The SDK build is stubbed (no esbuild needed for this assertion)."""
    import json

    from src.services.solutions import app_build as ab

    monkeypatch.setattr(ab, "build_sdk_tarball", lambda v: b"FAKE_TGZ", raising=False)
    # patch the symbol the method imports locally
    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda v: b"FAKE_TGZ")

    b = SolutionAppBuilder()
    b._materialize(tmp_path, {"src/main.tsx": b"export {}"}, {"react": "^18"})

    # tarball vendored
    assert (tmp_path / "bifrost-sdk.tgz").read_bytes() == b"FAKE_TGZ"
    # package.json carries app deps + the file: ref to the SDK
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["dependencies"]["react"] == "^18"
    assert pkg["dependencies"]["bifrost"] == "file:./bifrost-sdk.tgz"


@pytest.mark.e2e
def test_materialize_merges_into_app_package_json(monkeypatch, tmp_path):
    """If the app shipped its own package.json, the SDK ref is merged in, not
    clobbered (the app keeps its scripts/deps)."""
    import json

    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda v: b"FAKE_TGZ")

    app_pkg = {"name": "my-app", "scripts": {"dev": "vite"},
               "dependencies": {"lodash": "^4"}}
    (tmp_path / "package.json").write_text(json.dumps(app_pkg))

    b = SolutionAppBuilder()
    b._materialize(tmp_path, {}, {})

    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["scripts"] == {"dev": "vite"}          # preserved
    assert pkg["dependencies"]["lodash"] == "^4"       # preserved
    assert pkg["dependencies"]["bifrost"] == "file:./bifrost-sdk.tgz"


@pytest.mark.e2e
def test_build_subprocesses_have_timeouts(monkeypatch, tmp_path):
    """Both npm install and vite build run under the self-renewing per-install
    write lock — without a timeout, a hanging npm postinstall wedges the install
    until process restart (every deploy 409s). Both calls must carry one."""

    calls: list[tuple[list[str], dict]] = []

    def _fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(ab.subprocess, "run", _fake_run)
    (tmp_path / "dist").mkdir()

    b = SolutionAppBuilder()
    out = b._run_vite_build(tmp_path, base="/x/")

    assert out == {}
    assert len(calls) == 2
    assert calls[0][0] == [
        "npm",
        "install",
        "--no-audit",
        "--no-fund",
        "--package-lock=false",
    ]
    for _argv, kwargs in calls:
        assert kwargs.get("timeout") == ab._BUILD_STEP_TIMEOUT_S


@pytest.mark.e2e
def test_build_timeout_translated_to_deploy_failure(monkeypatch):
    """A TimeoutExpired from the npm/vite step surfaces as the deploy's
    build-failure exception (409 with the reason), not a raw TimeoutExpired."""

    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda v: b"FAKE_TGZ")
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()

    def _hang(workdir, base="/"):
        raise subprocess.TimeoutExpired(cmd=["npm", "install"], timeout=ab._BUILD_STEP_TIMEOUT_S)

    monkeypatch.setattr(b, "_run_vite_build", _hang)

    with pytest.raises(SolutionDeployConflict, match="timed out"):
        b.compile_dist(app_id, src_files={}, dependencies={}, prebuilt_dist=None)


@pytest.mark.parametrize("stage", ["materialize", "vite"])
def test_build_v8_memory_failure_is_classified(monkeypatch, stage):
    """Both SDK materialization and Vite preserve actionable OOM diagnostics."""
    builder = SolutionAppBuilder()

    def _oom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["npx", "vite", "build"],
            stderr=b"FATAL ERROR: JavaScript heap out of memory",
        )

    if stage == "materialize":
        monkeypatch.setattr(builder, "_materialize", _oom)
    else:
        monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda _version: b"FAKE_TGZ")
        monkeypatch.setattr(builder, "_run_vite_build", _oom)

    with pytest.raises(AppBuildMemoryError) as error:
        builder.compile_dist(uuid.uuid4(), src_files={}, dependencies={})

    assert "increase the build container's memory limit" in str(error.value)


def test_build_python_memory_error_is_classified(monkeypatch):
    """An allocation failure in the build path reaches callers as build OOM."""
    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda _version: b"FAKE_TGZ")
    builder = SolutionAppBuilder()
    monkeypatch.setattr(
        builder,
        "_run_vite_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MemoryError()),
    )

    with pytest.raises(AppBuildMemoryError) as error:
        builder.compile_dist(uuid.uuid4(), src_files={}, dependencies={})

    assert str(error.value).startswith("The app build ran out of memory (OOM).")


@pytest.mark.e2e
def test_build_step_failure_surfaces_tool_output(monkeypatch):
    """A failing npm/vite step surfaces as the deploy's build-failure exception
    carrying the tail of the tool's stderr, so the deploy job reports the cause."""

    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda v: b"FAKE_TGZ")
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()
    stderr = b"".join(f"npm verbose line {i}\n".encode() for i in range(40))
    stderr += b"npm error Cannot read properties of null (reading 'edgesOut')\n"

    def _fail(workdir, base="/"):
        raise subprocess.CalledProcessError(
            1, ["npm", "install", "--no-audit"], output=b"", stderr=stderr
        )

    monkeypatch.setattr(b, "_run_vite_build", _fail)

    with pytest.raises(SolutionDeployConflict) as raised:
        b.compile_dist(app_id, src_files={}, dependencies={}, prebuilt_dist=None)

    message = str(raised.value)
    assert message.startswith(f"`npm install` failed for app {app_id} (exit 1):")
    assert message.endswith("npm error Cannot read properties of null (reading 'edgesOut')")
    assert "npm verbose line 20" not in message
    assert len(message.splitlines()) == 1 + ab._FAILURE_OUTPUT_LINES


@pytest.mark.e2e
async def test_redeploy_clears_stale_dist_files(monkeypatch):
    """A second build with a different file set must not leave the old files
    fetchable (full replace of the dist/ artifact)."""
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()
    monkeypatch.setattr(b, "_run_vite_build", lambda *a, **k: {})

    # First deploy ships index.html + an old hashed asset.
    await b.build(
        app_id=app_id, src_files={}, dependencies={},
        prebuilt_dist={"index.html": b"<html>v1</html>", "assets/old-aaa.js": b"//old"},
    )
    assert await b.read_dist(app_id, "assets/old-aaa.js") == b"//old"

    # Redeploy with a renamed asset — the old one must be gone.
    await b.build(
        app_id=app_id, src_files={}, dependencies={},
        prebuilt_dist={"index.html": b"<html>v2</html>", "assets/new-bbb.js": b"//new"},
    )
    assert await b.read_dist(app_id, "assets/new-bbb.js") == b"//new"
    assert "assets/old-aaa.js" not in await b.list_dist(app_id)


@pytest.mark.e2e
async def test_delete_all_app_artifacts_deletes_legacy_and_versioned_paginated(
    monkeypatch,
):
    app_id = uuid.uuid4()
    prefix = f"_apps/{app_id}/"
    pages = {
        None: {
            "Contents": [
                {"Key": f"{prefix}dist/index.html"},
                {"Key": f"{prefix}deployments/{uuid.uuid4()}/dist/index.html"},
            ],
            "IsTruncated": True,
            "NextContinuationToken": "page-2",
        },
        "page-2": {
            "Contents": [
                {"Key": f"{prefix}deployments/{uuid.uuid4()}/dist/assets/app.js"},
            ],
            "IsTruncated": False,
        },
    }
    deleted: list[str] = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def list_objects_v2(self, **kwargs):
            assert kwargs["Prefix"] == prefix
            return pages[kwargs.get("ContinuationToken")]

        async def delete_object(self, **kwargs):
            deleted.append(kwargs["Key"])

    b = SolutionAppBuilder()

    @asynccontextmanager
    async def _client():
        yield _FakeClient()

    monkeypatch.setattr(b, "_client", _client)

    await b.delete_all_app_artifacts(app_id)

    assert deleted == [
        pages[None]["Contents"][0]["Key"],
        pages[None]["Contents"][1]["Key"],
        pages["page-2"]["Contents"][0]["Key"],
    ]
