from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.builder import local_app_build
from src.services.builder.local_app_build import (
    LocalBuildError,
    build_commands,
    materialize_build_input,
    run_local_app_build,
)


def _write_contract(workspace: Path, *, base: str = "./") -> None:
    (workspace / "package.json").write_text(
        json.dumps({"private": True}),
        encoding="utf-8",
    )
    (workspace / "build-meta.json").write_text(
        json.dumps({"base": base}),
        encoding="utf-8",
    )


def test_build_commands_are_fixed_and_ignore_lifecycle_scripts(
    tmp_path: Path,
) -> None:
    _write_contract(tmp_path)

    install, build = build_commands(tmp_path)

    assert install == (
        "npm",
        "install",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    )
    assert build[-2:] == ("--base", "./")


def test_build_commands_reject_untrusted_contract(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "build-meta.json").write_text('{"base":"./"}', encoding="utf-8")

    with pytest.raises(LocalBuildError, match="not a Bifrost build input"):
        build_commands(tmp_path)


@pytest.mark.asyncio
async def test_materialize_build_input_verifies_hash_and_extracts(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("package.json", '{"private":true}')
        bundle.writestr("build-meta.json", '{"base":"./"}')
    payload = archive.read_bytes()

    class Storage:
        async def open_input_stream(self):
            yield payload[:7]
            yield payload[7:]

    import hashlib

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    await materialize_build_input(
        Storage(),  # type: ignore[arg-type]
        workspace,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert (workspace / "package.json").is_file()
    assert (workspace / "build-meta.json").is_file()


@pytest.mark.asyncio
async def test_run_local_build_stages_manifest_through_shared_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_contract(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>ok</main>", encoding="utf-8")
    monkeypatch.setattr(local_app_build, "_run_command", AsyncMock())

    storage = AsyncMock()
    storage.write_output.return_value = ("a" * 64, 15)
    report = AsyncMock()
    cancelled = AsyncMock(return_value=False)

    manifest, _log = await run_local_app_build(
        workspace=tmp_path,
        storage=storage,
        app_id=uuid4(),
        timeout_seconds=60,
        log_limit_bytes=1024,
        output_limit_bytes=4096,
        report=report,
        is_cancelled=cancelled,
    )

    assert manifest == [{"path": "index.html", "sha256": "a" * 64, "size": 15}]
    assert report.await_count == 3
    storage.write_output.assert_awaited_once()
