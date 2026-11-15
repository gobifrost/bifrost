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
from unittest.mock import AsyncMock

import pytest

from shared.app_build_errors import AppBuildMemoryError
from shared.solution_app_compiler import (
    AppBuildResult,
    BUILD_STEP_TIMEOUT_S,
    FAILURE_OUTPUT_LINES,
)
from src.services.solutions import app_build as ab
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import SolutionDeployConflict
from src.services.solutions.deploy import (
    PreparedSolutionAppBuildReceipt,
    PreparedSolutionAppBuilds,
    SolutionAppBuildPlan,
    SolutionAppBuildSpec,
    SolutionDeployer,
    compile_solution_app_builds,
)


@pytest.fixture(autouse=True)
def _stub_identity_toolchain(monkeypatch):
    # Builder tests stub compiler execution; identity inputs have their own
    # tests and must not query Node or npm through a stubbed subprocess.
    monkeypatch.setattr(SolutionAppBuilder, "_sdk_tarball", lambda _self: b"FAKE_TGZ")
    monkeypatch.setattr(
        ab,
        "build_recipe",
        lambda *_args, **_kwargs: {
            "schema": 1,
            "variant": "server-install-v2",
            "sdk": "a" * 64,
            "toolchain": "b" * 64,
            "environment": "c" * 64,
        },
    )


@pytest.mark.e2e
async def test_prebuilt_dist_is_used_without_building(monkeypatch):
    """A non-empty prebuilt_dist short-circuits the vite build and is uploaded."""
    built = {"index.html": b"<html>v2</html>", "assets/app-abc.js": b"//js"}
    app_id = uuid.uuid4()

    b = SolutionAppBuilder()

    def _boom(*_args, **_kwargs):
        raise AssertionError("compiler must not run when prebuilt dist is supplied")

    monkeypatch.setattr(ab, "compile_app", _boom)

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
    """With no prebuilt dist, the builder compiles and uploads the produced dist."""
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()

    captured = {}

    def _fake_compile(inputs):
        captured["inputs"] = inputs
        return AppBuildResult({"index.html": b"<html>built</html>"}, "a" * 64)

    monkeypatch.setattr(ab, "compile_app", _fake_compile)

    out = await b.build(
        app_id=app_id,
        src_files={"src/main.tsx": b"console.log(1)", "index.html": b"<html></html>"},
        dependencies={"bifrost": "1.0.0"},
        prebuilt_dist=None,
    )
    assert out == {"index.html": b"<html>built</html>"}
    assert captured["inputs"].app_id == app_id
    assert b.last_build_identity == "a" * 64


@pytest.mark.asyncio
async def test_remote_compute_uses_prepared_inputs_and_keeps_physical_receipt(monkeypatch):
    """An injected compiler is authoritative; this path never invokes local Vite."""
    app_id = uuid.uuid4()
    builder = SolutionAppBuilder()
    compute = AsyncMock(return_value=AppBuildResult(
        {"index.html": b"remote"}, "physical-build-identity"
    ))

    def local_compile(*_args, **_kwargs):
        raise AssertionError("remote compute must not fall back to local compilation")

    monkeypatch.setattr(ab, "compile_app", local_compile)

    dist = await builder.compile_dist_using(
        app_id,
        {"package.json": b"{}", "index.html": b"<main />"},
        {"vite": "^6"},
        compute,
        require_lockfile=True,
    )

    assert dist == {"index.html": b"remote"}
    assert builder.last_build_identity == "physical-build-identity"
    inputs = compute.await_args.args[0]
    assert inputs.app_id == app_id
    assert inputs.source_files["index.html"] == b"<main />"
    assert inputs.dependencies == {"vite": "^6"}
    assert inputs.require_lockfile is True
    assert inputs.sdk_tarball == b"FAKE_TGZ"


@pytest.mark.asyncio
async def test_remote_compute_error_does_not_fall_back_to_local_compilation(monkeypatch):
    builder = SolutionAppBuilder()
    compute = AsyncMock(side_effect=RuntimeError("remote compile stopped"))

    monkeypatch.setattr(
        ab,
        "compile_app",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("remote failures must not compile locally")
        ),
    )

    with pytest.raises(RuntimeError, match="remote compile stopped"):
        await builder.compile_dist_using(
            uuid.uuid4(),
            {"package.json": b"{}", "index.html": b"<main />"},
            {},
            compute,
        )
    compute.assert_awaited_once()


def _frozen_source_plan(*, expected_old=None, fingerprint="input"):
    app_id = uuid.uuid4()
    solution_id = uuid.uuid4()
    spec = SolutionAppBuildSpec(
        app_id=app_id,
        solution_id=solution_id,
        organization_id=None,
        expected_old_deployment_id=expected_old,
        source_files={"index.html": b"<main />", "package.json": b"{}"},
        prebuilt_dist=None,
        dependencies={"vite": "^6"},
        build_recipe=None,
        reuse_deployment_id=None,
        slug="frozen-app",
        prebuilt_sdk_metadata=None,
        fingerprint=fingerprint,
    )
    return SolutionAppBuildPlan(
        solution_id=solution_id, organization_id=None, apps=(spec,), fingerprint="plan"
    )


@pytest.mark.asyncio
async def test_frozen_solution_plan_uses_remote_compute_without_local_vite(monkeypatch):
    plan = _frozen_source_plan()
    seen = []

    def local_compile(*_args, **_kwargs):
        raise AssertionError("frozen solution source must not compile in the API")

    async def compute(inputs):
        from shared.solution_app_compiler import AppBuildResult, source_build_identity

        seen.append(inputs)
        return AppBuildResult({"index.html": b"remote"}, source_build_identity(inputs))

    monkeypatch.setattr(ab, "compile_app", local_compile)
    receipts = await compile_solution_app_builds(plan, compute)

    assert seen and seen[0].app_id == plan.apps[0].app_id
    assert receipts.receipts[0].dist == {"index.html": b"remote"}
    assert receipts.receipts[0].input_fingerprint == plan.apps[0].fingerprint


@pytest.mark.asyncio
async def test_frozen_solution_plan_rejects_invalid_build_identity():
    plan = _frozen_source_plan()

    async def compute(_inputs):
        return AppBuildResult({"index.html": b"remote"}, "wrong-input-identity")

    with pytest.raises(SolutionDeployConflict, match="does not match"):
        await compile_solution_app_builds(plan, compute)


@pytest.mark.asyncio
async def test_frozen_solution_plan_preserves_verified_physical_identity():
    plan = _frozen_source_plan()
    # A production adapter verifies this against the physical recipe, whose
    # environment differs from the API host even for a version-matched image.
    compute = AsyncMock(return_value=AppBuildResult({"index.html": b"remote"}, "f" * 64))
    prepared = await compile_solution_app_builds(plan, compute)
    assert prepared.receipts[0].build_identity == "f" * 64


@pytest.mark.asyncio
async def test_frozen_solution_plan_reuses_verified_artifact_without_compute(monkeypatch):
    previous = uuid.uuid4()
    plan = _frozen_source_plan(expected_old=previous)
    monkeypatch.setattr(SolutionAppBuilder, "can_reuse", AsyncMock(return_value=True))
    compute = AsyncMock(side_effect=AssertionError("reuse must not request physical compile"))

    receipts = await compile_solution_app_builds(plan, compute)

    assert receipts.receipts[0].reused is True
    assert receipts.receipts[0].deployment_id == previous
    compute.assert_not_awaited()


def test_prepared_solution_receipt_rejects_a_moved_active_deployment():
    from src.models.orm.solutions import Solution

    solution = Solution(id=uuid.uuid4(), slug="frozen", name="Frozen")
    old, moved = uuid.uuid4(), uuid.uuid4()
    build = {
        "id": str(uuid.uuid4()), "slug": "frozen-app", "app_model": "standalone_v2",
        "src": {"index.html": b"<main />"}, "bin": {}, "dist": None,
        "dependencies": {}, "build_recipe": None, "reuse_deployment_id": None,
        "prebuilt_sdk_metadata": None, "expected_old_deployment_id": old,
    }
    deployer = SolutionDeployer(AsyncMock())
    spec = deployer._app_build_spec(solution, build, old)
    prepared = PreparedSolutionAppBuilds(
        plan=SolutionAppBuildPlan(solution.id, None, (spec,), "plan"),
        receipts=(PreparedSolutionAppBuildReceipt(
            app_id=spec.app_id, solution_id=solution.id, expected_old_deployment_id=old,
            input_fingerprint=spec.fingerprint, deployment_id=uuid.uuid4(),
            dist={"index.html": b"remote"}, build_identity="identity",
            sdk_metadata=None, source_built=True,
        ),),
    )
    stale = {**build, "expected_old_deployment_id": moved}

    with pytest.raises(SolutionDeployConflict, match="stale"):
        deployer._verified_prepared_app_builds(solution, [stale], prepared)


@pytest.mark.e2e
def test_build_timeout_translated_to_deploy_failure(monkeypatch):
    """A TimeoutExpired from the npm/vite step surfaces as the deploy's
    build-failure exception (409 with the reason), not a raw TimeoutExpired."""

    app_id = uuid.uuid4()

    def _hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["npm", "install"], timeout=BUILD_STEP_TIMEOUT_S)

    monkeypatch.setattr(ab, "compile_app", _hang)

    with pytest.raises(SolutionDeployConflict, match="timed out"):
        SolutionAppBuilder().compile_dist(app_id, src_files={}, dependencies={}, prebuilt_dist=None)


def test_build_v8_memory_failure_is_classified(monkeypatch):
    """Vite failures preserve actionable OOM diagnostics at the builder boundary."""

    def _oom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["npx", "vite", "build"],
            stderr=b"FATAL ERROR: JavaScript heap out of memory",
        )

    monkeypatch.setattr(ab, "compile_app", _oom)

    with pytest.raises(AppBuildMemoryError) as error:
        SolutionAppBuilder().compile_dist(uuid.uuid4(), src_files={}, dependencies={})

    assert "increase the build container's memory limit" in str(error.value)


def test_build_python_memory_error_is_classified(monkeypatch):
    """An allocation failure in the build path reaches callers as build OOM."""
    monkeypatch.setattr(
        ab, "compile_app", lambda *_args, **_kwargs: (_ for _ in ()).throw(MemoryError())
    )

    with pytest.raises(AppBuildMemoryError) as error:
        SolutionAppBuilder().compile_dist(uuid.uuid4(), src_files={}, dependencies={})

    assert str(error.value).startswith("The app build ran out of memory (OOM).")


@pytest.mark.e2e
def test_build_step_failure_surfaces_tool_output(monkeypatch):
    """A failing npm/vite step surfaces as the deploy's build-failure exception
    carrying the tail of the tool's stderr, so the deploy job reports the cause."""

    app_id = uuid.uuid4()
    stderr = b"".join(f"npm verbose line {i}\n".encode() for i in range(40))
    stderr += b"npm error Cannot read properties of null (reading 'edgesOut')\n"

    def _fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            1, ["npm", "install", "--no-audit"], output=b"", stderr=stderr
        )

    monkeypatch.setattr(ab, "compile_app", _fail)

    with pytest.raises(SolutionDeployConflict) as raised:
        SolutionAppBuilder().compile_dist(app_id, src_files={}, dependencies={}, prebuilt_dist=None)

    message = str(raised.value)
    assert message.startswith(f"`npm install` failed for app {app_id} (exit 1):")
    assert message.endswith("npm error Cannot read properties of null (reading 'edgesOut')")
    assert "npm verbose line 20" not in message
    assert len(message.splitlines()) == 1 + FAILURE_OUTPUT_LINES


@pytest.mark.e2e
async def test_redeploy_clears_stale_dist_files():
    """A second build with a different file set must not leave the old files
    fetchable (full replace of the dist/ artifact)."""
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()
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

    from src.core.storage.contracts import ObjectPage, ObjectSummary, ObjectVersion, PageCursor

    class _FakeStore:
        async def list_page(self, requested_prefix, *, cursor=None, **kwargs):
            assert requested_prefix == prefix
            page = pages[cursor]
            return ObjectPage(tuple(ObjectSummary(item["Key"], 1, ObjectVersion("v"), None)
                                    for item in page["Contents"]), (),
                              PageCursor(page["NextContinuationToken"]) if page["IsTruncated"] else None)

        async def delete_many(self, keys):
            deleted.extend(keys)

    b = SolutionAppBuilder()

    @asynccontextmanager
    async def store():
        yield _FakeStore()

    monkeypatch.setattr(b, "_get_store", store)

    await b.delete_all_app_artifacts(app_id)

    assert deleted == [
        pages[None]["Contents"][0]["Key"],
        pages[None]["Contents"][1]["Key"],
        pages["page-2"]["Contents"][0]["Key"],
    ]


@pytest.mark.asyncio
async def test_fenced_finalization_releases_parent_before_fresh_activation(monkeypatch):
    from src.services.solutions.deploy import CompiledSolutionAppDeployment

    events = []
    parent = AsyncMock()
    parent.commit.side_effect = lambda: events.append("parent_commit")

    async def authorize(db):
        assert db is parent
        events.append("parent_reauthorize")
        return True

    deployer = SolutionDeployer(parent, publication_authorization=authorize)

    async def activate(_compiled):
        assert events == ["parent_commit"]
        events.append("activation")

    monkeypatch.setattr(deployer, "_activate_compiled_dists", activate)
    item = CompiledSolutionAppDeployment(
        app_id=uuid.uuid4(), solution_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(), expected_old_deployment_id=None,
        superseded_deployment_id=None, dist={}, reused=True,
    )
    await deployer._upload_compiled_dists([item])
    assert events == ["parent_commit", "activation", "parent_reauthorize"]


@pytest.mark.asyncio
async def test_lost_job_cannot_reach_app_activation_rows(monkeypatch):
    from src.services.solutions import deploy as module
    from src.jobs.platform.base import PlatformJobCancelled

    activation_db = AsyncMock()

    @asynccontextmanager
    async def activation_context():
        yield activation_db

    monkeypatch.setattr(module, "_solution_app_activation_db_context", activation_context)
    authorize = AsyncMock(return_value=False)
    deployer = SolutionDeployer(AsyncMock(), publication_authorization=authorize)
    with pytest.raises(PlatformJobCancelled):
        await deployer._activate_compiled_dists([])
    authorize.assert_awaited_once_with(activation_db)
    activation_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalization_ownership_loss_is_not_retried_as_a_storage_failure(monkeypatch):
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.solutions.deploy import _retry_idempotent

    operation = AsyncMock(side_effect=PlatformJobCancelled)
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)
    with pytest.raises(PlatformJobCancelled):
        await _retry_idempotent("publish", uuid.uuid4(), operation)
    operation.assert_awaited_once()
    sleep.assert_not_awaited()
