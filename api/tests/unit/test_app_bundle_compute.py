from __future__ import annotations

import pytest

from shared.app_bundle_compute import (
    APP_BUNDLE_INPUT_KIND,
    APP_BUNDLE_RESULT_KIND,
    AppBundleComputeError,
    AppBundleInputs,
    AppBundleOutput,
    decode_app_bundle_inputs,
    decode_app_bundle_results,
    encode_app_bundle_inputs,
    encode_app_bundle_results,
)
from shared.worker_transfer import ReceivedFileTransfer


def _received(transfer):
    return ReceivedFileTransfer(transfer.manifest, transfer.path)


def test_bundle_input_round_trips_frozen_source_and_dependencies() -> None:
    transfer = encode_app_bundle_inputs(AppBundleInputs(
        app_id="app-1",
        source_files={"pages/index.tsx": b"export default () => null"},
        dependencies={"zod": "^3.0.0"},
        mode="preview",
        platform_exports=("Button", "useState"),
    ))
    assert transfer.manifest.kind == APP_BUNDLE_INPUT_KIND
    with transfer:
        with _received(transfer) as received:
            decoded = decode_app_bundle_inputs(received)
    assert decoded.app_id == "app-1"
    assert decoded.source_files == {"pages/index.tsx": b"export default () => null"}
    assert decoded.dependencies == {"zod": "^3.0.0"}
    assert decoded.platform_exports == ("Button", "useState")


def test_bundle_result_rejects_artifacts_not_named_by_manifest() -> None:
    with pytest.raises(AppBundleComputeError, match="do not match"):
        encode_app_bundle_results(AppBundleOutput(
            success=True,
            entry="entry.js",
            css=None,
            outputs=("entry.js",),
            duration_ms=1,
            warnings=(),
            errors=(),
            dependencies={},
            artifacts={"other.js": b"export {}"},
        ))


def test_failed_bundle_result_round_trips_errors_without_artifacts() -> None:
    transfer = encode_app_bundle_results(AppBundleOutput(
        success=False,
        entry=None,
        css=None,
        outputs=(),
        duration_ms=7,
        warnings=(),
        errors=({"text": "Could not resolve import", "file": "pages/index.tsx"},),
        dependencies={},
        artifacts={},
    ))
    assert transfer.manifest.kind == APP_BUNDLE_RESULT_KIND
    with transfer:
        with _received(transfer) as received:
            decoded = decode_app_bundle_results(received)
    assert decoded.success is False
    assert decoded.errors == ({"text": "Could not resolve import", "file": "pages/index.tsx"},)
