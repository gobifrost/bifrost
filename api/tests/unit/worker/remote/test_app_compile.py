"""Unit contracts for launcher-local App compilation stages."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest

from shared import app_build_compute as codec
from shared.solution_app_compiler import AppBuildInputs, AppBuildResult
from src.worker.remote import app_compile


def _payload(transfer: codec.AppBuildTransfer) -> dict[str, object]:
    return {
        "stage_id": str(uuid4()),
        "job_id": str(uuid4()),
        "lease_token": str(uuid4()),
        "input": {"manifest": transfer.manifest.to_dict()},
    }


def _inputs() -> AppBuildInputs:
    return AppBuildInputs(
        app_id=str(uuid4()),
        source_files={"index.html": b"<main />"},
        dependencies={},
        sdk_tarball=b"sdk",
        recipe={
            "schema": 1,
            "variant": "server-install-v2",
            "sdk": "a" * 64,
            "toolchain": "b" * 64,
            "environment": "c" * 64,
        },
        environment={},
    )


class _FakeProcess:
    def __init__(
        self, receipt: dict[str, object], *, result_path: Path | None = None
    ) -> None:
        self.pid, self.returncode, self._receipt, self._result_path = (
            1234,
            0,
            receipt,
            result_path,
        )
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)
        self.killed = False

    async def wait(self) -> int:
        return self.returncode or 0


class _FakeStdin:
    def __init__(self, process: _FakeProcess) -> None:
        self._process = process
        self._data = b""

    def write(self, data: bytes) -> None:
        self._data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        instruction = json.loads(self._data)
        if self._process._result_path is not None:
            Path(instruction["output_path"]).write_bytes(
                self._process._result_path.read_bytes()
            )


class _FakeStdout:
    def __init__(self, process: _FakeProcess) -> None:
        self._data = json.dumps(process._receipt).encode()

    async def read(self, size: int) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


@pytest.mark.asyncio
async def test_prepare_chunk_start_is_idempotent_and_retains_finished_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transfer = codec.encode_app_build_inputs(_inputs(), chunk_size=17)
    payload = _payload(transfer)
    recipe = {
        "schema": 1,
        "variant": "server-install-v2",
        "sdk": "a" * 64,
        "toolchain": "b" * 64,
        "environment": "c" * 64,
    }
    result = codec.encode_app_build_result(
        AppBuildResult({"index.html": b"done"}, "identity")
    )
    spawn_calls: list[_FakeProcess] = []
    terminal_states: list[str] = []

    async def spawn(*_args, **kwargs):
        assert kwargs["env"] == {"PATH": "/safe-toolchain"}
        process = _FakeProcess(
            {
                "state": "succeeded",
                "result_manifest": result.manifest.to_dict(),
                "build_recipe": recipe,
            },
            result_path=result.path,
        )
        spawn_calls.append(process)
        return process

    monkeypatch.setattr(
        app_compile, "package_tool_env", lambda: {"PATH": "/safe-toolchain"}
    )
    stages: app_compile.AppCompileStages

    async def terminal_notification(binding: dict[str, str]) -> None:
        terminal_states.append(stages.status(binding)["state"])

    stages = app_compile.AppCompileStages(
        temp_dir=tmp_path,
        spawn=spawn,
        terminal_notification=terminal_notification,
    )
    try:
        first = stages.prepare(payload)
        assert stages.prepare(payload) == first
        for chunk in transfer.iter_chunks():
            stages.add_chunk({**payload, "chunk": chunk.to_dict()})
        started = await stages.start(payload)
        assert started["state"] == "running"
        task = stages._stages[payload["stage_id"]].task
        assert task is not None
        await task
        assert terminal_states == ["succeeded"]
        assert (await stages.start(payload))["state"] == "succeeded"
        assert len(spawn_calls) == 1
        status = stages.status(
            {key: payload[key] for key in ("stage_id", "job_id", "lease_token")}
        )
        assert status["state"] == "succeeded"
        assert status["result_manifest"] == result.manifest.to_dict()
        assert stages.inventory()[0]["stage_id"] == payload["stage_id"]
        assert stages.inventory()[0]["input_sha256"] == transfer.manifest.sha256
        stage = stages._stages[payload["stage_id"]]
        chunk = stages.result_chunk(
            {key: payload[key] for key in ("stage_id", "job_id", "lease_token")}
            | {"index": 0}
        )
        assert (
            base64.b64decode(chunk["chunk"]["data_b64"])
            == (stage.output_path.read_bytes()[: result.manifest.chunk_size])
        )
    finally:
        transfer.close()
        await stages.shutdown()
        result.close()


@pytest.mark.asyncio
async def test_prepared_status_resumes_offset_and_refuses_duplicate_or_conflicting_chunks(
    tmp_path: Path,
) -> None:
    transfer = codec.encode_app_build_inputs(_inputs(), chunk_size=11)
    payload = _payload(transfer)
    stages = app_compile.AppCompileStages(temp_dir=tmp_path)
    try:
        stages.prepare(payload)
        chunks = list(transfer.iter_chunks())
        stages.add_chunk({**payload, "chunk": chunks[0].to_dict()})
        binding = {key: payload[key] for key in ("stage_id", "job_id", "lease_token")}
        assert stages.status(binding)["input_next_index"] == 1
        with pytest.raises(ValueError, match="ordered"):
            stages.add_chunk({**payload, "chunk": chunks[0].to_dict()})
        bad = {
            **payload,
            "input": {"manifest": {**transfer.manifest.to_dict(), "sha256": "0" * 64}},
        }
        with pytest.raises(ValueError, match="conflicts"):
            stages.prepare(bad)
    finally:
        transfer.close()
        await stages.shutdown()


@pytest.mark.asyncio
async def test_start_refuses_a_corrupt_input_digest_without_launching(
    tmp_path: Path,
) -> None:
    transfer = codec.encode_app_build_inputs(_inputs())
    payload = _payload(transfer)
    bad_manifest = {**transfer.manifest.to_dict(), "sha256": "0" * 64}
    payload["input"] = {"manifest": bad_manifest}
    launches = 0

    async def spawn(*_args, **_kwargs):
        nonlocal launches
        launches += 1
        raise AssertionError("corrupt input must not spawn")

    stages = app_compile.AppCompileStages(temp_dir=tmp_path, spawn=spawn)
    try:
        stages.prepare(payload)
        for chunk in transfer.iter_chunks():
            stages.add_chunk({**payload, "chunk": chunk.to_dict()})
        status = await stages.start(payload)
        assert status["state"] == "failed"
        assert "digest" in status["error"]["message"]
        assert launches == 0
    finally:
        transfer.close()
        await stages.shutdown()


@pytest.mark.asyncio
async def test_release_kills_process_cleans_files_and_tombstones_stage(
    tmp_path: Path,
) -> None:
    transfer = codec.encode_app_build_inputs(_inputs())
    payload = _payload(transfer)
    killed: list[_FakeProcess] = []
    refuse_stop = True

    async def spawn(*_args, **_kwargs):
        return _FakeProcess(
            {"state": "failed", "error": {"category": "buildfailure", "message": "no"}}
        )

    async def kill(process):
        if refuse_stop:
            raise ConnectionError("physical stop failed")
        killed.append(process)
        process.killed = True

    stages = app_compile.AppCompileStages(temp_dir=tmp_path, spawn=spawn, kill=kill)
    try:
        stages.prepare(payload)
        for chunk in transfer.iter_chunks():
            stages.add_chunk({**payload, "chunk": chunk.to_dict()})
        await stages.start(payload)
        stage_dir = stages._stages[payload["stage_id"]].directory
        binding = {key: payload[key] for key in ("stage_id", "job_id", "lease_token")}
        with pytest.raises(ValueError, match="existing binding"):
            await stages.release({**binding, "lease_token": str(uuid4())})
        assert payload["stage_id"] in stages._stages
        assert stage_dir.exists()
        assert stages._stages[payload["stage_id"]].process is not None
        with pytest.raises(ConnectionError, match="physical stop failed"):
            await stages.release(binding)
        assert stages.status(binding)["state"] != "unknown"
        assert stage_dir.exists()
        refuse_stop = False
        released = await stages.release(binding)
        assert released["released"] is True
        assert killed and killed[0].killed
        assert not stage_dir.exists()
        with pytest.raises(ValueError, match="released"):
            stages.prepare(payload)
        assert (
            stages.status(
                {key: payload[key] for key in ("stage_id", "job_id", "lease_token")}
            )["released"]
            is True
        )
    finally:
        transfer.close()
        await stages.shutdown()


@pytest.mark.asyncio
async def test_status_unknown_is_safe_readback_and_released_stays_fenced(
    tmp_path: Path,
) -> None:
    transfer = codec.encode_app_build_inputs(_inputs())
    payload = _payload(transfer)
    binding = {key: payload[key] for key in ("stage_id", "job_id", "lease_token")}
    stages = app_compile.AppCompileStages(temp_dir=tmp_path)
    try:
        assert stages.status(binding) == {
            **binding,
            "state": "unknown",
            "input_next_index": 0,
        }
        stages.prepare(payload)
        await stages.release(binding)
        assert stages.status(binding) == {
            **binding,
            "state": "released",
            "released": True,
        }
        with pytest.raises(ValueError, match="existing binding"):
            stages.status({**binding, "lease_token": str(uuid4())})
    finally:
        transfer.close()
        await stages.shutdown()


def test_child_rebuilds_recipe_using_scrubbed_local_environment(
    tmp_path: Path, monkeypatch
) -> None:
    transfer = codec.encode_app_build_inputs(_inputs())
    output = tmp_path / "output.zip"
    expected_environment = {"PATH": "/toolchain", "LANG": "C"}
    seen: dict[str, object] = {}
    recipe = {
        "schema": 1,
        "variant": "server-install-v2",
        "sdk": "a" * 64,
        "toolchain": "b" * 64,
        "environment": "c" * 64,
    }

    monkeypatch.setattr(app_compile, "package_tool_env", lambda: expected_environment)
    monkeypatch.setattr(
        app_compile, "build_recipe", lambda sdk, *, variant, environment: recipe
    )

    def fake_compile(inputs):
        seen["environment"], seen["recipe"] = inputs.environment, inputs.recipe
        return AppBuildResult({"index.html": b"done"}, "identity")

    monkeypatch.setattr(app_compile, "compile_app", fake_compile)
    try:
        receipt = app_compile._compile_instruction(
            {
                "input_path": str(transfer.path),
                "input_manifest": transfer.manifest.to_dict(),
                "output_path": str(output),
            }
        )
    finally:
        transfer.close()
    assert receipt["state"] == "succeeded"
    assert seen == {"environment": expected_environment, "recipe": recipe}
    assert output.exists()
