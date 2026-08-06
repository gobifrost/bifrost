"""RabbitMQ-driven coordinator between the API and the secretless runner."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from src.jobs.rabbitmq import BaseConsumer

_CHUNK_SIZE = 64 * 1024
_MAX_OUTPUT_FILES = 5_000
_BUILD_QUEUE = "solution-builds"


@dataclass(frozen=True)
class CoordinatorSettings:
    """The coordinator's deliberately tiny, credential-free configuration."""

    rabbitmq_url: str
    builder_internal_secret: str
    builder_runner_url: str
    internal_api_url: str
    builder_max_concurrent_builds: int = 1
    builder_build_timeout_s: int = 600
    builder_output_limit_bytes: int = 104_857_600

    @classmethod
    def from_env(cls) -> "CoordinatorSettings":
        def required(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                raise RuntimeError(f"{name} is required")
            return value

        return cls(
            rabbitmq_url=required("BIFROST_RABBITMQ_URL"),
            builder_internal_secret=required("BIFROST_BUILDER_INTERNAL_SECRET"),
            builder_runner_url=required("BIFROST_BUILDER_RUNNER_URL"),
            internal_api_url=required("BIFROST_INTERNAL_API_URL"),
            builder_max_concurrent_builds=int(
                os.environ.get("BIFROST_BUILDER_MAX_CONCURRENT_BUILDS", "1")
            ),
            builder_build_timeout_s=int(
                os.environ.get("BIFROST_BUILDER_BUILD_TIMEOUT_S", "600")
            ),
            builder_output_limit_bytes=int(
                os.environ.get("BIFROST_BUILDER_OUTPUT_LIMIT_BYTES", "104857600")
            ),
        )


def assert_secretless_environment() -> None:
    """Fail startup if the coordinator was accidentally given data credentials."""
    forbidden_prefixes = ("AWS_",)
    forbidden_names = {
        "BIFROST_DATABASE_URL",
        "BIFROST_S3_ACCESS_KEY",
        "BIFROST_S3_SECRET_KEY",
        "BIFROST_SECRET_KEY",
    }
    present = sorted(
        name
        for name in os.environ
        if name in forbidden_names or name.startswith(forbidden_prefixes)
    )
    if present:
        raise RuntimeError(
            f"builder coordinator received forbidden credentials: {', '.join(present)}"
        )


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            yield chunk
            await asyncio.sleep(0)


def _safe_dist_members(
    archive: zipfile.ZipFile,
    *,
    max_bytes: int,
) -> tuple[list[tuple[str, zipfile.ZipInfo]], dict[str, Any]]:
    """Validate the runner response before any artifact reaches storage."""
    infos = archive.infolist()
    if len(infos) > _MAX_OUTPUT_FILES + 1:
        raise ValueError("runner output exceeds file count limit")
    by_name = {info.filename: info for info in infos}
    metadata_info = by_name.get("build.json")
    if metadata_info is None or metadata_info.is_dir():
        raise ValueError("runner output is missing build.json")
    metadata = json.loads(archive.read(metadata_info))
    if not isinstance(metadata, dict) or metadata.get("ok") is not True:
        raise ValueError("runner returned invalid build metadata")

    members: list[tuple[str, zipfile.ZipInfo]] = []
    total = 0
    seen: set[str] = set()
    for info in infos:
        if info.filename == "build.json" or info.is_dir():
            continue
        if not info.filename.startswith("dist/"):
            raise ValueError(f"unexpected runner output path: {info.filename!r}")
        rel = info.filename.removeprefix("dist/")
        pure = PurePosixPath(rel)
        mode = info.external_attr >> 16
        if (
            not pure.parts
            or pure.is_absolute()
            or ".." in pure.parts
            or "\\" in rel
            or "\x00" in rel
            or stat.S_ISLNK(mode)
            or rel in seen
        ):
            raise ValueError(f"unsafe runner output path: {rel!r}")
        seen.add(rel)
        total += info.file_size
        if total > max_bytes:
            raise ValueError("runner output exceeds byte limit")
        members.append((pure.as_posix(), info))
    if not members:
        raise ValueError("runner output contains no dist files")
    return sorted(members), metadata


class BuilderCoordinator(BaseConsumer):
    """Consumes central dispatches and claims each exact durable build job."""

    def __init__(self, settings: CoordinatorSettings) -> None:
        self.settings = settings
        super().__init__(
            _BUILD_QUEUE,
            prefetch_count=self.settings.builder_max_concurrent_builds,
        )

    @property
    def _builder_headers(self) -> dict[str, str]:
        return {
            "X-Bifrost-Builder-Key": self.settings.builder_internal_secret or "",
        }

    async def process_message(self, body: dict[str, Any]) -> None:
        job_id = body.get("job_id")
        if not isinstance(job_id, str):
            raise ValueError("builder dispatch requires job_id")
        async with httpx.AsyncClient(
            base_url=self.settings.internal_api_url,
            timeout=httpx.Timeout(30),
        ) as api:
            response = await api.post(
                "/api/internal/builder/claim",
                headers=self._builder_headers,
                params={"job_id": job_id},
            )
            response.raise_for_status()
            claimed = response.json()
            if claimed["job"] is None:
                return
            await self._run_job(api, claimed["job"], claimed["capability"])

    async def _report(
        self,
        api: httpx.AsyncClient,
        job_id: str,
        token: str,
        payload: dict[str, Any],
    ) -> None:
        response = await api.post(
            f"/api/internal/builder/jobs/{job_id}/status",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()

    async def _runner_build(
        self,
        input_path: Path,
        output_path: Path,
        timeout_s: int,
    ) -> tuple[int, dict[str, Any] | None]:
        headers = {
            "Content-Type": "application/zip",
            "Content-Length": str(input_path.stat().st_size),
        }
        async with httpx.AsyncClient(
            base_url=self.settings.builder_runner_url or "",
            timeout=None,
        ) as runner:
            async with runner.stream(
                "POST",
                f"/build?timeout_s={timeout_s}",
                headers=headers,
                content=_file_chunks(input_path),
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    try:
                        return response.status_code, json.loads(body)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        return response.status_code, {"error": "runner request failed"}
                with output_path.open("xb") as output:
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        output.write(chunk)
                return response.status_code, None

    async def _run_job(
        self,
        api: httpx.AsyncClient,
        job: dict[str, Any],
        token: str,
    ) -> None:
        job_id = job["id"]
        auth = {"Authorization": f"Bearer {token}"}
        with tempfile.TemporaryDirectory(prefix=f"bifrost-coordinator-{job_id}-") as tmp:
            workdir = Path(tmp)
            input_path = workdir / "input.zip"
            output_path = workdir / "output.zip"
            try:
                async with api.stream(
                    "GET",
                    f"/api/internal/builder/jobs/{job_id}/input",
                    headers=auth,
                ) as response:
                    response.raise_for_status()
                    with input_path.open("xb") as output:
                        async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                            output.write(chunk)

                build_task = asyncio.create_task(
                    self._runner_build(
                        input_path,
                        output_path,
                        int(job["timeout_s"]),
                    )
                )
                deadline = asyncio.get_running_loop().time() + int(job["timeout_s"]) + 5
                cancelled = False
                while not build_task.done():
                    await asyncio.sleep(2)
                    await api.post(
                        f"/api/internal/builder/jobs/{job_id}/progress",
                        headers=auth,
                    )
                    cancel_response = await api.get(
                        f"/api/internal/builder/jobs/{job_id}/cancelled",
                        headers=auth,
                    )
                    cancel_response.raise_for_status()
                    cancelled = cancel_response.json()["cancelled"]
                    if cancelled or asyncio.get_running_loop().time() >= deadline:
                        async with httpx.AsyncClient(
                            base_url=self.settings.builder_runner_url or "",
                            timeout=10,
                        ) as runner:
                            await runner.post("/cancel")
                        break
                runner_status, runner_error = await build_task
                if cancelled:
                    await self._report(
                        api,
                        job_id,
                        token,
                        {"status": "cancelled", "error": "build cancelled"},
                    )
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    await self._report(
                        api,
                        job_id,
                        token,
                        {"status": "timeout", "error": "build timed out"},
                    )
                    return
                if runner_status != 200:
                    await self._report(
                        api,
                        job_id,
                        token,
                        {
                            "status": "failed",
                            "error": (runner_error or {}).get("error", "runner failed"),
                            "log_excerpt": (runner_error or {}).get("log_excerpt"),
                        },
                    )
                    return

                manifest: list[dict[str, Any]] = []
                with zipfile.ZipFile(output_path) as archive:
                    members, metadata = _safe_dist_members(
                        archive,
                        max_bytes=self.settings.builder_output_limit_bytes,
                    )
                    for rel_path, info in members:
                        async def chunks(
                            member: zipfile.ZipInfo = info,
                        ) -> AsyncIterator[bytes]:
                            with archive.open(member) as source:
                                while chunk := source.read(_CHUNK_SIZE):
                                    yield chunk
                                    await asyncio.sleep(0)

                        upload = await api.put(
                            f"/api/internal/builder/jobs/{job_id}/artifacts/"
                            f"{quote(rel_path, safe='/')}",
                            headers={
                                **auth,
                                "Content-Length": str(info.file_size),
                            },
                            content=chunks(),
                        )
                        upload.raise_for_status()
                        accepted = upload.json()
                        manifest.append({"path": rel_path, **accepted})
                await self._report(
                    api,
                    job_id,
                    token,
                    {
                        "status": "succeeded",
                        "output_manifest": manifest,
                        "log_excerpt": metadata.get("log_excerpt"),
                    },
                )
            except Exception as exc:
                await self._report(
                    api,
                    job_id,
                    token,
                    {"status": "failed", "error": str(exc)[:4096]},
                )


async def heartbeat_forever(settings: CoordinatorSettings) -> None:
    """Keep the availability TTL alive while the coordinator process runs."""
    headers = {"X-Bifrost-Builder-Key": settings.builder_internal_secret or ""}
    async with httpx.AsyncClient(
        base_url=settings.internal_api_url,
        timeout=10,
    ) as client:
        while True:
            try:
                response = await client.post(
                    "/api/internal/builder/heartbeat",
                    headers=headers,
                )
                response.raise_for_status()
                await asyncio.sleep(10)
            except httpx.HTTPError:
                # API rolling restarts/reset windows must expire availability,
                # not permanently kill an otherwise healthy coordinator.
                await asyncio.sleep(2)
