"""Coverage for the bounded remote App build transfer codec."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from shared import app_build_compute as codec
from shared.solution_app_compiler import AppBuildInputs, AppBuildResult


def _inputs() -> AppBuildInputs:
    return AppBuildInputs(
        app_id="app-1400",
        source_files={"index.html": b"<main />", "src/main.tsx": b"export {};"},
        dependencies={"vite": "^6"},
        sdk_tarball=b"sdk",
        recipe={"schema": 1, "sdk": "a" * 64, "toolchain": "b" * 64},
        environment={"API_SECRET": "must-not-transfer", "PATH": "/api-toolchain"},
        require_lockfile=True,
    )


def _receive(transfer: codec.AppBuildTransfer, *, temp_dir: Path) -> codec.ReceivedAppBuildTransfer:
    receiver = codec.AppBuildTransferReceiver(transfer.manifest, temp_dir=temp_dir)
    for chunk in transfer.iter_chunks():
        receiver.add_chunk(chunk.to_dict())
    return receiver.finish()


def test_input_round_trip_preserves_provenance_but_uses_remote_environment(tmp_path: Path) -> None:
    inputs = _inputs()
    with codec.encode_app_build_inputs(inputs, chunk_size=31) as transfer:
        chunks = list(transfer.iter_chunks())
        assert all(len(chunk.data_b64) <= 4 * ((31 + 2) // 3) for chunk in chunks)
        with zipfile.ZipFile(transfer.path) as archive:
            assert "environment" not in archive.read("manifest.json").decode()
            assert "must-not-transfer" not in archive.read("manifest.json").decode()
        received = _receive(transfer, temp_dir=tmp_path)

    with received:
        decoded = codec.decode_app_build_inputs(received, environment={"PATH": "/remote-toolchain"})
        assert decoded.app_id == inputs.app_id
        assert decoded.source_files == inputs.source_files
        assert decoded.dependencies == inputs.dependencies
        assert decoded.sdk_tarball == inputs.sdk_tarball
        assert decoded.recipe == inputs.recipe
        assert decoded.require_lockfile is True
        assert decoded.environment == {"PATH": "/remote-toolchain"}


def test_result_round_trip_is_bounded_and_preserves_artifacts(tmp_path: Path) -> None:
    result = AppBuildResult(
        artifacts={"index.html": b"built", "assets/app.js": b"console.log(1)"},
        build_identity="identity",
    )
    with codec.encode_app_build_result(result, chunk_size=11) as transfer:
        received = _receive(transfer, temp_dir=tmp_path)

    with received:
        assert codec.decode_app_build_result(received) == result


def test_receiver_rejects_out_of_order_and_duplicate_chunks(tmp_path: Path) -> None:
    with codec.encode_app_build_inputs(_inputs(), chunk_size=20) as transfer:
        chunks = list(transfer.iter_chunks())
        assert len(chunks) > 1
        with codec.AppBuildTransferReceiver(transfer.manifest, temp_dir=tmp_path) as receiver:
            with pytest.raises(codec.AppBuildTransferError, match="ordered"):
                receiver.add_chunk(chunks[1])
            receiver.add_chunk(chunks[0])
            with pytest.raises(codec.AppBuildTransferError, match="ordered"):
                receiver.add_chunk(chunks[0])


def test_receiver_rejects_corrupt_digest_and_removes_partial_temp_file(tmp_path: Path) -> None:
    with codec.encode_app_build_inputs(_inputs(), chunk_size=17) as transfer:
        bad_manifest = codec.AppBuildTransferManifest(
            **{**transfer.manifest.to_dict(), "sha256": "0" * 64}
        )
        receiver = codec.AppBuildTransferReceiver(bad_manifest, temp_dir=tmp_path)
        path = receiver.path
        for chunk in transfer.iter_chunks():
            receiver.add_chunk(chunk)
        with pytest.raises(codec.AppBuildTransferError, match="digest"):
            receiver.finish()
    assert not path.exists()


def test_decoder_rejects_unsafe_and_duplicate_archive_paths(tmp_path: Path) -> None:
    with _received_archive(
        tmp_path,
        [
            ("manifest.json", b'{"version":1,"kind":"app_build_result","build_identity":"id"}'),
            ("artifacts/../escape.js", b"nope"),
        ],
    ) as unsafe:
        with pytest.raises(codec.AppBuildTransferError, match="unsafe archive path"):
            codec.decode_app_build_result(unsafe)

    with _received_archive(
        tmp_path,
        [
            ("manifest.json", b'{"version":1,"kind":"app_build_result","build_identity":"id"}'),
            ("artifacts/index.html", b"one"),
            ("artifacts/index.html", b"two"),
        ],
        expects_duplicate_warning=True,
    ) as duplicate:
        with pytest.raises(codec.AppBuildTransferError, match="duplicate"):
            codec.decode_app_build_result(duplicate)


def test_source_and_result_limits_are_enforced(monkeypatch) -> None:
    monkeypatch.setattr(codec, "MAX_SOURCE_EXPANDED_BYTES", 1)
    with pytest.raises(codec.AppBuildTransferError, match="expanded byte limit"):
        codec.encode_app_build_inputs(
            AppBuildInputs(
                app_id="app",
                source_files={"index.html": b"xx"},
                dependencies={},
                sdk_tarball=b"sdk",
                recipe={},
                environment={},
            )
        )

    monkeypatch.setattr(codec, "MAX_RESULT_EXPANDED_BYTES", 1)
    with pytest.raises(codec.AppBuildTransferError, match="expanded byte limit"):
        codec.encode_app_build_result(AppBuildResult({"index.html": b"xx"}, "identity"))


def test_cancel_removes_sender_and_receiver_temp_files(tmp_path: Path) -> None:
    transfer = codec.encode_app_build_inputs(_inputs())
    transfer_path = transfer.path
    assert transfer_path.exists()
    receiver = codec.AppBuildTransferReceiver(transfer.manifest, temp_dir=tmp_path)
    receiver_path = receiver.path
    assert receiver_path.exists()

    transfer.cancel()
    receiver.cancel()

    assert not transfer_path.exists()
    assert not receiver_path.exists()


def _received_archive(
    tmp_path: Path,
    members: list[tuple[str, bytes]],
    *,
    expects_duplicate_warning: bool = False,
) -> codec.ReceivedAppBuildTransfer:
    path = tmp_path / "crafted.zip"
    if expects_duplicate_warning:
        with pytest.warns(UserWarning, match="Duplicate name"):
            with zipfile.ZipFile(path, "w") as archive:
                for name, content in members:
                    archive.writestr(name, content)
    else:
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members:
                archive.writestr(name, content)
    data = path.read_bytes()
    manifest = codec.AppBuildTransferManifest(
        version=codec.APP_BUILD_TRANSFER_VERSION,
        kind="app_build_result",
        total_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        chunk_size=codec.MAX_CHUNK_BYTES,
        chunk_count=1,
    )
    return codec.ReceivedAppBuildTransfer(manifest, path)
