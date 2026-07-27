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
from uuid import UUID

from src.config import Settings, get_settings
from src.services.repo_storage import _get_shared_session

BUILD_ARTIFACTS_ROOT = "_build_artifacts"
APPS_ROOT = "_apps"
INPUT_ARTIFACT_NAME = "input.zip"
_CHUNK_SIZE = 8 * 1024 * 1024


class BuildOutputTooLarge(Exception):
    """Raised when a build job's cumulative staged output exceeds its cap."""


class StagedBuildArtifactStorage:
    """S3 storage for one build job's staged input/output artifacts."""

    def __init__(self, build_job_id: UUID | str, settings: Settings | None = None):
        self.build_job_id = str(build_job_id)
        self.prefix = f"{BUILD_ARTIFACTS_ROOT}/{self.build_job_id}/"
        self._settings = settings or get_settings()
        self._bucket: str = self._settings.s3_bucket or ""

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
        return f"{self._output_prefix(app_id)}{rel_path.lstrip('/')}"

    def _app_dist_key(self, app_id: UUID | str, rel_path: str = "") -> str:
        base = f"{APPS_ROOT}/{app_id}/dist/"
        return f"{base}{rel_path.lstrip('/')}" if rel_path else base

    async def write_input(self, path: Path) -> str:
        """Stream a local zip file to the ``input.zip`` key. Returns the
        sha256 hex digest of the bytes written."""
        digest = hashlib.sha256()
        async with self._get_client() as client:
            with path.open("rb") as f:
                data = f.read()
                digest.update(data)
                await client.put_object(Bucket=self._bucket, Key=self._input_key(), Body=data)
        return digest.hexdigest()

    async def open_input_stream(self) -> AsyncIterator[bytes]:
        """Yield 8 MiB chunks of the staged ``input.zip``.

        Raises FileNotFoundError if no input has been staged for this job.
        """
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=self._input_key())
            except client.exceptions.NoSuchKey as exc:
                raise FileNotFoundError(
                    f"No staged input.zip for build job {self.build_job_id}"
                ) from exc
            except Exception as exc:  # noqa: BLE001 - aiobotocore backends vary
                if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc):
                    raise FileNotFoundError(
                        f"No staged input.zip for build job {self.build_job_id}"
                    ) from exc
                raise

            body = response["Body"]
            async with body:
                while chunk := await body.read(_CHUNK_SIZE):
                    yield chunk

    async def _cumulative_staged_bytes(self, client) -> int:
        """Sum the size of every object currently staged under this job's
        prefix (input.zip + all app output so far)."""
        total = 0
        continuation_token = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": self.prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = await client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
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
        digest = hashlib.sha256()
        body = bytearray()
        async for chunk in chunks:
            digest.update(chunk)
            body.extend(chunk)
        size = len(body)

        async with self._get_client() as client:
            existing_total = await self._cumulative_staged_bytes(client)
            if existing_total + size > max_total_bytes:
                raise BuildOutputTooLarge(
                    f"Build job {self.build_job_id} staged output would reach "
                    f"{existing_total + size} bytes, exceeding the {max_total_bytes} byte cap"
                )
            await client.put_object(
                Bucket=self._bucket, Key=self._output_key(app_id, rel_path), Body=bytes(body)
            )

        return digest.hexdigest(), size

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

    async def copy_outputs_to_app_dist(self, app_id: UUID | str, manifest: list[dict]) -> int:
        """Server-side copy each manifest entry from the staged output prefix
        to ``_apps/{app_id}/dist/{rel_path}``, then delete any existing
        ``_apps/{app_id}/dist/`` keys not present in the manifest.

        Manifest dict shape: ``{"rel_path": <path within the app's dist/>}``.
        The staged source key is derived internally as
        ``_build_artifacts/{build_job_id}/{app_id}/{rel_path}`` — the manifest
        only needs to name the destination-relative path, mirroring
        AppStorageService.sync_preview()'s copy pattern.

        Returns the number of files copied.
        """
        rel_paths = [entry["rel_path"] for entry in manifest]

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
