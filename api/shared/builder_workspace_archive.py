"""Pure Builder turn archive hydration shared by API and sandbox runner."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.services.builder.fs_tools import WorkspaceLimits, safe_extract_zip
from src.services.builder.scaffold import strip_legacy_builder_assets

MAX_BUILDER_TURN_ARCHIVE_BYTES = 200 * 1024 * 1024
BUILDER_TURN_WORKSPACE_LIMITS = WorkspaceLimits()


class BuilderWorkspaceArchiveError(ValueError):
    """A Builder turn archive cannot be trusted or hydrated."""


class BuilderWorkspaceArchiveMismatch(BuilderWorkspaceArchiveError):
    """Archive bytes do not match the recorded digest."""


class BuilderWorkspaceArchiveTooLarge(BuilderWorkspaceArchiveError):
    """Compressed Builder turn archive exceeds the shared input limit."""


async def hydrate_builder_turn_workspace(
    chunks: AsyncIterator[bytes],
    *,
    expected_sha256: str,
    destination: Path,
    solution_id: UUID | str,
    archive_path: Path | None = None,
) -> list[str]:
    """Verify and extract one Builder turn archive into an ephemeral workspace."""

    archive = archive_path or destination.parent / "source.zip"
    digest = hashlib.sha256()
    total = 0
    with archive.open("xb") as output:
        async for chunk in chunks:
            total += len(chunk)
            if total > MAX_BUILDER_TURN_ARCHIVE_BYTES:
                archive.unlink(missing_ok=True)
                raise BuilderWorkspaceArchiveTooLarge(
                    "Builder workspace archive exceeds the byte limit"
                )
            digest.update(chunk)
            output.write(chunk)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        archive.unlink(missing_ok=True)
        raise BuilderWorkspaceArchiveMismatch(
            "Builder workspace archive SHA-256 does not match its durable record"
        )
    extracted = safe_extract_zip(
        archive,
        destination,
        BUILDER_TURN_WORKSPACE_LIMITS,
    )
    strip_legacy_builder_assets(destination, solution_id=UUID(str(solution_id)))
    return extracted


__all__ = [
    "BUILDER_TURN_WORKSPACE_LIMITS",
    "BuilderWorkspaceArchiveError",
    "BuilderWorkspaceArchiveMismatch",
    "BuilderWorkspaceArchiveTooLarge",
    "MAX_BUILDER_TURN_ARCHIVE_BYTES",
    "hydrate_builder_turn_workspace",
]
