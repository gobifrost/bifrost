from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.builder.coordinator import (
    CoordinatorSettings,
    BuilderCoordinator,
    _safe_dist_members,
    assert_secretless_environment,
)


def test_coordinator_settings_require_only_narrow_environment(monkeypatch) -> None:
    for name in (
        "BIFROST_DATABASE_URL",
        "BIFROST_S3_ACCESS_KEY",
        "BIFROST_S3_SECRET_KEY",
        "BIFROST_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BIFROST_RABBITMQ_URL", "amqp://queue")
    monkeypatch.setenv("BIFROST_BUILDER_INTERNAL_SECRET", "internal")
    monkeypatch.setenv("BIFROST_BUILDER_RUNNER_URL", "http://runner")
    monkeypatch.setenv("BIFROST_INTERNAL_API_URL", "http://api")

    assert_secretless_environment()
    settings = CoordinatorSettings.from_env()
    assert settings.rabbitmq_url == "amqp://queue"
    assert settings.builder_runner_url == "http://runner"


def test_coordinator_rejects_data_credentials(monkeypatch) -> None:
    monkeypatch.setenv("BIFROST_DATABASE_URL", "postgresql://forbidden")
    with pytest.raises(RuntimeError, match="forbidden credentials"):
        assert_secretless_environment()


@pytest.mark.asyncio
async def test_coordinator_rejects_dispatch_without_exact_job_id() -> None:
    coordinator = BuilderCoordinator(
        CoordinatorSettings(
            rabbitmq_url="amqp://queue",
            builder_internal_secret="internal",
            builder_runner_url="http://runner",
            internal_api_url="http://api",
        )
    )
    coordinator._run_job = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="requires job_id"):
        await coordinator.process_message({"kick": True})

    coordinator._run_job.assert_not_awaited()


def _response_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_runner_response_validation_accepts_dist_and_metadata(tmp_path) -> None:
    path = tmp_path / "output.zip"
    _response_zip(
        path,
        {
            "dist/index.html": b"<html></html>",
            "dist/assets/app.js": b"ok",
            "build.json": json.dumps({"ok": True, "log_excerpt": ""}).encode(),
        },
    )
    with zipfile.ZipFile(path) as archive:
        members, metadata = _safe_dist_members(archive, max_bytes=100)
    assert [name for name, _info in members] == ["assets/app.js", "index.html"]
    assert metadata["ok"] is True


@pytest.mark.parametrize(
    "member",
    ["../escape", "other/file.js", "dist/../../escape"],
)
def test_runner_response_validation_rejects_unsafe_paths(
    tmp_path,
    member: str,
) -> None:
    path = tmp_path / "output.zip"
    _response_zip(
        path,
        {
            member: b"x",
            "build.json": b'{"ok":true}',
        },
    )
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ValueError):
            _safe_dist_members(archive, max_bytes=100)
