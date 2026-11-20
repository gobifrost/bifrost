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

import asyncio
import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from bifrost.app_build_identity import build_identity, build_recipe, valid_recipe
from bifrost.app_lockfile import ReproducibleDependencyBuildError
from shared.app_build_errors import AppBuildMemoryError, subprocess_memory_error
from shared.solution_app_compiler import (
    AppBuildResult,
    BUILD_STEP_TIMEOUT_S,
    AppBuildInputs,
    compile_app,
    output_tail,
    source_build_identity,
)
from shared.subprocess_env import package_tool_env
from src.config import Settings, get_settings
from src.core.storage import object_pages, object_store, optional_read
from src.core.storage.contracts import ObjectStore

if TYPE_CHECKING:
    from src.jobs.platform.base import AppBuildCompute

logger = logging.getLogger(__name__)

APPS_PREFIX = "_apps/"


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
        sdk_tarball = self._sdk_tarball()
        return source_build_identity(self._compiler_inputs(
            app_id,
            files,
            dependencies,
            sdk_tarball,
            require_lockfile=require_lockfile,
        ))

    def _sdk_tarball(self) -> bytes:
        from shared.version import get_version
        from src.services.sdk_package import build_sdk_tarball

        return build_sdk_tarball(get_version())

    def _compiler_inputs(
        self,
        app_id: UUID | str,
        files: dict[str, bytes],
        dependencies: dict[str, str],
        sdk_tarball: bytes,
        *,
        require_lockfile: bool,
    ) -> AppBuildInputs:
        environment = package_tool_env()
        if require_lockfile not in self._recipes:
            self._recipes[require_lockfile] = build_recipe(
                sdk_tarball,
                variant="server-locked-v1" if require_lockfile else "server-install-v2",
                environment=environment,
            )
        return AppBuildInputs(
            app_id=app_id,
            source_files=files,
            dependencies=dependencies,
            sdk_tarball=sdk_tarball,
            recipe=self._recipes[require_lockfile],
            environment=environment,
            require_lockfile=require_lockfile,
        )

    def _compile_inputs_for_remote(
        self,
        app_id: UUID | str,
        files: dict[str, bytes],
        dependencies: dict[str, str],
        *,
        require_lockfile: bool,
    ) -> AppBuildInputs:
        """Prepare the exact SDK-bearing compiler inputs off the API loop."""
        return self._compiler_inputs(
            app_id,
            files,
            dependencies,
            self._sdk_tarball(),
            require_lockfile=require_lockfile,
        )

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
        try:
            result = compile_app(
                self._compiler_inputs(
                    app_id,
                    src_files,
                    dependencies,
                    self._sdk_tarball(),
                    require_lockfile=require_lockfile,
                )
            )
            self.last_build_identity = result.build_identity
            return result.artifacts
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
                f"npm/vite step timed out after {BUILD_STEP_TIMEOUT_S}s for "
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
                f"(exit {exc.returncode}):\n{output_tail(exc)}"
            ) from exc
        except MemoryError as exc:
            raise AppBuildMemoryError() from exc

    async def compile_dist_using(
        self,
        app_id: UUID | str,
        src_files: dict[str, bytes],
        dependencies: dict[str, str],
        compute: "AppBuildCompute",
        prebuilt_dist: dict[str, bytes] | None = None,
        *,
        require_lockfile: bool = False,
    ) -> dict[str, bytes]:
        """Compile through an explicitly supplied physical compute stage.

        Storage and activation remain with this adapter's caller.  A remote
        compute failure deliberately propagates: it must never fall back to
        compiling the same inputs in the API process.
        """
        self.last_build_identity = None
        if prebuilt_dist:
            return prebuilt_dist
        inputs = await asyncio.to_thread(
            self._compile_inputs_for_remote,
            app_id,
            src_files,
            dependencies,
            require_lockfile=require_lockfile,
        )
        result: AppBuildResult = await compute(inputs)
        self.last_build_identity = result.build_identity
        return result.artifacts

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
