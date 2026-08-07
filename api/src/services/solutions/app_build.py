"""
SolutionAppBuilder — server-side Vite build for v2 standalone apps (criterion 12).

A v2 app's transient ``src/`` is built into a ``dist/`` and uploaded to
``_apps/{app_id}/dist/``, from which the platform serves the standalone app. A
deploy bundle may instead ship a prebuilt ``dist/`` (the disconnected fast-path),
in which case the Vite build is skipped. App ``src/`` is NEVER persisted under
``_solutions/`` — it is transient build input only (success-criteria §3.6).

This is the ONE canonical build path: git-connected installs always build here
(from the clone); disconnected installs build here too unless they pre-ship
``dist/``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from aiobotocore.session import get_session

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

APPS_PREFIX = "_apps/"

# Hard cap per npm/vite step. The build runs under the self-renewing per-install
# write lock (write_lock.py renews forever), so an un-timeboxed hang — e.g. a
# dependency's postinstall script waiting on the network — would wedge the
# install until process restart (every subsequent deploy 409s).
_BUILD_STEP_TIMEOUT_S = 600


class SolutionAppBuilder:
    """Builds a v2 app's dist/ and serves it from ``_apps/{app_id}/dist/``."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._bucket: str = self._settings.s3_bucket or ""

    def _dist_key(self, app_id: UUID | str, rel: str = "") -> str:
        base = f"{APPS_PREFIX}{app_id}/dist/"
        return f"{base}{rel.lstrip('/')}" if rel else base

    def _client(self):
        session = get_session()
        return session.create_client(
            "s3",
            endpoint_url=self._settings.s3_endpoint_url,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            region_name=self._settings.s3_region,
        )

    def compile_dist(
        self,
        app_id: UUID | str,
        src_files: dict[str, bytes],
        dependencies: dict[str, str],
        prebuilt_dist: dict[str, bytes] | None = None,
    ) -> dict[str, bytes]:
        """Produce the app's dist/ IN MEMORY (no S3). This is the failure-prone
        part — npm install + vite build — isolated so a deploy can run it BEFORE
        the durable DB commit; a build error then rolls the deploy back with no
        S3 or DB side effects (Codex R4 atomicity).

        If ``prebuilt_dist`` is non-empty it's returned verbatim (disconnected
        fast-path, no build). Synchronous (subprocess-bound); call via
        ``asyncio.to_thread`` from async deploy code.
        """
        if prebuilt_dist:
            return prebuilt_dist
        with tempfile.TemporaryDirectory(prefix=f"bifrost-appbuild-{app_id}-") as tmp:
            workdir = Path(tmp)
            self._materialize(workdir, src_files, dependencies)
            try:
                # base must match the serving route so emitted asset URLs resolve
                # under /api/applications/{id}/dist/ rather than the site root.
                return self._run_vite_build(workdir, base=self._dist_base(app_id))
            except subprocess.TimeoutExpired as exc:
                # Translate to the deploy's build-failure exception (→ 409 with
                # the reason) instead of an unhandled TimeoutExpired 500.
                from src.services.solutions.deploy import SolutionDeployConflict

                raise SolutionDeployConflict(
                    f"npm/vite step timed out after {_BUILD_STEP_TIMEOUT_S}s for "
                    f"app {app_id}; a dependency's install script may be hanging"
                ) from exc

    async def upload_dist(self, app_id: UUID | str, dist: dict[str, bytes]) -> None:
        """Full-replace upload of an already-compiled dist/ to
        ``_apps/{app_id}/dist/`` (cheap PUTs). Runs AFTER the DB commit."""
        await self._upload_dist(app_id, dist)

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
            pkg.write_text(json.dumps(
                {"name": "bifrost-app", "private": True, "dependencies": deps},
                indent=2,
            ))

    def _run_vite_build(self, workdir: Path, base: str = "/") -> dict[str, bytes]:
        """Install declared deps, run ``vite build`` in ``workdir``, and return
        the produced dist/ files.

        Isolated as a seam so tests can stub it (the Node toolchain is an
        environmental dependency, not under unit test). ``base`` is passed to
        Vite so emitted asset URLs in index.html resolve under the serving
        route, not the site root. ``npm install`` runs first so a real app's
        declared dependencies (react, the bifrost SDK, etc.) resolve during the
        build — Vite cannot resolve bare imports from a bare package.json alone.
        """
        subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
            ["npm", "install", "--no-audit", "--no-fund"],
            cwd=str(workdir),
            check=True,
            capture_output=True,
            timeout=_BUILD_STEP_TIMEOUT_S,
        )
        subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
            ["npx", "vite", "build", "--base", base],
            cwd=str(workdir),
            check=True,
            capture_output=True,
            timeout=_BUILD_STEP_TIMEOUT_S,
        )
        dist_dir = workdir / "dist"
        out: dict[str, bytes] = {}
        for f in dist_dir.rglob("*"):
            if f.is_file():
                out[f.relative_to(dist_dir).as_posix()] = f.read_bytes()
        return out

    async def _upload_dist(self, app_id: UUID | str, dist: dict[str, bytes]) -> None:
        # Full replace: a redeploy must not leave a removed/renamed asset
        # fetchable from the new dist/. Clear the prefix, then upload.
        await self.delete_dist(app_id)
        async with self._client() as c:
            for rel, data in dist.items():
                await c.put_object(Bucket=self._bucket, Key=self._dist_key(app_id, rel), Body=data)

    async def read_dist(self, app_id: UUID | str, rel: str) -> bytes:
        """Read one built dist file back from ``_apps/{app_id}/dist/``."""
        async with self._client() as c:
            resp = await c.get_object(Bucket=self._bucket, Key=self._dist_key(app_id, rel))
            return await resp["Body"].read()

    async def list_dist(self, app_id: UUID | str) -> list[str]:
        """List the relative paths of an app's uploaded ``dist/``."""
        prefix = self._dist_key(app_id)
        strip = len(prefix)
        paths: list[str] = []
        token = None
        async with self._client() as c:
            while True:
                kwargs: dict = {"Bucket": self._bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = await c.list_objects_v2(**kwargs)
                for obj in resp.get("Contents", []):
                    key = obj.get("Key")
                    if key:
                        paths.append(key[strip:])
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
        return paths

    async def delete_dist(self, app_id: UUID | str) -> None:
        """Delete an app's entire ``_apps/{app_id}/dist/`` artifact (reconcile)."""
        async with self._client() as c:
            for rel in await self.list_dist(app_id):
                await c.delete_object(Bucket=self._bucket, Key=self._dist_key(app_id, rel))
