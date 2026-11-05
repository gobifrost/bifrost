"""Resource limits shared by App source ingestion and compilation paths."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class AppSourceLimits:
    """Bounds for one App's editable source tree.

    The limits accommodate a substantial multi-page application while keeping a
    single request from materializing an unbounded S3 prefix in an API process.
    """

    max_files: int = 500
    max_total_bytes: int = 32 * 1024 * 1024
    max_file_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class AppArtifactLimits:
    """Bounds for the files emitted by one legacy App bundle."""

    max_files: int = 200
    max_total_bytes: int = 64 * 1024 * 1024
    max_file_bytes: int = 16 * 1024 * 1024


DEFAULT_APP_SOURCE_LIMITS = AppSourceLimits()
DEFAULT_APP_ARTIFACT_LIMITS = AppArtifactLimits()

_TEXT_SOURCE_SUFFIXES = (".tsx", ".ts", ".jsx", ".js", ".css", ".json", ".mjs", ".cjs")


class AppSourceLimitError(ValueError):
    """An App source tree is too large to compile safely in-platform."""


class AppSourceCompilationError(ValueError):
    """An App's authored source has compilation errors that require editing."""


class AppArtifactLimitError(ValueError):
    """A compiler emitted more App artifacts than the platform can publish."""


def validate_app_source_metadata(
    entries: Mapping[str, int],
    *,
    limits: AppSourceLimits = DEFAULT_APP_SOURCE_LIMITS,
) -> None:
    """Reject an oversized source tree before any object body is read."""
    if len(entries) > limits.max_files:
        raise AppSourceLimitError(
            f"App source has {len(entries)} files; the maximum is {limits.max_files}. "
            "Remove generated or unused files and retry."
        )

    total = 0
    for path, size in entries.items():
        if size > limits.max_file_bytes:
            raise AppSourceLimitError(
                f"App source file {path!r} is {size} bytes; the maximum per file is "
                f"{limits.max_file_bytes} bytes. Split or reduce the file and retry."
            )
        total += size

    if total > limits.max_total_bytes:
        raise AppSourceLimitError(
            f"App source totals {total} bytes; the maximum is {limits.max_total_bytes} bytes. "
            "Remove generated or unused files and retry."
        )


def is_compiler_text_source(path: str) -> bool:
    """Whether a source file is decoded or executed by the App toolchain."""
    return path.casefold().endswith(_TEXT_SOURCE_SUFFIXES)


def validate_app_source_write(
    path: str,
    content: bytes,
    *,
    limits: AppSourceLimits = DEFAULT_APP_SOURCE_LIMITS,
) -> None:
    """Validate one App-source update before it reaches durable storage."""
    if len(content) > limits.max_file_bytes:
        raise AppSourceLimitError(
            f"App source file is {len(content)} bytes; the maximum per file is "
            f"{limits.max_file_bytes} bytes. Split or reduce the file and retry."
        )
    if is_compiler_text_source(path):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppSourceLimitError(
                f"App source file {path!r} must be valid UTF-8 text before it can be compiled."
            ) from exc


def validate_app_artifacts(
    entries: Mapping[str, int],
    *,
    limits: AppArtifactLimits = DEFAULT_APP_ARTIFACT_LIMITS,
) -> None:
    """Reject oversized compiler output before it is read into API memory."""
    if len(entries) > limits.max_files:
        raise AppArtifactLimitError(
            f"App bundle has {len(entries)} artifacts; the maximum is {limits.max_files}."
        )
    total = 0
    for path, size in entries.items():
        if size > limits.max_file_bytes:
            raise AppArtifactLimitError(
                f"App bundle artifact {path!r} is {size} bytes; the maximum per artifact is "
                f"{limits.max_file_bytes} bytes."
            )
        total += size
    if total > limits.max_total_bytes:
        raise AppArtifactLimitError(
            f"App bundle totals {total} bytes; the maximum is {limits.max_total_bytes} bytes."
        )
