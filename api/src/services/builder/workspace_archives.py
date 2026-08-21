"""Runner-neutral Builder workspace archive hydration and persistence."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from shared.builder_workspace_archive import (
    BuilderWorkspaceArchiveMismatch,
    BuilderWorkspaceArchiveTooLarge,
    hydrate_builder_turn_workspace,
)
from src.services.builder.fs_tools import WorkspaceLimits
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.scaffold import zip_workspace
from src.services.builder.turn_artifacts import BuilderTurnArtifactStorage

_CHUNK_SIZE = 8 * 1024 * 1024


class BuilderWorkspaceArchiveMissing(FileNotFoundError):
    """The durable source archive for a Builder run is unavailable."""


@dataclass(frozen=True, slots=True)
class BuilderWorkspaceArchiveSource:
    kind: Literal["revision", "checkpoint"]
    solution_id: UUID
    session_id: UUID
    archive_id: UUID
    expected_sha256: str

    def iter_chunks(self) -> AsyncIterator[bytes]:
        """Stream this archive from durable object storage."""

        if self.kind == "revision":
            return SolutionRevisionStorage(self.solution_id).iter_chunks(
                self.archive_id
            )
        return BuilderTurnArtifactStorage(self.archive_id, 1).iter_checkpoint(
            self.solution_id,
            self.session_id,
            self.archive_id,
        )


async def hydrate_workspace_archive(
    source: BuilderWorkspaceArchiveSource,
    destination: Path,
    *,
    limits: WorkspaceLimits,
) -> list[str]:
    """Hydrate a durable Builder archive into an ephemeral POSIX workspace."""

    if limits != WorkspaceLimits():
        raise ValueError("Builder turn archives must use the shared workspace limits")
    try:
        return await hydrate_builder_turn_workspace(
            source.iter_chunks(),
            expected_sha256=source.expected_sha256,
            destination=destination,
            solution_id=source.solution_id,
        )
    except FileNotFoundError as exc:
        raise BuilderWorkspaceArchiveMissing(str(source.archive_id)) from exc


async def persist_workspace_archive(
    *,
    workspace: Path,
    turn_id: UUID,
    dispatch_attempt: int,
    max_bytes: int,
    archive_name: str = "output.zip",
) -> str:
    """Zip an ephemeral workspace and persist it through the turn artifact path."""

    output_zip = workspace.parent / archive_name
    await asyncio.to_thread(zip_workspace, workspace, output_zip)
    output_sha256, _ = await BuilderTurnArtifactStorage(
        turn_id,
        dispatch_attempt,
    ).write_from_path(
        output_zip,
        max_bytes=max_bytes,
    )
    return output_sha256


__all__ = [
    "BuilderWorkspaceArchiveMismatch",
    "BuilderWorkspaceArchiveMissing",
    "BuilderWorkspaceArchiveTooLarge",
    "BuilderWorkspaceArchiveSource",
    "hydrate_workspace_archive",
    "persist_workspace_archive",
]
