"""Production build checks for a Builder workspace."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import yaml

from src.services.builder.build_requests import BuildFailed
from src.services.builder.solution_build_check import (
    MAX_MODEL_BUILD_LOG_CHARS,
    model_visible_build_failure,
    test_solution_workspace_build as run_solution_workspace_build,
)
from src.services.solutions.deploy import solution_entity_id


def _workspace(tmp_path: Path, *, prebuilt: bool = False) -> tuple[Path, UUID]:
    app_id = uuid4()
    (tmp_path / "bifrost.solution.yaml").write_text(
        "slug: compile-check\nname: Compile Check\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "apps" / "portal"
    app_dir.mkdir(parents=True)
    (app_dir / "src").mkdir()
    (app_dir / "src" / "main.tsx").write_text(
        "export default function App() { return null }",
        encoding="utf-8",
    )
    (app_dir / "public").mkdir()
    (app_dir / "public" / "pixel.bin").write_bytes(b"\x00\xff")
    manifest_dir = tmp_path / ".bifrost"
    manifest_dir.mkdir()
    entry: dict[str, object] = {
        "id": str(app_id),
        "slug": "portal",
        "name": "Portal",
        "path": "apps/portal",
        "app_model": "standalone_v2",
        "dependencies": {"clsx": "2.1.1"},
    }
    if prebuilt:
        entry["dist_files"] = {"index.html": "<html></html>"}
    (manifest_dir / "apps.yaml").write_text(
        yaml.safe_dump({"apps": {str(app_id): entry}}),
        encoding="utf-8",
    )
    return tmp_path, app_id


@pytest.mark.asyncio
async def test_workspace_build_uses_deploy_identity_and_canonical_job_plane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, manifest_app_id = _workspace(tmp_path)
    solution_id = uuid4()
    requester_id = uuid4()
    build_job = SimpleNamespace(id=uuid4())
    request = AsyncMock(return_value=build_job)
    wait = AsyncMock(return_value=[build_job])
    monkeypatch.setattr(
        "src.services.builder.solution_build_check.request_app_build",
        request,
    )
    monkeypatch.setattr(
        "src.services.builder.solution_build_check.await_build_jobs",
        wait,
    )

    result = await run_solution_workspace_build(
        workspace,
        solution_id=solution_id,
        requested_by=requester_id,
    )

    request.assert_awaited_once()
    arguments = request.await_args.kwargs
    assert arguments["solution_id"] == solution_id
    assert arguments["app_id"] == solution_entity_id(solution_id, manifest_app_id)
    assert arguments["requested_by"] == requester_id
    assert arguments["dependencies"] == {"clsx": "2.1.1"}
    assert arguments["src_files"]["src/main.tsx"].startswith(b"export default")
    assert arguments["src_files"]["public/pixel.bin"] == b"\x00\xff"
    wait.assert_awaited_once_with([build_job])
    assert result.as_dict() == {
        "valid": True,
        "app_count": 1,
        "compiled_app_count": 1,
        "prebuilt_app_count": 0,
        "build_job_ids": [str(build_job.id)],
    }


@pytest.mark.asyncio
async def test_workspace_build_skips_prebuilt_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _app_id = _workspace(tmp_path, prebuilt=True)
    request = AsyncMock()
    wait = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "src.services.builder.solution_build_check.request_app_build",
        request,
    )
    monkeypatch.setattr(
        "src.services.builder.solution_build_check.await_build_jobs",
        wait,
    )

    result = await run_solution_workspace_build(
        workspace,
        solution_id=uuid4(),
        requested_by=uuid4(),
    )

    request.assert_not_awaited()
    wait.assert_awaited_once_with([])
    assert result.prebuilt_app_count == 1
    assert result.build_job_ids == ()


def test_build_failure_keeps_bounded_actionable_log_tail() -> None:
    marker = "unknown utility class border-border"
    job = SimpleNamespace(
        id=uuid4(),
        status="failed",
        error="npx exited with status 1",
        log_excerpt="old output\n" + "x" * MAX_MODEL_BUILD_LOG_CHARS + marker,
    )

    message = model_visible_build_failure(BuildFailed(job))

    assert "npx exited with status 1" in message
    assert marker in message
    assert "[earlier build output omitted]" in message
    assert "old output" not in message
