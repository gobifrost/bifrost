"""Immutable AI proposals for the administrator-owned global ``_repo`` workspace."""

from __future__ import annotations

import ast
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionGlobalWorkspaceApply,
    SolutionSourceRevision,
)
from src.models.orm.solutions import Solution
from src.services.audit import emit_audit
from src.services.builder.fs_tools import (
    WorkspaceLimits,
    WorkspaceRoot,
    safe_extract_zip,
)
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.scaffold import zip_workspace
from src.services.editor.file_filter import is_excluded_path
from src.services.file_storage.service import FileStorageService
from src.services.repo_storage import RepoStorage
from src.services.solutions.write_lock import solution_write_lock

GLOBAL_WORKSPACE_SLUG = "bifrost-global-workspace"
GLOBAL_WORKSPACE_NAME = "Global Workspace"
GLOBAL_TARGET_KIND = "global_repo"


class GlobalWorkspaceError(RuntimeError):
    pass


class GlobalWorkspaceConflict(GlobalWorkspaceError):
    pass


class GlobalWorkspaceInvalid(GlobalWorkspaceError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class GlobalWorkspaceState:
    solution: Solution
    project: SolutionBuilderProject
    can_rollback: bool
    last_applied_at: datetime | None


@dataclass(frozen=True)
class GlobalWorkspaceApplyResult:
    revision_id: UUID
    changed_paths: list[str]
    applied_at: datetime
    rolled_back: bool = False


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def validate_global_workspace(base: Path, candidate: Path) -> list[str]:
    """Validate a proposal without executing any proposed code."""
    errors: list[str] = []
    base_files = _file_map(base)
    candidate_files = _file_map(candidate)

    protected = sorted(
        path
        for path in set(base_files) | set(candidate_files)
        if path == ".bifrost" or path.startswith(".bifrost/")
    )
    changed_protected = [
        path for path in protected if base_files.get(path) != candidate_files.get(path)
    ]
    if changed_protected:
        errors.append(
            ".bifrost manifests are read-only: " + ", ".join(changed_protected[:20])
        )

    excluded = sorted(path for path in candidate_files if is_excluded_path(path))
    if excluded:
        errors.append("proposal contains excluded paths: " + ", ".join(excluded[:20]))

    for path, content in candidate_files.items():
        if not path.endswith(".py"):
            continue
        try:
            ast.parse(content, filename=path)
        except (SyntaxError, ValueError) as exc:
            line = f" line {exc.lineno}" if isinstance(exc, SyntaxError) else ""
            message = exc.msg if isinstance(exc, SyntaxError) else str(exc)
            errors.append(f"{path}{line}: invalid Python: {message}")
    return errors


async def _repo_map(*, limits: WorkspaceLimits) -> dict[str, bytes]:
    repo = RepoStorage()
    paths = sorted(path for path in await repo.list() if not is_excluded_path(path))
    if len(paths) > limits.max_files:
        raise GlobalWorkspaceInvalid(["Global workspace exceeds the file-count limit"])
    files: dict[str, bytes] = {}
    total_bytes = 0
    for path in paths:
        content = await repo.read(path)
        total_bytes += len(content)
        if total_bytes > limits.max_total_bytes:
            raise GlobalWorkspaceInvalid(["Global workspace exceeds the size limit"])
        files[path] = content
    return files


async def _repo_archive(
    destination: Path,
    *,
    limits: WorkspaceLimits,
) -> str:
    workspace = destination.parent / f"{destination.stem}-workspace"
    workspace.mkdir(mode=0o700)
    root = WorkspaceRoot(workspace, limits)
    for path, content in (await _repo_map(limits=limits)).items():
        root.write_file(path, content)
    return zip_workspace(workspace, destination)


async def _materialize_revision(
    solution_id: UUID,
    revision_id: UUID,
    destination: Path,
    *,
    limits: WorkspaceLimits,
) -> Path:
    destination.mkdir(mode=0o700, parents=True)
    archive = destination / f"{revision_id}.zip"
    if not await SolutionRevisionStorage(solution_id).copy_to_path(revision_id, archive):
        raise GlobalWorkspaceError(f"Global workspace revision {revision_id} is missing")
    workspace = destination / str(revision_id)
    workspace.mkdir(mode=0o700)
    safe_extract_zip(archive, workspace, limits)
    return workspace


async def _create_snapshot_revision(
    db: AsyncSession,
    *,
    solution_id: UUID,
    parent_revision_id: UUID | None,
    created_by: UUID,
    summary: str,
    limits: WorkspaceLimits,
) -> SolutionSourceRevision:
    revision_id = uuid4()
    with tempfile.TemporaryDirectory(prefix="bifrost-global-workspace-") as tmp:
        archive = Path(tmp) / "repo.zip"
        digest = await _repo_archive(archive, limits=limits)
        size = archive.stat().st_size
        await SolutionRevisionStorage(solution_id).write_from_path(revision_id, archive)
    revision = SolutionSourceRevision(
        id=revision_id,
        solution_id=solution_id,
        parent_revision_id=parent_revision_id,
        created_by=created_by,
        source_sha256=digest,
        size_bytes=size,
        summary=summary,
    )
    db.add(revision)
    await db.flush()
    return revision


async def _latest_apply(
    db: AsyncSession,
    solution_id: UUID,
) -> SolutionGlobalWorkspaceApply | None:
    return await db.scalar(
        select(SolutionGlobalWorkspaceApply)
        .where(
            SolutionGlobalWorkspaceApply.solution_id == solution_id,
            SolutionGlobalWorkspaceApply.state == "applied",
        )
        .order_by(SolutionGlobalWorkspaceApply.applied_at.desc())
        .limit(1)
    )


async def global_workspace_state(
    db: AsyncSession,
    *,
    solution_id: UUID | None = None,
) -> GlobalWorkspaceState | None:
    query = (
        select(Solution, SolutionBuilderProject)
        .join(
            SolutionBuilderProject,
            SolutionBuilderProject.solution_id == Solution.id,
        )
        .where(SolutionBuilderProject.target_kind == GLOBAL_TARGET_KIND)
    )
    if solution_id is not None:
        query = query.where(Solution.id == solution_id)
    row = (await db.execute(query)).one_or_none()
    if row is None:
        return None
    latest = await _latest_apply(db, row[0].id)
    return GlobalWorkspaceState(
        solution=row[0],
        project=row[1],
        can_rollback=latest is not None,
        last_applied_at=latest.applied_at if latest else None,
    )


async def ensure_global_workspace(
    db: AsyncSession,
    *,
    owner_user_id: UUID,
    organization_id: UUID | None,
    limits: WorkspaceLimits | None = None,
) -> GlobalWorkspaceState:
    existing = await global_workspace_state(db)
    if existing is not None:
        return existing
    limits = limits or WorkspaceLimits()
    solution = Solution(
        slug=GLOBAL_WORKSPACE_SLUG,
        name=GLOBAL_WORKSPACE_NAME,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        visibility="private",
        global_repo_access=False,
    )
    db.add(solution)
    try:
        await db.flush()
        revision = await _create_snapshot_revision(
            db,
            solution_id=solution.id,
            parent_revision_id=None,
            created_by=owner_user_id,
            summary="initial global workspace snapshot",
            limits=limits,
        )
        project = SolutionBuilderProject(
            solution_id=solution.id,
            current_revision_id=revision.id,
            deployed_revision_id=revision.id,
            target_kind=GLOBAL_TARGET_KIND,
        )
        db.add(project)
        await emit_audit(
            db,
            "builder.global_workspace.create",
            resource_type="solution",
            resource_id=solution.id,
            details={"revision_id": str(revision.id)},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await global_workspace_state(db)
        if winner is None:
            raise
        return winner
    return GlobalWorkspaceState(
        solution=solution,
        project=project,
        can_rollback=False,
        last_applied_at=None,
    )


async def refresh_global_workspace(
    db: AsyncSession,
    *,
    solution_id: UUID,
    requested_by: UUID,
    limits: WorkspaceLimits | None = None,
) -> GlobalWorkspaceState:
    limits = limits or WorkspaceLimits()
    async with solution_write_lock(solution_id):
        state = await global_workspace_state(db, solution_id=solution_id)
        if state is None:
            raise GlobalWorkspaceError("Global workspace not found")
        project = state.project
        if project.current_revision_id != project.deployed_revision_id:
            raise GlobalWorkspaceConflict(
                "Apply or discard the pending proposal before refreshing from live _repo"
            )
        revision = await _create_snapshot_revision(
            db,
            solution_id=solution_id,
            parent_revision_id=project.current_revision_id,
            created_by=requested_by,
            summary="refreshed from live global workspace",
            limits=limits,
        )
        current = (
            await db.get(SolutionSourceRevision, project.current_revision_id)
            if project.current_revision_id
            else None
        )
        if current is not None and current.source_sha256 == revision.source_sha256:
            await SolutionRevisionStorage(solution_id).delete(revision.id)
            await db.delete(revision)
        else:
            project.current_revision_id = revision.id
            project.deployed_revision_id = revision.id
        await db.commit()
    refreshed = await global_workspace_state(db, solution_id=solution_id)
    assert refreshed is not None
    return refreshed


async def validate_global_workspace_revision(
    db: AsyncSession,
    *,
    solution_id: UUID,
    revision_id: UUID,
    limits: WorkspaceLimits | None = None,
) -> list[str]:
    limits = limits or WorkspaceLimits()
    state = await global_workspace_state(db, solution_id=solution_id)
    if state is None or state.project.deployed_revision_id is None:
        raise GlobalWorkspaceError("Global workspace baseline is missing")
    with tempfile.TemporaryDirectory(prefix="bifrost-global-validate-") as tmp:
        root = Path(tmp)
        base = await _materialize_revision(
            solution_id,
            state.project.deployed_revision_id,
            root / "base",
            limits=limits,
        )
        candidate = await _materialize_revision(
            solution_id,
            revision_id,
            root / "candidate",
            limits=limits,
        )
        return validate_global_workspace(base, candidate)


async def _apply_maps(
    db: AsyncSession,
    *,
    before: dict[str, bytes],
    after: dict[str, bytes],
    updated_by: str,
    reject_diagnostics: bool = True,
) -> list[str]:
    storage = FileStorageService(db)
    changed = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    for path in changed:
        if path in after:
            result = await storage.write_file(
                path,
                after[path],
                updated_by=updated_by,
                force_deactivation=True,
            )
            errors = [
                diagnostic.message
                for diagnostic in result.diagnostics or []
                if diagnostic.severity == "error"
            ]
            if errors and reject_diagnostics:
                raise GlobalWorkspaceInvalid([f"{path}: {message}" for message in errors])
        else:
            await storage.delete_file(path)
    return changed


async def _restore_maps(
    db: AsyncSession,
    *,
    current: dict[str, bytes],
    restore: dict[str, bytes],
    updated_by: str,
) -> None:
    await _apply_maps(
        db,
        before=current,
        after=restore,
        updated_by=updated_by,
        reject_diagnostics=False,
    )


async def _compensate_live_repo(
    db: AsyncSession,
    *,
    before: dict[str, bytes],
    proposed: dict[str, bytes],
    updated_by: str,
    limits: WorkspaceLimits,
) -> list[str]:
    """Restore paths still matching our proposal without clobbering other writers."""
    current = await _repo_map(limits=limits)
    restore = dict(current)
    conflicts: list[str] = []
    for path in sorted(
        path
        for path in set(before) | set(proposed)
        if before.get(path) != proposed.get(path)
    ):
        live = current.get(path)
        if live == before.get(path):
            continue
        if live != proposed.get(path):
            conflicts.append(path)
            continue
        if path in before:
            restore[path] = before[path]
        else:
            restore.pop(path, None)
    await _restore_maps(
        db,
        current=current,
        restore=restore,
        updated_by=updated_by,
    )
    await db.commit()
    return conflicts


async def _verify_live_revision(
    *,
    expected_sha256: str,
    limits: WorkspaceLimits,
    prefix: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        archive = Path(tmp) / "live.zip"
        actual = await _repo_archive(archive, limits=limits)
    if actual != expected_sha256:
        raise GlobalWorkspaceConflict(
            "Live _repo does not match the expected reviewed revision"
        )


async def apply_global_workspace(
    db: AsyncSession,
    *,
    solution_id: UUID,
    requested_by: UUID,
    requested_by_email: str,
    limits: WorkspaceLimits | None = None,
) -> GlobalWorkspaceApplyResult:
    limits = limits or WorkspaceLimits()
    async with solution_write_lock(solution_id):
        state = await global_workspace_state(db, solution_id=solution_id)
        if state is None:
            raise GlobalWorkspaceError("Global workspace not found")
        project = state.project
        if project.current_revision_id is None or project.deployed_revision_id is None:
            raise GlobalWorkspaceError("Global workspace revisions are missing")
        if project.current_revision_id == project.deployed_revision_id:
            raise GlobalWorkspaceConflict("There is no pending proposal to apply")
        baseline = await db.get(SolutionSourceRevision, project.deployed_revision_id)
        proposal = await db.get(SolutionSourceRevision, project.current_revision_id)
        if baseline is None or proposal is None:
            raise GlobalWorkspaceError("Global workspace revisions are missing")
        before: dict[str, bytes] | None = None
        after: dict[str, bytes] | None = None
        mutation_started = False
        try:
            with tempfile.TemporaryDirectory(prefix="bifrost-global-apply-") as tmp:
                root = Path(tmp)
                live_archive = root / "live.zip"
                live_sha = await _repo_archive(live_archive, limits=limits)
                if live_sha != baseline.source_sha256:
                    raise GlobalWorkspaceConflict(
                        "Live _repo changed after this proposal began. "
                        "Refresh and review a new diff."
                    )
                baseline_root = await _materialize_revision(
                    solution_id,
                    baseline.id,
                    root / "baseline",
                    limits=limits,
                )
                proposal_root = await _materialize_revision(
                    solution_id,
                    proposal.id,
                    root / "proposal",
                    limits=limits,
                )
                errors = validate_global_workspace(baseline_root, proposal_root)
                if errors:
                    raise GlobalWorkspaceInvalid(errors)
                before = _file_map(baseline_root)
                after = _file_map(proposal_root)
                mutation_started = True
                changed = await _apply_maps(
                    db,
                    before=before,
                    after=after,
                    updated_by=requested_by_email,
                )
                actual_archive = root / "actual.zip"
                actual_sha = await _repo_archive(actual_archive, limits=limits)
                if actual_sha != proposal.source_sha256:
                    raise GlobalWorkspaceConflict(
                        "Live _repo did not match the reviewed proposal after apply"
                    )

            await db.execute(
                update(SolutionGlobalWorkspaceApply)
                .where(
                    SolutionGlobalWorkspaceApply.solution_id == solution_id,
                    SolutionGlobalWorkspaceApply.state == "applied",
                )
                .values(state="superseded")
            )
            applied_at = datetime.now(timezone.utc)
            db.add(
                SolutionGlobalWorkspaceApply(
                    solution_id=solution_id,
                    from_revision_id=baseline.id,
                    to_revision_id=proposal.id,
                    applied_by=requested_by,
                    applied_at=applied_at,
                )
            )
            project.deployed_revision_id = proposal.id
            await emit_audit(
                db,
                "builder.global_workspace.apply",
                resource_type="solution",
                resource_id=solution_id,
                details={"revision_id": str(proposal.id), "changed_paths": changed},
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - compensate cross-store writes
            await db.rollback()
            if mutation_started and before is not None and after is not None:
                try:
                    conflicts = await _compensate_live_repo(
                        db,
                        before=before,
                        proposed=after,
                        updated_by="builder-rollback@gobifrost.local",
                        limits=limits,
                    )
                    if conflicts:
                        joined = ", ".join(conflicts[:20])
                        raise GlobalWorkspaceConflict(
                            "Apply failed and concurrent edits prevented a full automatic "
                            f"restore. Review these paths: {joined}"
                        ) from exc
                    await _verify_live_revision(
                        expected_sha256=baseline.source_sha256,
                        limits=limits,
                        prefix="bifrost-global-apply-verify-",
                    )
                except GlobalWorkspaceConflict:
                    raise
                except Exception as restore_exc:  # noqa: BLE001
                    await db.rollback()
                    raise GlobalWorkspaceError(
                        "Apply failed and the live workspace could not be restored"
                    ) from restore_exc
            raise
        return GlobalWorkspaceApplyResult(
            revision_id=proposal.id,
            changed_paths=changed,
            applied_at=applied_at,
        )


async def rollback_global_workspace(
    db: AsyncSession,
    *,
    solution_id: UUID,
    requested_by: UUID,
    requested_by_email: str,
    limits: WorkspaceLimits | None = None,
) -> GlobalWorkspaceApplyResult:
    limits = limits or WorkspaceLimits()
    async with solution_write_lock(solution_id):
        state = await global_workspace_state(db, solution_id=solution_id)
        latest = await _latest_apply(db, solution_id)
        if state is None or latest is None:
            raise GlobalWorkspaceConflict("There is no applied proposal to roll back")
        if state.project.current_revision_id != state.project.deployed_revision_id:
            raise GlobalWorkspaceConflict(
                "Apply or discard the pending proposal before rolling back live _repo"
            )
        from_revision = await db.get(SolutionSourceRevision, latest.from_revision_id)
        to_revision = await db.get(SolutionSourceRevision, latest.to_revision_id)
        if from_revision is None or to_revision is None:
            raise GlobalWorkspaceError("Rollback revisions are missing")
        before: dict[str, bytes] | None = None
        after: dict[str, bytes] | None = None
        mutation_started = False
        restored_id: UUID | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="bifrost-global-rollback-") as tmp:
                root = Path(tmp)
                live_archive = root / "live.zip"
                live_sha = await _repo_archive(live_archive, limits=limits)
                if live_sha != to_revision.source_sha256:
                    raise GlobalWorkspaceConflict(
                        "Live _repo changed after this apply. Refresh instead of "
                        "overwriting newer work."
                    )
                from_root = await _materialize_revision(
                    solution_id,
                    from_revision.id,
                    root / "from",
                    limits=limits,
                )
                to_root = await _materialize_revision(
                    solution_id,
                    to_revision.id,
                    root / "to",
                    limits=limits,
                )
                before = _file_map(to_root)
                after = _file_map(from_root)
                mutation_started = True
                changed = await _apply_maps(
                    db,
                    before=before,
                    after=after,
                    updated_by=requested_by_email,
                )
                actual_archive = root / "actual.zip"
                actual_sha = await _repo_archive(actual_archive, limits=limits)
                if actual_sha != from_revision.source_sha256:
                    raise GlobalWorkspaceConflict(
                        "Live _repo did not match the reviewed rollback revision"
                    )
                restored_id = uuid4()
                restored_archive = root / "restored.zip"
                zip_workspace(from_root, restored_archive)
                restored_size = restored_archive.stat().st_size
                await SolutionRevisionStorage(solution_id).write_from_path(
                    restored_id,
                    restored_archive,
                )

            assert restored_id is not None
            restored = SolutionSourceRevision(
                id=restored_id,
                solution_id=solution_id,
                parent_revision_id=to_revision.id,
                restored_from_revision_id=from_revision.id,
                created_by=requested_by,
                source_sha256=from_revision.source_sha256,
                size_bytes=restored_size,
                summary=f"rolled back applied revision {to_revision.id}",
            )
            db.add(restored)
            await db.flush()
            state.project.current_revision_id = restored.id
            state.project.deployed_revision_id = restored.id
            latest.state = "rolled_back"
            latest.rolled_back_by = requested_by
            rolled_back_at = datetime.now(timezone.utc)
            latest.rolled_back_at = rolled_back_at
            await emit_audit(
                db,
                "builder.global_workspace.rollback",
                resource_type="solution",
                resource_id=solution_id,
                details={
                    "from_revision_id": str(to_revision.id),
                    "restored_revision_id": str(restored.id),
                    "changed_paths": changed,
                },
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - compensate cross-store writes
            await db.rollback()
            if restored_id is not None:
                await SolutionRevisionStorage(solution_id).delete(restored_id)
            if mutation_started and before is not None and after is not None:
                try:
                    conflicts = await _compensate_live_repo(
                        db,
                        before=before,
                        proposed=after,
                        updated_by="builder-rollback@gobifrost.local",
                        limits=limits,
                    )
                    if conflicts:
                        joined = ", ".join(conflicts[:20])
                        raise GlobalWorkspaceConflict(
                            "Rollback failed and concurrent edits prevented a full "
                            f"automatic restore. Review these paths: {joined}"
                        ) from exc
                    await _verify_live_revision(
                        expected_sha256=to_revision.source_sha256,
                        limits=limits,
                        prefix="bifrost-global-rollback-verify-",
                    )
                except GlobalWorkspaceConflict:
                    raise
                except Exception as restore_exc:  # noqa: BLE001
                    await db.rollback()
                    raise GlobalWorkspaceError(
                        "Rollback failed and the live workspace could not be restored"
                    ) from restore_exc
            raise
        return GlobalWorkspaceApplyResult(
            revision_id=restored.id,
            changed_paths=changed,
            applied_at=rolled_back_at,
            rolled_back=True,
        )


__all__ = [
    "GLOBAL_TARGET_KIND",
    "GlobalWorkspaceApplyResult",
    "GlobalWorkspaceConflict",
    "GlobalWorkspaceError",
    "GlobalWorkspaceInvalid",
    "GlobalWorkspaceState",
    "apply_global_workspace",
    "ensure_global_workspace",
    "global_workspace_state",
    "refresh_global_workspace",
    "rollback_global_workspace",
    "validate_global_workspace",
    "validate_global_workspace_revision",
]
