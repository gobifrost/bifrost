"""S3 storage for one build job's staged (not-yet-deployed) artifacts.

S3 layout:
    _build_artifacts/{build_job_id}/input.zip
    _build_artifacts/{build_job_id}/{app_id}/<dist rel path>

The build plane (a separate coordinator process, see build_plane.py) streams
the app's input zip here, runs npm/vite out-of-process, and streams the
resulting dist/ files back one at a time — never buffering the whole dist in
memory, since output size is untrusted until it's counted. Once a build
finishes, ``copy_outputs_to_app_dist`` promotes the staged files to the app's
real serving prefix (`_apps/{app_id}/dist/`) via server-side copy, and
``delete_job`` reclaims the staged prefix.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from pathlib import PurePosixPath
from uuid import UUID

from src.config import Settings, get_settings
from src.services.file_storage.s3_client import S3StorageClient
from src.services.repo_storage import _get_shared_session

BUILD_ARTIFACTS_ROOT = "_build_artifacts"
APPS_ROOT = "_apps"
INPUT_ARTIFACT_NAME = "input.zip"
_CHUNK_SIZE = 8 * 1024 * 1024


class BuildOutputTooLarge(Exception):
    """Raised when a build job's cumulative staged output exceeds its cap."""


class BuildArtifactIntegrityError(Exception):
    """Raised when staged output no longer matches its accepted manifest."""


def validate_output_path(rel_path: str) -> str:
    """Return a canonical safe dist-relative path or raise ``ValueError``."""
    pure = PurePosixPath(rel_path)
    if (
        not pure.parts
        or pure.is_absolute()
        or ".." in pure.parts
        or "\x00" in rel_path
        or "\\" in rel_path
        or rel_path.endswith("/")
    ):
        raise ValueError(f"unsafe build output path: {rel_path!r}")
    return pure.as_posix()


class StagedBuildArtifactStorage:
    """S3 storage for one build job's staged input/output artifacts."""

    def __init__(self, build_job_id: UUID | str, settings: Settings | None = None):
        self.build_job_id = str(build_job_id)
        self.prefix = f"{BUILD_ARTIFACTS_ROOT}/{self.build_job_id}/"
        self._settings = settings or get_settings()
        self._bucket: str = self._settings.s3_bucket or ""
        self._streaming = S3StorageClient(self._settings)

    @asynccontextmanager
    async def _get_client(self):
        session = _get_shared_session()
        async with session.create_client(
            "s3",
            endpoint_url=self._settings.s3_endpoint_url,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            region_name=self._settings.s3_region,
        ) as client:
            yield client

    def _input_key(self) -> str:
        return f"{self.prefix}{INPUT_ARTIFACT_NAME}"

    def _output_prefix(self, app_id: UUID | str) -> str:
        return f"{self.prefix}{app_id}/"

    def _output_key(self, app_id: UUID | str, rel_path: str) -> str:
        return f"{self._output_prefix(app_id)}{validate_output_path(rel_path)}"

    def _app_dist_key(self, app_id: UUID | str, rel_path: str = "") -> str:
        base = f"{APPS_ROOT}/{app_id}/dist/"
        return f"{base}{rel_path.lstrip('/')}" if rel_path else base

    async def write_input(self, path: Path) -> str:
        """Stream a local zip file to the ``input.zip`` key. Returns the
        sha256 hex digest of the bytes written."""

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as f:
                while chunk := f.read(_CHUNK_SIZE):
                    yield chunk

        digest, _ = await self._streaming.put_object_from_chunks(
            self._input_key(),
            chunks(),
            content_type="application/zip",
        )
        return digest

    async def open_input_stream(self) -> AsyncIterator[bytes]:
        """Yield 8 MiB chunks of the staged ``input.zip``.

        Raises FileNotFoundError if no input has been staged for this job.
        """
        async for chunk in self._streaming.iter_object_chunks(
            self._input_key(),
            chunk_size=_CHUNK_SIZE,
        ):
            yield chunk

    async def _cumulative_output_bytes(
        self,
        client,
        app_id: UUID | str,
        *,
        replacing_key: str,
    ) -> int:
        """Sum staged output bytes for this app, excluding a key that is about
        to be replaced. The source input has its own limit and is not build
        output."""
        total = 0
        continuation_token = None
        prefix = self._output_prefix(app_id)
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = await client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                if obj.get("Key") != replacing_key:
                    total += obj.get("Size", 0)
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
        return total

    async def write_output(
        self,
        app_id: UUID | str,
        rel_path: str,
        chunks: AsyncIterator[bytes],
        max_total_bytes: int,
    ) -> tuple[str, int]:
        """Stream one dist file to the staged key under
        ``_build_artifacts/{build_job_id}/{app_id}/{rel_path}``.

        Returns (sha256, size) of the bytes written. Raises
        BuildOutputTooLarge when the cumulative staged bytes for this job
        (across all prior write_output calls plus this one) would exceed
        max_total_bytes.
        """
        output_key = self._output_key(app_id, rel_path)
        async with self._get_client() as client:
            existing_total = await self._cumulative_output_bytes(
                client,
                app_id,
                replacing_key=output_key,
            )

        streamed = 0

        async def bounded_chunks() -> AsyncIterator[bytes]:
            nonlocal streamed
            async for chunk in chunks:
                streamed += len(chunk)
                if existing_total + streamed > max_total_bytes:
                    raise BuildOutputTooLarge(
                        f"Build job {self.build_job_id} staged output would reach "
                        f"{existing_total + streamed} bytes, exceeding the "
                        f"{max_total_bytes} byte cap"
                    )
                yield chunk

        return await self._streaming.put_object_from_chunks(
            output_key,
            bounded_chunks(),
        )

    async def list_outputs(self, app_id: UUID | str) -> list[str]:
        """List the relative paths staged for one app under this build job."""
        prefix = self._output_prefix(app_id)
        strip = len(prefix)
        paths: list[str] = []
        async with self._get_client() as client:
            continuation_token = None
            while True:
                kwargs = {"Bucket": self._bucket, "Prefix": prefix}
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token
                response = await client.list_objects_v2(**kwargs)
                for obj in response.get("Contents", []):
                    key = obj.get("Key")
                    if key:
                        paths.append(key[strip:])
                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")
        return paths

    async def verify_manifest(self, app_id: UUID | str, manifest: list[dict]) -> None:
        """Re-hash every staged file and require an exact manifest match.

        The coordinator's upload response supplies the expected hash/size, but
        staged bytes remain outside the database. Rechecking them at status
        acceptance/finalize prevents a capability retry or storage mutation
        from swapping bytes after the manifest was recorded.
        """
        expected_paths = {validate_output_path(entry["path"]) for entry in manifest}
        actual_paths = set(await self.list_outputs(app_id))
        if expected_paths != actual_paths:
            raise BuildArtifactIntegrityError(
                f"staged paths do not match manifest: expected={sorted(expected_paths)}, "
                f"actual={sorted(actual_paths)}"
            )

        for entry in manifest:
            rel_path = validate_output_path(entry["path"])
            digest = hashlib.sha256()
            size = 0
            async for chunk in self._streaming.iter_object_chunks(
                self._output_key(app_id, rel_path),
                chunk_size=_CHUNK_SIZE,
            ):
                digest.update(chunk)
                size += len(chunk)
            if digest.hexdigest() != entry["sha256"] or size != entry["size"]:
                raise BuildArtifactIntegrityError(
                    f"staged artifact {rel_path!r} does not match its manifest"
                )

    async def copy_outputs_to_app_dist(self, app_id: UUID | str, manifest: list[dict]) -> int:
        """Server-side copy each manifest entry from the staged output prefix
        to ``_apps/{app_id}/dist/{rel_path}``, then delete any existing
        ``_apps/{app_id}/dist/`` keys not present in the manifest.

        Manifest dict shape: ``{"path": <path within the app's dist/>}``.
        The staged source key is derived internally as
        ``_build_artifacts/{build_job_id}/{app_id}/{rel_path}`` — the manifest
        only needs to name the destination-relative path, mirroring
        AppStorageService.sync_preview()'s copy pattern.

        Returns the number of files copied.
        """
        await self.verify_manifest(app_id, manifest)
        rel_paths = [validate_output_path(entry["path"]) for entry in manifest]

        async with self._get_client() as client:
            for rel_path in rel_paths:
                await client.copy_object(
                    Bucket=self._bucket,
                    CopySource={"Bucket": self._bucket, "Key": self._output_key(app_id, rel_path)},
                    Key=self._app_dist_key(app_id, rel_path),
                )

            dist_prefix = self._app_dist_key(app_id)
            strip = len(dist_prefix)
            existing_dist: set[str] = set()
            continuation_token = None
            while True:
                kwargs = {"Bucket": self._bucket, "Prefix": dist_prefix}
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token
                response = await client.list_objects_v2(**kwargs)
                for obj in response.get("Contents", []):
                    key = obj.get("Key")
                    if key:
                        existing_dist.add(key[strip:])
                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")

            stale = existing_dist - set(rel_paths)
            for rel_path in stale:
                await client.delete_object(Bucket=self._bucket, Key=self._app_dist_key(app_id, rel_path))

        return len(rel_paths)

    async def delete_job(self) -> int:
        """Batch-delete every key under ``_build_artifacts/{build_job_id}/``.
        Returns the count of keys deleted."""
        deleted = 0
        async with self._get_client() as client:
            continuation_token = None
            while True:
                kwargs = {"Bucket": self._bucket, "Prefix": self.prefix}
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token

                response = await client.list_objects_v2(**kwargs)
                keys = [
                    {"Key": key}
                    for obj in response.get("Contents", [])
                    if (key := obj.get("Key")) is not None
                ]
                if keys:
                    await client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
                    deleted += len(keys)

                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")

        return deleted
