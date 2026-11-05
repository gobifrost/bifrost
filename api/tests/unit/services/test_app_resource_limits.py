from __future__ import annotations

import pytest

from src.services.app_resource_limits import (
    AppSourceLimitError,
    AppSourceLimits,
    validate_app_source_metadata,
    validate_app_source_write,
)


def test_source_metadata_rejects_an_oversized_file_before_materialization() -> None:
    with pytest.raises(AppSourceLimitError, match="maximum per file"):
        validate_app_source_metadata(
            {"pages/large.tsx": 11},
            limits=AppSourceLimits(max_files=5, max_total_bytes=20, max_file_bytes=10),
        )


def test_compiler_text_source_must_be_utf8() -> None:
    with pytest.raises(AppSourceLimitError, match="valid UTF-8"):
        validate_app_source_write("pages/index.tsx", b"\xff")


def test_binary_asset_is_not_rejected_for_text_decoding() -> None:
    validate_app_source_write("public/logo.png", b"\x89PNG\r\n\x1a\n\xff")
