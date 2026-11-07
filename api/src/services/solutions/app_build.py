"""
SolutionAppBuilder — server-side Vite build for v2 standalone apps (criterion 12).

A v2 app's transient ``src/`` is built into a ``dist/`` and uploaded to
``_apps/{app_id}/dist/``, from which the platform serves the standalone app. A
deploy bundle may instead ship a prebuilt ``dist/`` (the disconnected fast-path),
in which case the Vite build is skipped. Solution app ``src/`` is not persisted
under ``_solutions/`` by this builder; independent deploy source retention is
owned by ``ApplicationSourceArtifactStorage``.

This is the ONE canonical build path: git-connected installs always build here
(from the clone); disconnected installs build here too unless they pre-ship
``dist/``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from bifrost.app_build_identity import build_identity, build_recipe, valid_recipe
from bifrost.app_lockfile import ReproducibleDependencyBuildError, refresh_sdk_lockfile
from shared.app_build_errors import AppBuildMemoryError, subprocess_memory_error
from shared.subprocess_env import package_tool_env
from src.config import Settings, get_settings
from src.core.storage import object_pages, object_store, optional_read
from src.core.storage.contracts import ObjectStore

logger = logging.getLogger(__name__)

APPS_PREFIX = "_apps/"

# Hard cap per npm/vite step. The build runs under the self-renewing per-install
# write lock (write_lock.py renews forever), so an un-timeboxed hang — e.g. a
# dependency's postinstall script waiting on the network — would wedge the
# install until process restart (every subsequent deploy 409s).
_BUILD_STEP_TIMEOUT_S = 600

# How much of a failed step's output is surfaced on the deploy job.
_FAILURE_OUTPUT_LINES = 20


def _output_tail(exc: subprocess.CalledProcessError) -> str:
    """The last lines of a failed step's stderr, or stdout when stderr is empty."""
    raw = exc.stderr or exc.output or b""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-_FAILURE_OUTPUT_LINES:]) or "(no output)"


class SolutionAppBuilder:
    """Builds a v2 app's dist/ and serves it from ``_apps/{app_id}/dist/``."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self.last_build_identity: str | None = None
        self._recipes: dict[bool, dict] = {}

    def _dist_key(
        self,
        app_id: UUID | str,
        rel: str = "",
        *,
        deployment_id: UUID | str | None = None,
    ) -> str:
        base = (
            f"{APPS_PREFIX}{app_id}/deployments/{deployment_id}/dist/"
            if deployment_id is not None
            else f"{APPS_PREFIX}{app_id}/dist/"
        )
        return f"{base}{rel.lstrip('/')}" if rel else base

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    def source_identity(
        self, app_id: UUID | str, files: dict[str, bytes], dependencies: dict[str, str],
        *, require_lockfile: bool = False,
    ) -> str:
        from shared.version import get_version
        from src.services.sdk_package import build_sdk_tarball

        if require_lockfile not in self._recipes:
            self._recipes[require_lockfile] = build_recipe(
                build_sdk_tarball(get_version()),
                variant="server-locked-v1" if require_lockfile else "server-install-v2",
                environment=package_tool_env(),
            )
        return build_identity(app_id, files, dependencies, self._recipes[require_lockfile])

    @staticmethod
    def attested_identity(
        app_id: UUID | str, files: dict[str, bytes], dependencies: dict[str, str], recipe: object, *, package_name: str = "bifrost-app",
    ) -> str | None:
        from bifrost.app_build_identity import sdk_identity
        from shared.version import get_version
        from src.services.sdk_package import build_sdk_tarball

        if not isinstance(recipe, dict) or not valid_recipe(recipe) or recipe["variant"] not in {"cli-install-v1", "cli-install-v2"}:
            return None
        if recipe["sdk"] != sdk_identity(build_sdk_tarball(get_version())):
            return None
        return build_identity(app_id, files, dependencies, recipe, package_name=package_name)

    def _provenance_key(self, app_id: UUID | str, deployment_id: UUID | str) -> str:
        return f"{APPS_PREFIX}{app_id}/deployments/{deployment_id}/build.json"

    async def write_build_identity(
        self, app_id: UUID | str, deployment_id: UUID | str,
        identity: str, dist: dict[str, bytes],
    ) -> None:
        record = {
            "schema": 1, "app_id": str(app_id), "deployment_id": str(deployment_id),
            "identity": identity,
            "artifacts": {rel: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for rel, data in sorted(dist.items())},
        }
        async with self._get_store() as client:
            await client.write(self._provenance_key(app_id, deployment_id), json.dumps(record, sort_keys=True).encode())

    async def can_reuse(self, app_id: UUID | str, deployment_id: UUID | str | None, identity: str) -> bool:
        if deployment_id is None:
            return False
        async with self._get_store() as client:
            try:
                async with optional_read(client, self._provenance_key(app_id, deployment_id)) as read:
                    if read is None:
                        return False
                    record = json.loads(await read.body.read())
            except (ValueError, UnicodeDecodeError):
                return False
            if (not isinstance(record, dict) or type(record.get("schema")) is not int or record.get("schema") != 1
                    or record.get("app_id") != str(app_id)
                    or record.get("deployment_id") != str(deployment_id)
                    or record.get("identity") != identity):
                return False
            artifacts = record.get("artifacts")
            if not isinstance(artifacts, dict) or not artifacts or "index.html" not in artifacts:
                return False
            # Read every immutable member, not just index.html: partial uploads
            # and missing binary/public assets must never be accepted as hits.
            for rel, expected in artifacts.items():
                if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts or not isinstance(expected, dict):
                    return False
                async with optional_read(client, self._dist_key(app_id, rel, deployment_id=deployment_id)) as read:
                    if read is None:
                        return False
                    content = await read.body.read()
                if expected != {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}:
                    return False
        return True

    def compile_dist(
        self,
        app_id: UUID | str,
        src_files: dict[str, bytes],
        dependencies: dict[str, str],
        prebuilt_dist: dict[str, bytes] | None = None,
        *,
        require_lockfile: bool = False,
    ) -> dict[str, bytes]:
        """Produce the app's dist/ IN MEMORY (no object storage). This is the failure-prone
        part — npm install + vite build — isolated so a deploy can run it BEFORE
        the durable DB commit; a build error then rolls the deploy back with no
        Storage or DB side effects (Codex R4 atomicity).

        If ``prebuilt_dist`` is non-empty it's returned verbatim (disconnected
        fast-path, no build). Synchronous (subprocess-bound); call via
        ``asyncio.to_thread`` from async deploy code.
        """
        self.last_build_identity = None
        if prebuilt_dist:
            return prebuilt_dist
        with tempfile.TemporaryDirectory(prefix=f"bifrost-appbuild-{app_id}-") as tmp:
            workdir = Path(tmp)
            try:
                identity = self.source_identity(app_id, src_files, dependencies, require_lockfile=require_lockfile)
                self._materialize(workdir, src_files, dependencies)
                # base must match the serving route so emitted asset URLs resolve
                # under /api/applications/{id}/dist/ rather than the site root.
                vite_kwargs = {"require_lockfile": True} if require_lockfile else {}
                dist = self._run_vite_build(
                    workdir,
                    base=self._dist_base(app_id),
                    **vite_kwargs,
                )
                self.last_build_identity = identity
                return dist
            except ReproducibleDependencyBuildError as exc:
                if require_lockfile:
                    raise
                from src.services.solutions.deploy import SolutionDeployConflict

                raise SolutionDeployConflict(str(exc)) from exc
            except subprocess.TimeoutExpired as exc:
                # Translate to the deploy's build-failure exception (→ 409 with
                # the reason) instead of an unhandled TimeoutExpired 500.
                from src.services.solutions.deploy import SolutionDeployConflict

                raise SolutionDeployConflict(
                    f"npm/vite step timed out after {_BUILD_STEP_TIMEOUT_S}s for "
                    f"app {app_id}; a dependency's install script may be hanging"
                ) from exc
            except subprocess.CalledProcessError as exc:
                memory_error = subprocess_memory_error(
                    exc.returncode, exc.stderr, exc.stdout
                )
                if memory_error is not None:
                    raise memory_error from exc
                # Same translation for a non-OOM failing step, carrying the
                # tool's own error so the deploy job reports why instead of
                # "see server logs".
                from src.services.solutions.deploy import SolutionDeployConflict

                raise SolutionDeployConflict(
                    f"`{' '.join(exc.cmd[:2])}` failed for app {app_id} "
                    f"(exit {exc.returncode}):\n{_output_tail(exc)}"
                ) from exc
            except MemoryError as exc:
                raise AppBuildMemoryError() from exc

    async def upload_dist(self, app_id: UUID | str, dist: dict[str, bytes]) -> None:
        """Full-replace upload of an already-compiled dist/ to
        ``_apps/{app_id}/dist/`` (cheap PUTs). Runs AFTER the DB commit."""
        await self._upload_dist(app_id, dist)

    async def upload_deployment(
        self,
        app_id: UUID | str,
        deployment_id: UUID | str,
        dist: dict[str, bytes],
    ) -> None:
        """Upload one immutable deployment without touching the active build."""
        async with self._get_store() as c:
            for rel, data in dist.items():
                await c.write(self._dist_key(app_id, rel, deployment_id=deployment_id), data)

    async def build(
        self,
        app_id: UUID | str,
        src_files: dict[str, bytes],
        dependencies: dict[str, str],
        prebuilt_dist: dict[str, bytes] | None = None,
    ) -> dict[str, bytes]:
        """Compile + upload in one step (compile_dist then upload_dist). Kept for
        callers/tests that don't need the build/upload split."""
        dist = self.compile_dist(app_id, src_files, dependencies, prebuilt_dist)
        await self._upload_dist(app_id, dist)
        return dist

    @staticmethod
    def _dist_base(app_id: UUID | str) -> str:
        """The public URL prefix the built assets are served from."""
        return f"/api/applications/{app_id}/dist/"

    # The app resolves `import ... from "bifrost"` against this local tarball,
    # the SAME `bifrost` package /api/sdk/download serves to a dev laptop. We
    # drop it into the workspace and reference it as a file: dependency so the
    # build needs no registry/network (and stays deterministic). One mechanism,
    # laptop == server (Codex P1-a).
    _SDK_TARBALL = "bifrost-sdk.tgz"

    def _materialize(
        self, workdir: Path, src_files: dict[str, bytes], dependencies: dict[str, str]
    ) -> None:
        """Lay out the app sources + a package.json carrying its npm deps, and
        vendor the local ``bifrost`` SDK tarball so ``import from "bifrost"``
        resolves during the build."""
        from shared.version import get_version
        from src.services.sdk_package import build_sdk_tarball

        for rel, content in src_files.items():
            dest = workdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

        # Drop the instance's bifrost SDK tarball next to the app.
        (workdir / self._SDK_TARBALL).write_bytes(build_sdk_tarball(get_version()))

        # Build package.json: app deps + a file: ref to the SDK tarball. If the
        # app already shipped a package.json (its own deps/scripts), merge the
        # SDK ref into its dependencies rather than clobbering it.
        deps = {**(dependencies or {}), "bifrost": f"file:./{self._SDK_TARBALL}"}
        pkg = workdir / "package.json"
        if pkg.exists():
            existing = json.loads(pkg.read_text())
            existing.setdefault("dependencies", {})
            existing["dependencies"] = {**existing["dependencies"], **deps}
            pkg.write_text(json.dumps(existing, indent=2))
        else:
            pkg.write_text(
                json.dumps(
                    {"name": "bifrost-app", "private": True, "dependencies": deps},
                    indent=2,
                )
            )

    def _run_vite_build(
        self,
        workdir: Path,
        base: str = "/",
        *,
        require_lockfile: bool = False,
    ) -> dict[str, bytes]:
        """Install declared deps, run ``vite build`` in ``workdir``, and return
        the produced dist/ files.

        Isolated as a seam so tests can stub it (the Node toolchain is an
        environmental dependency, not under unit test). ``base`` is passed to
        Vite so emitted asset URLs in index.html resolve under the serving
        route, not the site root. ``npm install`` runs first so a real app's
        declared dependencies (react, the bifrost SDK, etc.) resolve during the
        build — Vite cannot resolve bare imports from a bare package.json alone.
        """
        locked = require_lockfile or (workdir / "package-lock.json").exists()
        if locked:
            self._refresh_sdk_lockfile(workdir)
        install_command = (
            ["npm", "ci", "--no-audit", "--no-fund"]
            if locked
            else ["npm", "install", "--no-audit", "--no-fund", "--package-lock=false"]
        )
        subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
            install_command,
            cwd=str(workdir),
            check=True,
            capture_output=True,
            timeout=_BUILD_STEP_TIMEOUT_S,
            env=package_tool_env(),
        )
        vite_command = ["npx", "vite"]
        if locked:
            locked_vite = workdir / "node_modules" / ".bin" / "vite"
            if not locked_vite.is_file():
                raise ReproducibleDependencyBuildError(
                    "Locked builds require Vite in package-lock.json; add Vite to the app dependencies and update the lockfile."
                )
            vite_command = [str(locked_vite)]
        subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
            [*vite_command, "build", "--base", base],
            cwd=str(workdir),
            check=True,
            capture_output=True,
            timeout=_BUILD_STEP_TIMEOUT_S,
            env=package_tool_env(),
        )
        dist_dir = workdir / "dist"
        out: dict[str, bytes] = {}
        for f in dist_dir.rglob("*"):
            if f.is_file():
                out[f.relative_to(dist_dir).as_posix()] = f.read_bytes()
        return out

    def _refresh_sdk_lockfile(self, workdir: Path) -> None:
        refresh_sdk_lockfile(workdir, npm="npm", timeout=_BUILD_STEP_TIMEOUT_S, env=package_tool_env())

    async def _upload_dist(self, app_id: UUID | str, dist: dict[str, bytes]) -> None:
        # Full replace: a redeploy must not leave a removed/renamed asset
        # fetchable from the new dist/. Clear the prefix, then upload.
        await self.delete_dist(app_id)
        async with self._get_store() as c:
            for rel, data in dist.items():
                await c.write(self._dist_key(app_id, rel), data)

    async def read_dist(
        self,
        app_id: UUID | str,
        rel: str,
        *,
        deployment_id: UUID | str | None = None,
    ) -> bytes:
        """Read one built dist file back from ``_apps/{app_id}/dist/``."""
        async with self._get_store() as c:
            async with c.open_read(self._dist_key(app_id, rel, deployment_id=deployment_id)) as read:
                return await read.body.read()

    async def list_dist(
        self,
        app_id: UUID | str,
        *,
        deployment_id: UUID | str | None = None,
    ) -> list[str]:
        """List the relative paths of an app's uploaded ``dist/``."""
        prefix = self._dist_key(app_id, deployment_id=deployment_id)
        strip = len(prefix)
        async with self._get_store() as store:
            return [item.key[strip:] async for page in object_pages(store, prefix)
                    for item in page.objects if item.key]

    async def delete_dist(self, app_id: UUID | str) -> None:
        """Delete an app's unversioned dist artifacts."""
        keys = [self._dist_key(app_id, rel) for rel in await self.list_dist(app_id)]
        async with self._get_store() as store:
            await store.delete_many(keys)

    async def delete_deployment(self, app_id: UUID | str, deployment_id: UUID | str) -> None:
        """Delete one versioned deployment artifact and its provenance."""
        keys = [self._provenance_key(app_id, deployment_id)] + [
            self._dist_key(app_id, rel, deployment_id=deployment_id)
            for rel in await self.list_dist(app_id, deployment_id=deployment_id)
        ]
        async with self._get_store() as store:
            await store.delete_many(keys)

    async def delete_all_app_artifacts(self, app_id: UUID | str) -> None:
        """Delete legacy and versioned artifacts when their App is removed."""
        async with self._get_store() as store:
            async for page in object_pages(store, f"{APPS_PREFIX}{app_id}/"):
                await store.delete_many([item.key for item in page.objects])
