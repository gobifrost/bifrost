"""Bounded file and diff inspection for immutable builder revisions."""

from __future__ import annotations

import difflib
import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID

from src.models.contracts.solution_builder import (
    RevisionDiffDTO,
    RevisionDiffFileDTO,
    RevisionFileContentDTO,
    RevisionFileDTO,
    RevisionFilesList,
)
from src.services.builder.fs_tools import (
    WorkspaceLimits,
    WorkspaceRoot,
    WorkspaceViolation,
    safe_extract_zip,
)
from src.services.builder.revision_storage import SolutionRevisionStorage

_MAX_DIFF_BYTES = 1024 * 1024
_MAX_FILE_DIFF_BYTES = 256 * 1024


class RevisionArtifactMissing(FileNotFoundError):
    """The immutable revision row exists but its source artifact does not."""


async def _materialize(
    solution_id: UUID,
    revision_id: UUID,
    root: Path,
    limits: WorkspaceLimits,
) -> WorkspaceRoot:
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    archive_path = root / "source.zip"
    if not await SolutionRevisionStorage(solution_id).copy_to_path(
        revision_id, archive_path
    ):
        raise RevisionArtifactMissing(str(revision_id))
    workspace_path = root / "workspace"
    workspace_path.mkdir(mode=0o700)
    safe_extract_zip(archive_path, workspace_path, limits)
    return WorkspaceRoot(workspace_path, limits)


def _is_text(content: bytes) -> bool:
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


@contextmanager
def _scratch(prefix: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        yield Path(tmp)


async def list_revision_files(
    solution_id: UUID,
    revision_id: UUID,
) -> RevisionFilesList:
    limits = WorkspaceLimits()
    with _scratch("bifrost-revision-files-") as root:
        workspace = await _materialize(solution_id, revision_id, root, limits)
        files: list[RevisionFileDTO] = []
        for relative in workspace.list_files():
            path = workspace.resolve_target(relative)
            with path.open("rb") as handle:
                sample = handle.read(min(limits.max_read_bytes, 8192))
            files.append(
                RevisionFileDTO(
                    path=relative,
                    size_bytes=path.stat().st_size,
                    is_text=_is_text(sample),
                )
            )
    return RevisionFilesList(
        revision_id=revision_id,
        files=files,
        total=len(files),
    )


async def read_revision_file(
    solution_id: UUID,
    revision_id: UUID,
    path: str,
) -> RevisionFileContentDTO:
    limits = WorkspaceLimits()
    with _scratch("bifrost-revision-content-") as root:
        workspace = await _materialize(solution_id, revision_id, root, limits)
        content, truncated = workspace.read_file(path)
        size_bytes = workspace.resolve_target(path).stat().st_size
        if not _is_text(content):
            return RevisionFileContentDTO(
                revision_id=revision_id,
                path=path,
                size_bytes=size_bytes,
                encoding="binary",
                truncated=truncated,
            )
        return RevisionFileContentDTO(
            revision_id=revision_id,
            path=path,
            size_bytes=size_bytes,
            encoding="utf-8",
            content=content.decode("utf-8"),
            truncated=truncated,
        )


def _read_text(workspace: WorkspaceRoot | None, path: str) -> tuple[str | None, bool]:
    if workspace is None:
        return "", False
    try:
        content, truncated = workspace.read_file(path)
    except WorkspaceViolation:
        return "", False
    if not _is_text(content):
        return None, truncated
    return content.decode("utf-8"), truncated


def _signature(workspace: WorkspaceRoot, path: str) -> str:
    digest = hashlib.sha256()
    with workspace.resolve_target(path).open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def diff_revisions(
    solution_id: UUID,
    revision_id: UUID,
    against_revision_id: UUID | None,
) -> RevisionDiffDTO:
    limits = WorkspaceLimits()
    with _scratch("bifrost-revision-diff-") as root:
        current = await _materialize(
            solution_id, revision_id, root / "current", limits
        )
        previous = (
            await _materialize(
                solution_id, against_revision_id, root / "previous", limits
            )
            if against_revision_id is not None
            else None
        )
        current_paths = set(current.list_files())
        previous_paths = set(previous.list_files()) if previous else set()
        changed_paths = sorted(current_paths | previous_paths)

        files: list[RevisionDiffFileDTO] = []
        total_diff_bytes = 0
        for path in changed_paths:
            if (
                previous is not None
                and path in current_paths
                and path in previous_paths
                and _signature(current, path) == _signature(previous, path)
            ):
                continue
            current_text, current_truncated = _read_text(
                current if path in current_paths else None, path
            )
            previous_text, previous_truncated = _read_text(
                previous if path in previous_paths else None, path
            )
            status = (
                "added"
                if path not in previous_paths
                else "deleted"
                if path not in current_paths
                else "modified"
            )
            is_binary = current_text is None or previous_text is None
            diff_text: str | None = None
            additions = 0
            deletions = 0
            truncated = current_truncated or previous_truncated
            if not is_binary:
                rendered = "".join(
                    difflib.unified_diff(
                        (previous_text or "").splitlines(keepends=True),
                        (current_text or "").splitlines(keepends=True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                    )
                )
                for line in rendered.splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        additions += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        deletions += 1

                encoded = rendered.encode("utf-8")
                available = max(0, _MAX_DIFF_BYTES - total_diff_bytes)
                cap = min(_MAX_FILE_DIFF_BYTES, available)
                if len(encoded) > cap:
                    encoded = encoded[:cap]
                    rendered = encoded.decode("utf-8", errors="ignore")
                    truncated = True
                total_diff_bytes += len(encoded)
                diff_text = rendered

            files.append(
                RevisionDiffFileDTO(
                    path=path,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                    is_binary=is_binary,
                    diff=diff_text,
                    truncated=truncated,
                )
            )

    return RevisionDiffDTO(
        revision_id=revision_id,
        against_revision_id=against_revision_id,
        files=files,
        total=len(files),
        additions=sum(file.additions for file in files),
        deletions=sum(file.deletions for file in files),
    )
