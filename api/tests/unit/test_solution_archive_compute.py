"""File-backed contract tests for remote Solution archive inspection."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from shared import solution_archive_compute as codec
from shared.solution_archive import PreviewResult
from shared.worker_transfer import ReceivedFileTransfer


def _source_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("bifrost.solution.yaml", "slug: demo\nname: Demo\n")
        archive.writestr("README.md", "# Demo\n")
        archive.writestr("apps/demo/index.tsx", "export default null\n")


def test_input_snapshot_cleanup_leaves_original_staged_archive(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    _source_archive(source)

    transfer = codec.encode_solution_archive_inspect_inputs(source, chunk_size=13)

    assert transfer.path != source
    assert transfer.path.parent == source.parent
    assert transfer.path.stat().st_ino != source.stat().st_ino
    with zipfile.ZipFile(transfer.path) as snapshot, zipfile.ZipFile(source) as original:
        assert snapshot.namelist() == original.namelist()
        assert snapshot.read("README.md") == original.read("README.md")
    transfer.close()
    assert source.is_file()
    assert not transfer.path.exists()


def test_input_snapshot_is_cleaned_after_encoder_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.zip"
    _source_archive(source)
    monkeypatch.setattr(
        codec,
        "_file_size_and_digest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("digest failure")),
    )

    with pytest.raises(OSError, match="digest failure"):
        codec.encode_solution_archive_inspect_inputs(source)

    assert source.is_file()
    assert not list(tmp_path.glob(".source.zip.inspect-*"))


def test_input_cancel_cleanup_leaves_original_staged_archive(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    _source_archive(source)
    transfer = codec.encode_solution_archive_inspect_inputs(source)

    transfer.cancel()

    assert source.is_file()
    assert not transfer.path.exists()


def test_codec_preserves_file_backed_source_and_binds_result_to_input(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    _source_archive(source)
    with codec.encode_solution_archive_inspect_inputs(source, chunk_size=13) as inputs:
        assert inputs.manifest.kind == codec.SOLUTION_ARCHIVE_INSPECT_INPUT_KIND
        inspection_input = codec.decode_solution_archive_inspect_inputs(
            ReceivedFileTransfer(inputs.manifest, inputs.path)
        )
        with codec.encode_solution_archive_inspect_results(
            inspection_input,
            PreviewResult(slug="demo", name="Demo", readme="# Demo\n"),
            (
                codec.SolutionArchiveMember(
                    path="bifrost.solution.yaml",
                    size=22,
                    compressed_size=22,
                    crc=1,
                ),
            ),
            source,
            chunk_size=17,
        ) as results:
            received = ReceivedFileTransfer(results.manifest, results.path)
            inspection = codec.decode_solution_archive_inspect_results(
                received, expected_input=inputs.manifest
            )

    with inspection:
        assert inspection.preview.slug == "demo"
        assert inspection.members[0].path == "bifrost.solution.yaml"
        with zipfile.ZipFile(inspection.source_archive) as archive:
            assert archive.read("apps/demo/index.tsx") == b"export default null\n"


def test_projection_keeps_large_app_source_in_file_backed_result(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    app_source = "export default null\n" * (1024 * 1024)
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("bifrost.solution.yaml", "slug: demo\nname: Demo\n")
        archive.writestr("apps/demo/index.tsx", app_source)
    preview, source_index = codec._project_preview(
        PreviewResult(
            apps=[
                {
                    "id": "demo",
                    "path": "apps/demo",
                    "src_files": {"index.tsx": app_source},
                    "bin_files": {},
                    "logo_b64": None,
                }
            ]
        )
    )
    assert "src_files" not in preview.apps[0]

    with codec.encode_solution_archive_inspect_inputs(source) as inputs:
        inspection_input = codec.decode_solution_archive_inspect_inputs(
            ReceivedFileTransfer(inputs.manifest, inputs.path)
        )
        with codec.encode_solution_archive_inspect_results(
            inspection_input,
            preview,
            (),
            source,
            source_index=source_index,
        ) as results:
            inspection = codec.decode_solution_archive_inspect_results(
                ReceivedFileTransfer(results.manifest, results.path),
                expected_input=inputs.manifest,
            )

    with inspection:
        hydrated = codec.materialize_solution_archive_preview(inspection)
    assert hydrated.apps[0]["src_files"] == {"index.tsx": app_source}


def test_result_decoder_rejects_different_input_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    _source_archive(source)
    with codec.encode_solution_archive_inspect_inputs(source) as inputs:
        inspection_input = codec.decode_solution_archive_inspect_inputs(
            ReceivedFileTransfer(inputs.manifest, inputs.path)
        )
        with codec.encode_solution_archive_inspect_results(
            inspection_input, PreviewResult(), (), source
        ) as results:
            received = ReceivedFileTransfer(results.manifest, results.path)
            altered = inputs.manifest.__class__(
                version=inputs.manifest.version,
                kind=inputs.manifest.kind,
                total_size=inputs.manifest.total_size,
                sha256="0" * 64,
                chunk_size=inputs.manifest.chunk_size,
                chunk_count=inputs.manifest.chunk_count,
                max_total_size=inputs.manifest.max_total_size,
            )
            with pytest.raises(codec.SolutionArchiveInspectError, match="input snapshot"):
                codec.decode_solution_archive_inspect_results(
                    received, expected_input=altered
                )
