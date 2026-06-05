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

    async def build(
        self,
        app_id: UUID | str,
        src_files: dict[str, bytes],
        dependencies: dict[str, str],
        prebuilt_dist: dict[str, bytes] | None = None,
    ) -> dict[str, bytes]:
        """Produce + upload the app's dist/ to ``_apps/{app_id}/dist/``.

        If ``prebuilt_dist`` is non-empty, it is uploaded verbatim and the Vite
        build is skipped (disconnected fast-path). Otherwise ``src_files`` are
        written to a temp workspace, ``vite build`` runs, and the produced dist/
        is uploaded. Returns the dist file map that was uploaded.
        """
        if prebuilt_dist:
            dist = prebuilt_dist
        else:
            with tempfile.TemporaryDirectory(prefix=f"bifrost-appbuild-{app_id}-") as tmp:
                workdir = Path(tmp)
                self._materialize(workdir, src_files, dependencies)
                dist = self._run_vite_build(workdir)

        await self._upload_dist(app_id, dist)
        return dist

    def _materialize(
        self, workdir: Path, src_files: dict[str, bytes], dependencies: dict[str, str]
    ) -> None:
        """Lay out the app sources + a package.json carrying its npm deps."""
        for rel, content in src_files.items():
            dest = workdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        # Ensure a package.json with the declared deps exists for the build.
        pkg = workdir / "package.json"
        if not pkg.exists():
            pkg.write_text(json.dumps({"name": "bifrost-app", "private": True,
                                       "dependencies": dependencies or {}}))

    def _run_vite_build(self, workdir: Path) -> dict[str, bytes]:
        """Run ``vite build`` in ``workdir`` and return the produced dist/ files.

        Isolated as a seam so tests can stub it (the Node toolchain is an
        environmental dependency, not under unit test).
        """
        subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
            ["npx", "vite", "build"],
            cwd=str(workdir),
            check=True,
            capture_output=True,
        )
        dist_dir = workdir / "dist"
        out: dict[str, bytes] = {}
        for f in dist_dir.rglob("*"):
            if f.is_file():
                out[f.relative_to(dist_dir).as_posix()] = f.read_bytes()
        return out

    async def _upload_dist(self, app_id: UUID | str, dist: dict[str, bytes]) -> None:
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
                    paths.append(obj["Key"][strip:])
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
        return paths

    async def delete_dist(self, app_id: UUID | str) -> None:
        """Delete an app's entire ``_apps/{app_id}/dist/`` artifact (reconcile)."""
        async with self._client() as c:
            for rel in await self.list_dist(app_id):
                await c.delete_object(Bucket=self._bucket, Key=self._dist_key(app_id, rel))
