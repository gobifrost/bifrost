"""
Git Sync Service

GitPython-based synchronization. S3 _repo/ is the persistent working tree.

Key principles:
1. Git working tree is source of truth during sync operations
2. GitPython for clone/pull/push/commit
3. Conflict detection with user resolution
4. .bifrost/ split manifest files declare entity identity
5. Preflight validates repo health (syntax, lint, refs, orphans)
"""

import hashlib
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping
from uuid import uuid4

import yaml
from git import Repo as GitRepo
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.models.contracts.github import (
    GitConnectItem,
    GitConnectPreview,
    GitConnectRequest,
    PreflightIssue,
    PreflightResult,
    WorkspaceFileChange,
    WorkspaceSyncPlan,
)
from src.services.git_repo_manager import GitRepoManager, hash_file, iter_tree_metadata
from src.services.github_sync_entity_metadata import extract_entity_metadata

if TYPE_CHECKING:
    from src.models.contracts.github import (
        AbortMergeResult,
        CommitResult,
        DiscardResult,
        DiffResult,
        FetchResult,
        PullResult,
        PushResult,
        ResolveResult,
        SyncResult,
        WorkingTreeStatus,
    )
    from src.services.sync_ops import SyncOp

from bifrost.manifest import (
    Manifest,
    get_all_entity_ids,
    read_manifest_from_dir,
)
from src.services.manifest_import import (
    ManifestResolver,
)


logger = logging.getLogger(__name__)


# Keep changed text content bounded while building PostgreSQL upsert statements.
# The content of one large searchable file necessarily lives in memory for its
# database write, but unrelated files must never accumulate behind it.
FILE_INDEX_UPSERT_MAX_ROWS = 100
FILE_INDEX_UPSERT_MAX_BYTES = 8 * 1024 * 1024
GIT_CONNECT_PREVIEW_TTL = timedelta(minutes=30)
_GIT_CONNECT_PREVIEW_KEY_PREFIX = "bifrost:git-connect-preview:"


# =============================================================================
# Errors
# =============================================================================


class SyncError(Exception):
    """Error during sync operation."""
    pass


class GitStatusError(SyncError):
    """Git status could not be read or parsed safely."""
    pass


class WorkspaceMergeConflict(SyncError):
    """The remote merge must be resolved before a workspace sync can continue."""

    def __init__(self, conflicts: list) -> None:
        super().__init__("Merge conflicts detected")
        self.conflicts = conflicts


class WorkspacePlanStale(SyncError):
    """A reviewed sync plan no longer matches the checked-out workspace."""
    pass


class GitConnectPreviewError(SyncError):
    """A first-connect preview is unavailable, expired, or not owned by this caller."""


class GitConnectPreviewStale(SyncError):
    """The workspace or remote changed after the user reviewed the preview."""


class GitConnectDecisionError(SyncError):
    """A chosen connect strategy is unsafe or lacks required decisions."""


class _GitConnectPreviewRecord(BaseModel):
    token: str
    repository_url: str
    branch: str
    requested_by_user_id: str
    organization_id: str | None
    expires_at: datetime
    local_fingerprint: str
    remote_fingerprint: str
    remote_head_sha: str | None
    items: list[GitConnectItem]


def _delete_keys(changes: list) -> set[tuple[str, str]]:
    """Return the stable identities that a deletion confirmation authorizes."""
    keys = {
        (change.entity_type, change.entity_id)
        for change in changes
        if change.action == "removed" and change.entity_id is not None
    }
    if len(keys) != len(changes):
        raise WorkspacePlanStale("pending entity deletions lack stable identities")
    return keys


# =============================================================================
# Helpers
# =============================================================================


def _workspace_fingerprint(root: Path) -> str:
    """Hash every workspace path and byte sequence, excluding Git internals."""
    digest = hashlib.sha256()
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )
    for source in paths:
        path = source.relative_to(root).as_posix()
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        with source.open("rb") as file:
            while chunk := file.read(8 * 1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _connect_tree_hashes(root: Path) -> dict[str, str]:
    """Hash one workspace tree while refusing symlinked first-connect input."""
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            raise GitConnectPreviewError(
                f"first Git connection does not support symlinks ({relative.as_posix()})"
            )
        if path.is_file():
            files[relative.as_posix()] = hash_file(path)[1]
    return files


def _connect_tree_fingerprint(tree: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path, sha256 in sorted(tree.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _connect_shape_conflicts(
    local: Mapping[str, str], remote: Mapping[str, str],
) -> list[str]:
    """Return paths that are a file on one side and a directory on the other."""
    conflicts: set[str] = set()
    for files, other_files in ((local, remote), (remote, local)):
        for path in other_files:
            components = path.split("/")
            for index in range(1, len(components)):
                ancestor = "/".join(components[:index])
                if ancestor in files:
                    conflicts.add(ancestor)
    return sorted(conflicts)


def classify_connect_trees(
    local: Mapping[str, str], remote: Mapping[str, str],
) -> list[GitConnectItem]:
    """Classify the union of detached local and reviewed remote tree hashes."""
    shape_conflicts = _connect_shape_conflicts(local, remote)
    if shape_conflicts:
        raise GitConnectPreviewError(
            "first Git connection cannot reconcile file/directory shape conflict(s): "
            + ", ".join(shape_conflicts)
        )
    items: list[GitConnectItem] = []
    for path in sorted(set(local) | set(remote)):
        local_hash = local.get(path)
        remote_hash = remote.get(path)
        if local_hash is None:
            classification: Literal["local_only", "remote_only", "identical", "conflict"] = "remote_only"
        elif remote_hash is None:
            classification = "local_only"
        elif local_hash == remote_hash:
            classification = "identical"
        else:
            classification = "conflict"
        items.append(GitConnectItem(
            path=path,
            classification=classification,
            local_sha256=local_hash,
            remote_sha256=remote_hash,
        ))
    return items


def resolve_connect_items(
    items: list[GitConnectItem],
    *,
    strategy: Literal["publish_local", "start_from_remote", "reconcile"],
    decisions: Mapping[str, Literal["local", "remote"]],
) -> dict[str, Literal["local", "remote"]]:
    """Validate decisions and return the source selected for each conflicting path."""
    conflicts = {item.path for item in items if item.classification == "conflict"}
    if strategy != "reconcile":
        if decisions:
            raise GitConnectDecisionError("path decisions are only valid for reconcile")
        return {}
    if set(decisions) != conflicts:
        missing = sorted(conflicts - set(decisions))
        extra = sorted(set(decisions) - conflicts)
        detail = []
        if missing:
            detail.append("missing conflict decisions: " + ", ".join(missing))
        if extra:
            detail.append("unknown conflict decisions: " + ", ".join(extra))
        raise GitConnectDecisionError("; ".join(detail))
    return dict(decisions)


# =============================================================================
# Git Sync Service
# =============================================================================


class GitHubSyncService:
    """
    Git sync service using GitPython.

    All git operations go through GitPython against repo_url.
    The working tree is serialized to/from S3 _repo/ between operations.
    """

    def __init__(
        self,
        db: AsyncSession,
        repo_url: str,
        branch: str = "main",
        settings: Settings | None = None,
    ):
        self.db = db
        self.repo_url = repo_url
        self.branch = branch
        self.repo_manager = GitRepoManager(settings or get_settings())
        self._resolver = ManifestResolver(db)

    @staticmethod
    def _connect_preview_key(token: str) -> str:
        return f"{_GIT_CONNECT_PREVIEW_KEY_PREFIX}{token}"

    @staticmethod
    def _clone_connect_remote(destination: Path, repository_url: str, branch: str) -> GitRepo | None:
        """Clone the reviewed remote branch, treating an empty remote as an empty tree."""
        try:
            repo = GitRepo.clone_from(repository_url, str(destination), branch=branch)
        except Exception as exc:
            message = str(exc).lower()
            if "empty repository" in message:
                return None
            # Git reports both an empty repository and a missing branch as
            # "remote branch ... not found" when a branch is requested.  A
            # branchless clone distinguishes those cases without trusting the
            # error text as the decision.
            if "remote branch" in message:
                if destination.exists():
                    shutil.rmtree(destination)
                try:
                    fallback = GitRepo.clone_from(repository_url, str(destination))
                except Exception as fallback_exc:
                    if "empty repository" in str(fallback_exc).lower():
                        return None
                    raise GitConnectPreviewError(
                        f"could not read remote branch {branch}: {fallback_exc}"
                    ) from fallback_exc
                if not fallback.head.is_valid():
                    return None
            raise GitConnectPreviewError(f"could not read remote branch {branch}: {exc}") from exc
        return repo

    @classmethod
    async def load_connect_preview(
        cls,
        token: str,
        *,
        requested_by_user_id: str,
        organization_id: str | None,
    ) -> _GitConnectPreviewRecord:
        """Load an opaque preview only for its requester and original organization."""
        from src.core.cache.redis_client import get_shared_redis

        redis = await get_shared_redis()
        raw = await redis.get(cls._connect_preview_key(token))
        if raw is None:
            raise GitConnectPreviewError("Git connection preview was not found or has expired")
        try:
            record = _GitConnectPreviewRecord.model_validate_json(raw)
        except Exception as exc:
            raise GitConnectPreviewError("Git connection preview is invalid") from exc
        if record.expires_at <= datetime.now(timezone.utc):
            await redis.delete(cls._connect_preview_key(token))
            raise GitConnectPreviewError("Git connection preview has expired")
        if (
            record.requested_by_user_id != requested_by_user_id
            or record.organization_id != organization_id
        ):
            raise GitConnectPreviewError("Git connection preview was not found")
        return record

    async def preview_connect(
        self,
        repository_url: str,
        branch: str,
        *,
        requested_by_user_id: str,
        organization_id: str | None,
    ) -> GitConnectPreview:
        """Compare a detached workspace and remote branch without changing either."""
        async with self.repo_manager.checkout_readonly() as work_dir:
            local = _connect_tree_hashes(work_dir)
            with tempfile.TemporaryDirectory(prefix="bifrost-connect-preview-") as raw_remote:
                remote_root = Path(raw_remote) / "remote"
                remote_repo = self._clone_connect_remote(remote_root, self.repo_url, branch)
                remote = {} if remote_repo is None else _connect_tree_hashes(remote_root)
                remote_head_sha = (
                    remote_repo.head.commit.hexsha
                    if remote_repo is not None and remote_repo.head.is_valid()
                    else None
                )

        items = classify_connect_trees(local, remote)
        token = str(uuid4())
        record = _GitConnectPreviewRecord(
            token=token,
            repository_url=repository_url,
            branch=branch,
            requested_by_user_id=requested_by_user_id,
            organization_id=organization_id,
            expires_at=datetime.now(timezone.utc) + GIT_CONNECT_PREVIEW_TTL,
            local_fingerprint=_connect_tree_fingerprint(local),
            remote_fingerprint=_connect_tree_fingerprint(remote),
            remote_head_sha=remote_head_sha,
            items=items,
        )
        from src.core.cache.redis_client import get_shared_redis

        redis = await get_shared_redis()
        await redis.setex(
            self._connect_preview_key(token),
            int(GIT_CONNECT_PREVIEW_TTL.total_seconds()),
            record.model_dump_json(),
        )
        return GitConnectPreview(
            token=token,
            repository_url=repository_url,
            branch=branch,
            state=(
                "ready"
                if all(item.classification == "identical" for item in items)
                else "requires_reconciliation"
            ),
            items=items,
        )

    @staticmethod
    def _replace_connect_workspace(destination: Path, source: Path) -> None:
        """Replace the checked-out workspace from a reviewed, symlink-free tree."""
        for child in destination.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in source.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

    @staticmethod
    def _copy_connect_file(source_root: Path, destination_root: Path, path: str) -> None:
        source = source_root / path
        if not source.is_file():
            raise GitConnectPreviewStale(f"reviewed source file disappeared: {path}")
        destination = destination_root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        shutil.copy2(source, destination)

    def _configure_connect_repo(self, repo: GitRepo) -> None:
        if "origin" in [remote.name for remote in repo.remotes]:
            repo.remotes.origin.set_url(self.repo_url)
        else:
            repo.create_remote("origin", self.repo_url)
        with repo.config_writer() as writer:
            writer.set_value("user", "name", "Bifrost")
            writer.set_value("user", "email", "bifrost@localhost")

    async def _commit_connect_tree(self, work_dir: Path, repo: GitRepo) -> None:
        """Commit reviewed files without regenerating a manifest from old DB state."""
        repo.git.add(A=True)
        if repo.head.is_valid() and not repo.index.diff("HEAD") and not repo.untracked_files:
            return
        preflight = await self._run_preflight(work_dir)
        if not preflight.valid:
            raise GitConnectDecisionError("reviewed workspace failed preflight validation")
        repo.index.commit("Connect Bifrost workspace")

    async def desktop_connect(
        self,
        request: GitConnectRequest,
        *,
        requested_by_user_id: str,
        organization_id: str | None,
        progress_fn=None,
    ) -> "SyncResult":
        """Materialize a reviewed first connection, then use the normal sync apply path."""
        record = await self.load_connect_preview(
            request.preview_token,
            requested_by_user_id=requested_by_user_id,
            organization_id=organization_id,
        )
        decisions = resolve_connect_items(
            record.items, strategy=request.strategy, decisions=request.decisions
        )
        async with self.repo_manager.checkout() as work_dir:
            local = _connect_tree_hashes(work_dir)
            if _connect_tree_fingerprint(local) != record.local_fingerprint:
                raise GitConnectPreviewStale("workspace changed after the connection preview")
            with tempfile.TemporaryDirectory(prefix="bifrost-connect-apply-") as raw_remote:
                remote_root = Path(raw_remote) / "remote"
                remote_repo = self._clone_connect_remote(remote_root, self.repo_url, record.branch)
                remote = {} if remote_repo is None else _connect_tree_hashes(remote_root)
                remote_head_sha = (
                    remote_repo.head.commit.hexsha
                    if remote_repo is not None and remote_repo.head.is_valid()
                    else None
                )
                if (
                    _connect_tree_fingerprint(remote) != record.remote_fingerprint
                    or remote_head_sha != record.remote_head_sha
                ):
                    raise GitConnectPreviewStale("remote branch changed after the connection preview")

                if request.strategy == "publish_local":
                    if remote_head_sha is not None or remote:
                        raise GitConnectDecisionError(
                            "publish_local refuses a nonempty remote branch; choose reconcile instead"
                        )
                    git_dir = work_dir / ".git"
                    if git_dir.exists():
                        shutil.rmtree(git_dir)
                    repo = GitRepo.init(str(work_dir))
                    self._configure_connect_repo(repo)
                    await self._commit_connect_tree(work_dir, repo)
                elif request.strategy == "start_from_remote":
                    discards_local = any(
                        item.classification in {"local_only", "conflict"}
                        for item in record.items
                    )
                    if discards_local and not request.confirm_destructive:
                        raise GitConnectDecisionError(
                            "start_from_remote would discard local content; set confirm_destructive"
                        )
                    if remote_repo is None:
                        raise GitConnectDecisionError(
                            "start_from_remote requires a nonempty remote branch"
                        )
                    self._replace_connect_workspace(work_dir, remote_root)
                    repo = GitRepo(str(work_dir))
                    self._configure_connect_repo(repo)
                else:
                    if remote_repo is None:
                        git_dir = work_dir / ".git"
                        if git_dir.exists():
                            shutil.rmtree(git_dir)
                        repo = GitRepo.init(str(work_dir))
                        self._configure_connect_repo(repo)
                        await self._commit_connect_tree(work_dir, repo)
                    else:
                        local_root = Path(raw_remote) / "local"
                        local_root.mkdir()
                        for item in record.items:
                            if item.local_sha256 is not None:
                                self._copy_connect_file(work_dir, local_root, item.path)
                        self._replace_connect_workspace(work_dir, remote_root)
                        for item in record.items:
                            if item.classification == "local_only" or (
                                item.classification == "conflict" and decisions[item.path] == "local"
                            ):
                                self._copy_connect_file(local_root, work_dir, item.path)
                        repo = GitRepo(str(work_dir))
                        self._configure_connect_repo(repo)
                        await self._commit_connect_tree(work_dir, repo)

                if progress_fn:
                    await progress_fn("Validating reconciled workspace")
                plan = await self.prepare_desktop_sync(work_dir, repo, progress_fn=progress_fn)
                return await self.apply_desktop_sync(
                    work_dir,
                    repo,
                    plan,
                    confirm_deletes=False,
                    progress_fn=progress_fn,
                )

    # -----------------------------------------------------------------
    # Preflight: validate repo health
    # -----------------------------------------------------------------

    async def preflight(
        self,
    ) -> PreflightResult:
        """
        Validate the remote repo's health without syncing.

        Uses GitRepoManager to restore persistent .git/ from S3.
        Checks: Python syntax, ruff lint, UUID ref resolution, manifest validity.
        """
        async with self.repo_manager.checkout() as work_dir:
            if not (work_dir / ".git").exists():
                self._clone_or_init(work_dir)
            return await self._run_preflight(work_dir)

    # -----------------------------------------------------------------
    # Desktop-style operations: fetch, status, commit, pull, push, resolve, diff
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # Internal helpers: core logic extracted from desktop_* methods.
    # Accept work_dir/repo so callers can share a single checkout.
    # -----------------------------------------------------------------

    def _do_fetch(self, work_dir: Path, repo: GitRepo) -> "FetchResult":
        """Core fetch logic. Fetches remote and computes ahead/behind."""
        from src.models.contracts.github import FetchResult

        remote_exists = True
        try:
            repo.remotes.origin.fetch(self.branch)
        except Exception as e:
            err_str = str(e).lower()
            if "not found" in err_str or "empty" in err_str or "couldn't find remote ref" in err_str:
                remote_exists = False
            else:
                raise

        ahead = 0
        behind = 0
        if remote_exists and repo.head.is_valid():
            try:
                ahead = int(repo.git.rev_list("--count", f"origin/{self.branch}..HEAD"))
            except Exception:
                ahead = 0
            try:
                behind = int(repo.git.rev_list("--count", f"HEAD..origin/{self.branch}"))
            except Exception:
                behind = 0

        return FetchResult(
            success=True,
            commits_ahead=ahead,
            commits_behind=behind,
            remote_branch_exists=remote_exists,
        )

    def _do_status(self, work_dir: Path, repo: GitRepo) -> "WorkingTreeStatus":
        """Core status logic. Returns changed files and conflicts."""
        from src.models.contracts.github import ChangedFile, MergeConflict, WorkingTreeStatus

        def _read_stage(path: str, stage: int) -> str:
            try:
                return repo.git.show(f":{stage}:{path}")
            except Exception as e:
                raise GitStatusError(
                    f"status failed reading conflict stage {stage} for {path}: {e}"
                ) from e

        try:
            # `git status` normally refreshes index stat information. Disable optional
            # locks so inspection cannot rewrite the index while gathering status.
            porcelain = repo.git.status(
                "--porcelain=v2", "-z", env={"GIT_OPTIONAL_LOCKS": "0"}
            )
        except Exception as e:
            raise GitStatusError(f"status failed: {e}") from e

        if porcelain and not porcelain.endswith("\0"):
            raise GitStatusError("status failed: malformed porcelain-v2 output")

        conflict_types = {
            "UU": "both_modified",
            "AA": "both_added",
            "UD": "deleted_by_them",
            "DU": "deleted_by_us",
            "AU": "both_modified",
            "UA": "both_modified",
            "DD": "both_modified",
        }
        ours_stages = {"UU", "AA", "UD", "AU"}
        theirs_stages = {"UU", "AA", "DU", "UA"}
        changed: list[ChangedFile] = []
        conflicts: list[MergeConflict] = []
        records = porcelain.split("\0")
        record_index = 0
        while record_index < len(records) - 1:
            record = records[record_index]
            record_index += 1
            if not record:
                raise GitStatusError("status failed: malformed empty porcelain-v2 record")

            status_code = ""
            path: str | None = None
            if record.startswith("1 "):
                fields = record.split(" ", 8)
                if len(fields) != 9 or len(fields[1]) != 2 or not fields[8]:
                    raise GitStatusError("status failed: malformed porcelain-v2 ordinary record")
                status_code = fields[1]
                path = fields[8]
            elif record.startswith("2 "):
                fields = record.split(" ", 9)
                if (
                    len(fields) != 10
                    or len(fields[1]) != 2
                    or not fields[9]
                    or record_index >= len(records) - 1
                    or not records[record_index]
                ):
                    raise GitStatusError("status failed: malformed porcelain-v2 rename record")
                status_code = fields[1]
                path = fields[9]
                # A rename/copy record is followed by its original path.
                record_index += 1
            elif record.startswith("u "):
                fields = record.split(" ", 10)
                if len(fields) != 11 or fields[1] not in conflict_types or not fields[10]:
                    raise GitStatusError("status failed: malformed porcelain-v2 conflict record")
                status_code = fields[1]
                path = fields[10]
                metadata = extract_entity_metadata(path)
                conflicts.append(MergeConflict(
                    path=path,
                    ours_content=_read_stage(path, 2) if status_code in ours_stages else None,
                    theirs_content=_read_stage(path, 3) if status_code in theirs_stages else None,
                    display_name=metadata.display_name,
                    entity_type=metadata.entity_type,
                    conflict_type=conflict_types[status_code],
                ))
                continue
            elif record.startswith("? "):
                if not record[2:]:
                    raise GitStatusError("status failed: malformed porcelain-v2 untracked record")
                status_code = "?"
                path = record[2:]
            elif record.startswith("! "):
                if not record[2:]:
                    raise GitStatusError("status failed: malformed porcelain-v2 ignored record")
                continue
            else:
                raise GitStatusError("status failed: unknown porcelain-v2 record")

            if status_code != "?" and any(code not in ".MTADRCU" for code in status_code):
                raise GitStatusError("status failed: invalid porcelain-v2 status code")
            if path is None:
                raise GitStatusError("status failed: missing porcelain-v2 path")
            if status_code == "?" or "A" in status_code:
                change_type = "added"
            elif "D" in status_code:
                change_type = "deleted"
            elif "R" in status_code:
                change_type = "renamed"
            else:
                change_type = "modified"

            metadata = extract_entity_metadata(path)
            changed.append(ChangedFile(
                path=path,
                change_type=change_type,
                display_name=metadata.display_name,
                entity_type=metadata.entity_type,
            ))

        try:
            merging = (work_dir / ".git" / "MERGE_HEAD").exists()
            ahead = 0
            behind = 0
            if repo.head.is_valid():
                try:
                    ahead = int(repo.git.rev_list("--count", f"origin/{self.branch}..HEAD"))
                except Exception as e:
                    # No origin/<branch> ref locally (never fetched) — leave ahead=0
                    logger.debug(f"could not compute commits ahead of origin/{self.branch}: {e}")
                try:
                    behind = int(repo.git.rev_list("--count", f"HEAD..origin/{self.branch}"))
                except Exception as e:
                    # No origin/<branch> ref locally — leave behind=0
                    logger.debug(f"could not compute commits behind origin/{self.branch}: {e}")
        except GitStatusError:
            raise
        except Exception as e:
            raise GitStatusError(f"status failed: {e}") from e

        return WorkingTreeStatus(
            changed_files=changed,
            total_changes=len(changed),
            conflicts=conflicts,
            commits_ahead=ahead,
            commits_behind=behind,
            merging=merging,
        )

    async def _do_commit(self, work_dir: Path, repo: GitRepo, message: str) -> "CommitResult":
        """Core commit logic. Stages, runs preflight, commits."""
        from src.models.contracts.github import CommitResult

        # Regenerate manifest from DB so the commit captures current platform state
        await self._regenerate_manifest_to_dir(self.db, work_dir)
        repo.git.add(A=True)

        # Check if there are changes to commit
        if repo.head.is_valid() and not repo.index.diff("HEAD") and not repo.untracked_files:
            return CommitResult(success=True, files_committed=0)

        # Run preflight
        pf = await self._run_preflight(work_dir)
        if not pf.valid:
            return CommitResult(success=False, error="Preflight validation failed", preflight=pf)

        # Count files
        if repo.head.is_valid():
            file_count = len(repo.index.diff("HEAD")) + len(repo.untracked_files)
        else:
            file_count = len(repo.untracked_files) + len(list(repo.index.diff(None)))

        # Commit
        commit = repo.index.commit(message)

        logger.info(f"Committed {file_count} files: {commit.hexsha[:8]}")
        return CommitResult(
            success=True,
            commit_sha=commit.hexsha,
            files_committed=max(file_count, 1),
            preflight=pf,
        )

    async def _do_pull(self, work_dir: Path, repo: GitRepo, progress_fn=None) -> "PullResult":
        """Core pull logic. Fetches, merges, imports entities."""
        from src.models.contracts.github import PullResult

        async def _progress(phase: str, current: int = 0, total: int = 0) -> None:
            if progress_fn:
                await progress_fn(phase, current, total)

        # NOTE: We intentionally do NOT regenerate the manifest here.
        # The sync_execute flow commits first (which regenerates the manifest),
        # then calls _do_pull. Regenerating here would overwrite the
        # manifest with DB state and stash it, causing conflicts with remote.

        # Fetch first
        await _progress("Fetching remote...")
        remote_exists = True
        try:
            repo.remotes.origin.fetch(self.branch)
        except Exception as e:
            err_str = str(e).lower()
            if "not found" in err_str or "empty" in err_str or "couldn't find remote ref" in err_str:
                remote_exists = False
            else:
                raise

        if not remote_exists:
            return PullResult(success=True, pulled=0)

        await _progress("Merging changes...")
        try:
            merge_output = repo.git.merge(f"origin/{self.branch}")
            logger.info(f"Merge succeeded: {merge_output[:200] if merge_output else 'no output'}")
        except Exception as merge_err:
            logger.info(f"Merge failed: {merge_err}")
            is_merge_conflict = (work_dir / ".git" / "MERGE_HEAD").exists()

            if is_merge_conflict:
                status = self._do_status(work_dir, repo)
                logger.info(f"Merge conflict: returning {len(status.conflicts)} conflicts to UI")
                return PullResult(
                    success=False,
                    conflicts=status.conflicts,
                    error="Merge conflicts detected",
                )
            else:
                raise

        # Entity import is handled by desktop_sync() after push succeeds.
        pulled = 0  # Will be counted during entity import in desktop_sync

        commit_sha = repo.head.commit.hexsha if repo.head.is_valid() else None
        logger.info(f"Pull complete: {pulled} entities, commit={commit_sha[:8] if commit_sha else 'none'}")
        return PullResult(
            success=True,
            pulled=pulled,
            commit_sha=commit_sha,
        )

    def _do_push(self, work_dir: Path, repo: GitRepo) -> "PushResult":
        """Core push logic. Pushes local commits to remote."""
        from src.models.contracts.github import PushResult

        if not repo.head.is_valid():
            return PushResult(success=True, pushed_commits=0)

        # Count ahead before push
        ahead = 0
        try:
            repo.remotes.origin.fetch(self.branch)
            ahead = int(repo.git.rev_list("--count", f"origin/{self.branch}..HEAD"))
        except Exception:
            ahead = int(repo.git.rev_list("--count", "HEAD"))

        if ahead == 0:
            return PushResult(success=True, pushed_commits=0)

        # Push
        push_infos = repo.remotes.origin.push(refspec=f"HEAD:{self.branch}")

        from git.remote import PushInfo
        for pi in push_infos:
            if pi.flags & PushInfo.ERROR:
                error_msg = pi.summary.strip() if pi.summary else "Push rejected"
                logger.error(f"Push error: {error_msg}")
                return PushResult(success=False, error=error_msg)
            if pi.flags & PushInfo.REJECTED:
                error_msg = f"Push rejected (non-fast-forward): {pi.summary.strip() if pi.summary else ''}"
                logger.error(error_msg)
                return PushResult(success=False, error=error_msg)
            if pi.flags & PushInfo.REMOTE_REJECTED:
                error_msg = f"Push remote-rejected: {pi.summary.strip() if pi.summary else ''}"
                logger.error(error_msg)
                return PushResult(success=False, error=error_msg)

        commit_sha = repo.head.commit.hexsha
        logger.info(f"Pushed {ahead} commits, head={commit_sha[:8]}")
        return PushResult(
            success=True,
            commit_sha=commit_sha,
            pushed_commits=ahead,
        )

    @staticmethod
    def _plan_file_changes(
        work_dir: Path,
        repo: GitRepo,
        base_sha: str | None,
        merge_sha: str,
    ) -> list[WorkspaceFileChange]:
        """Describe committed file changes between the pre-pull and merged heads."""
        if not merge_sha or base_sha == merge_sha:
            return []

        if base_sha:
            output = repo.git.diff("--name-status", f"{base_sha}..{merge_sha}")
        else:
            output = repo.git.diff_tree("--no-commit-id", "--name-status", "-r", merge_sha)

        changes: list[WorkspaceFileChange] = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status, *paths = parts
            path = paths[-1]
            action = {
                "A": "create",
                "D": "delete",
            }.get(status[0], "update")
            source = work_dir / path
            changes.append(
                WorkspaceFileChange(
                    path=path,
                    action=action,
                    sha256=None if action == "delete" or not source.is_file() else hash_file(source)[1],
                )
            )
        return changes

    # -----------------------------------------------------------------
    # Desktop-style operations: fetch, status, commit, sync, resolve, diff
    # -----------------------------------------------------------------

    async def desktop_fetch(self, *, progress_fn=None) -> "FetchResult":
        """Git fetch origin. S3 sync down → regenerate manifest → git fetch → ahead/behind."""
        from src.models.contracts.github import FetchResult

        async def _progress(phase: str, current: int = 0, total: int = 0) -> None:
            if progress_fn:
                await progress_fn(phase, current, total)

        try:
            await _progress("Syncing from storage...")
            async with self.repo_manager.checkout() as work_dir:
                repo = self._open_or_init(work_dir)
                await _progress("Generating manifest...")
                await self._regenerate_manifest_to_dir(self.db, work_dir)
                await _progress("Fetching remote...")
                return self._do_fetch(work_dir, repo)
        except Exception as e:
            logger.error(f"Fetch failed: {e}", exc_info=True)
            return FetchResult(success=False, error=str(e))

    async def desktop_status(self) -> "WorkingTreeStatus":
        """Get working tree status under the repo lock, without S3 sync."""
        from src.models.contracts.github import WorkingTreeStatus

        if not self.repo_manager.is_initialized:
            return WorkingTreeStatus()

        try:
            async with self.repo_manager.lock() as work_dir:
                return self._do_status(work_dir, GitRepo(str(work_dir)))
        except GitStatusError:
            raise
        except Exception as e:
            logger.error(f"Status failed: {e}", exc_info=True)
            raise GitStatusError(f"status failed: {e}") from e

    @staticmethod
    async def _regenerate_manifest_to_dir(db, work_dir) -> None:
        """Generate manifest from DB and write split files to work_dir/.bifrost/."""
        from bifrost.manifest import serialize_manifest_dir, MANIFEST_FILES
        from src.services.manifest_generator import generate_manifest

        manifest = await generate_manifest(db)

        # Filter out entities whose files don't exist in work_dir.
        # The DB may contain entities from other workspaces or deleted files;
        # the manifest should only reference files actually present in the repo.
        # Forms/agents carry inline content under their UUID — they have no
        # required companion file, so they are NOT filtered by file existence.
        manifest.workflows = {
            k: v for k, v in manifest.workflows.items()
            if (work_dir / v.path).exists()
        }
        manifest.apps = {
            k: v for k, v in manifest.apps.items()
            if (work_dir / v.path).is_dir()
        }

        # Filter configs to only include those whose integration_id is present
        # in the manifest (or has no integration_id). This prevents stale configs
        # from referencing integrations that aren't part of this repo.
        integration_ids = {v.id for v in manifest.integrations.values()}
        manifest.configs = {
            k: v for k, v in manifest.configs.items()
            if v.integration_id is None or v.integration_id in integration_ids
        }

        files = serialize_manifest_dir(manifest)

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(parents=True, exist_ok=True)

        for filename, content in files.items():
            (bifrost_dir / filename).write_text(content)

        # Remove files for now-empty entity types
        for filename in MANIFEST_FILES.values():
            path = bifrost_dir / filename
            if filename not in files and path.exists():
                path.unlink()

    async def _reindex_registered_workflows(self, work_dir) -> int:
        """Re-run WorkflowIndexer on all registered workflow .py files."""
        from src.services.file_storage.indexers.workflow import WorkflowIndexer
        from src.models.orm.workflows import Workflow as WfORM
        from sqlalchemy import select

        indexer = WorkflowIndexer(self.db)
        result = await self.db.execute(
            select(WfORM.path).where(WfORM.is_active.is_(True)).distinct()
        )
        paths = [row[0] for row in result.all()]
        count = 0

        for py_path in paths:
            full_path = work_dir / py_path
            if full_path.exists():
                content = full_path.read_bytes()
                await indexer.index_python_file(py_path, content)
                count += 1

        logger.info(f"Re-indexed {count} registered workflow files")
        return count

    async def desktop_commit(self, message: str) -> "CommitResult":
        """
        Commit working tree changes (local only, no push, no S3 sync).
        Runs preflight, commits if valid.
        """
        from src.models.contracts.github import CommitResult

        try:
            async with self.repo_manager.lock() as work_dir:
                repo = self._open_or_init(work_dir)
                return await self._do_commit(work_dir, repo, message)
        except Exception as e:
            logger.error(f"Commit failed: {e}", exc_info=True)
            return CommitResult(success=False, error=str(e))

    async def prepare_desktop_sync(self, work_dir: Path, repo: GitRepo, *, progress_fn=None) -> WorkspaceSyncPlan:
        """Merge and validate a workspace sync without publishing it anywhere."""
        base_sha = repo.head.commit.hexsha if repo.head.is_valid() else None
        pull_result = await self._do_pull(work_dir, repo, progress_fn=progress_fn)
        if not pull_result.success:
            if pull_result.conflicts:
                raise WorkspaceMergeConflict(pull_result.conflicts)
            raise SyncError(pull_result.error or "Unable to merge remote changes")

        merge_sha = repo.head.commit.hexsha if repo.head.is_valid() else ""
        if progress_fn:
            await progress_fn("Validating entity import...")
        configs_touched_before = self._resolver.configs_touched.copy()
        try:
            async with self.db.begin_nested() as validation:
                _imported, entity_changes = await self._import_all_entities(
                    work_dir,
                    progress_fn=progress_fn,
                    validate_only=True,
                )
                if progress_fn:
                    await progress_fn("Checking for removed entities...")
                pending_deletes = await self._resolver._resolve_deletions(work_dir=work_dir)
                await validation.rollback()
        finally:
            self._resolver.configs_touched = configs_touched_before
            self.db.expire_all()
        pending_removals = [change for change in pending_deletes if change.action != "keep"]
        return WorkspaceSyncPlan(
            base_sha=base_sha,
            merge_sha=merge_sha,
            workspace_fingerprint=_workspace_fingerprint(work_dir),
            pending_deletes=pending_removals,
            entity_changes=entity_changes,
            file_changes=self._plan_file_changes(work_dir, repo, base_sha, merge_sha),
        )

    async def apply_desktop_sync(
        self,
        work_dir: Path,
        repo: GitRepo,
        plan: WorkspaceSyncPlan,
        *,
        confirm_deletes: bool,
        progress_fn=None,
    ) -> "SyncResult":
        """Apply a validated plan, publishing only after its DB import succeeds."""
        from src.models.contracts.github import SyncResult

        current_sha = repo.head.commit.hexsha if repo.head.is_valid() else ""
        if (
            current_sha != plan.merge_sha
            or _workspace_fingerprint(work_dir) != plan.workspace_fingerprint
        ):
            raise WorkspacePlanStale("working tree changed after validation")
        if plan.pending_deletes and not plan.db_applied and not confirm_deletes:
            logger.info(
                "Sync blocked: %d entity deletion(s) require confirmation",
                len(plan.pending_deletes),
            )
            return SyncResult(
                needs_delete_confirmation=True,
                requires_action="confirm_deletes",
                pending_deletes=plan.pending_deletes,
                entity_changes=plan.entity_changes,
            )

        if plan.db_applied:
            entities_imported = 0
            all_entity_changes = list(plan.entity_changes)
        else:
            if progress_fn:
                await progress_fn("Importing entities...")
            checkpoint_id = None
            async with self.db.begin_nested():
                entities_imported, entity_changes = await self._import_all_entities(
                    work_dir, progress_fn=progress_fn,
                )
                all_entity_changes = list(entity_changes)
                if plan.pending_deletes:
                    if progress_fn:
                        await progress_fn("Deleting removed entities...")
                    approved_deletes = _delete_keys(plan.pending_deletes)
                    current_deletes = await self._resolver._resolve_deletions(
                        work_dir=work_dir,
                        dry_run=True,
                        lock_rows=True,
                    )
                    current_keys = _delete_keys(
                        [change for change in current_deletes if change.action != "keep"]
                    )
                    if current_keys != approved_deletes:
                        raise WorkspacePlanStale(
                            "pending entity deletions changed after confirmation"
                        )
                    all_entity_changes.extend(
                        await self._resolver._resolve_deletions(
                            work_dir=work_dir,
                            approved_deletes=approved_deletes,
                            lock_rows=True,
                        )
                    )
                if progress_fn:
                    await progress_fn("Updating file index...")
                await self._update_file_index(work_dir)
                checkpoint_id = await self.repo_manager.checkpoint_workspace(work_dir)
            try:
                await self.db.commit()
            except Exception:
                if checkpoint_id:
                    await self.repo_manager.delete_workspace_checkpoint(checkpoint_id)
                raise
            plan = plan.model_copy(update={"checkpoint_id": checkpoint_id})

        publication_plan = plan.model_copy(update={
            "db_applied": True,
            "entity_changes": all_entity_changes,
        })

        if progress_fn:
            await progress_fn("Pushing to remote...")
        push_result = self._do_push(work_dir, repo)
        if not push_result.success:
            logger.warning(
                "Workspace publication failed after import; retaining local dirty state for retry: %s",
                push_result.error,
            )
            return SyncResult(
                pull_success=True,
                push_success=False,
                entities_imported=entities_imported,
                entity_changes=all_entity_changes,
                error=push_result.error,
                retryable=True,
                retry_plan=publication_plan,
            )

        try:
            if progress_fn:
                await progress_fn("Syncing to storage...")
            await self.repo_manager.sync_up(work_dir)

            from src.core.module_cache import refresh_modules_from_directory
            await refresh_modules_from_directory(work_dir)

            if progress_fn:
                await progress_fn("Syncing app previews...")
            await self._sync_app_previews(work_dir)
            if publication_plan.checkpoint_id:
                await self.repo_manager.delete_workspace_checkpoint(
                    publication_plan.checkpoint_id
                )
        except Exception as error:
            logger.warning(
                "Workspace publication failed after import; retaining local dirty state for retry: %s",
                error,
            )
            return SyncResult(
                pull_success=True,
                pushed_commits=push_result.pushed_commits,
                commit_sha=push_result.commit_sha,
                entities_imported=entities_imported,
                entity_changes=all_entity_changes,
                error=str(error),
                retryable=True,
                retry_plan=publication_plan,
            )

        logger.info(
            "Sync complete: pushed=%d, imported=%d, sha=%s",
            push_result.pushed_commits,
            entities_imported,
            push_result.commit_sha,
        )
        return SyncResult(
            success=True,
            pushed_commits=push_result.pushed_commits,
            commit_sha=push_result.commit_sha,
            entities_imported=entities_imported,
            entity_changes=all_entity_changes,
        )

    async def desktop_sync(
        self,
        confirm_deletes: bool = False,
        retry_plan: "WorkspaceSyncPlan | None" = None,
        *,
        progress_fn=None,
    ) -> "SyncResult":
        """Prepare, validate, then conditionally apply a workspace synchronization."""
        from src.models.contracts.github import SyncResult

        async def _progress(phase: str, current: int = 0, total: int = 0) -> None:
            if progress_fn:
                await progress_fn(phase, current, total)

        try:
            async with self.repo_manager.lock() as work_dir:
                if retry_plan and retry_plan.db_applied:
                    if not retry_plan.checkpoint_id:
                        raise SyncError("Publication retry is missing its workspace checkpoint")
                    await self.repo_manager.restore_workspace_checkpoint(
                        retry_plan.checkpoint_id, work_dir
                    )
                repo = self._open_or_init(work_dir)
                plan = retry_plan or await self.prepare_desktop_sync(
                    work_dir, repo, progress_fn=_progress,
                )
                return await self.apply_desktop_sync(
                    work_dir,
                    repo,
                    plan,
                    confirm_deletes=confirm_deletes,
                    progress_fn=_progress,
                )
        except WorkspaceMergeConflict as error:
            return SyncResult(
                pull_success=False,
                conflicts=error.conflicts,
                error=str(error),
            )
        except Exception as error:
            logger.error("Sync failed: %s", error, exc_info=True)
            return SyncResult(success=False, error=str(error))

    async def desktop_abort_merge(self) -> "AbortMergeResult":
        """Abort an in-progress merge. Returns to pre-pull state."""
        from src.models.contracts.github import AbortMergeResult

        try:
            async with self.repo_manager.lock() as work_dir:
                repo = self._open_or_init(work_dir)

                merge_head = work_dir / ".git" / "MERGE_HEAD"
                if not merge_head.exists():
                    return AbortMergeResult(success=False, error="No merge in progress")

                repo.git.merge("--abort")
                logger.info("Merge aborted successfully")
                return AbortMergeResult(success=True)
        except Exception as e:
            logger.error(f"Abort merge failed: {e}", exc_info=True)
            return AbortMergeResult(success=False, error=str(e))

    async def desktop_resolve(self, resolutions: dict[str, str]) -> "ResolveResult":
        """
        Resolve merge conflicts after a failed pull.
        Applies ours/theirs per file, creates a merge commit.
        NO push, NO S3 sync, NO entity import — those happen when user pushes via sync.
        """
        from src.models.contracts.github import ResolveResult

        try:
            async with self.repo_manager.lock() as work_dir:
                repo = self._open_or_init(work_dir)

                # Check for merge state or unmerged entries (stash pop conflicts)
                merge_head = work_dir / ".git" / "MERGE_HEAD"
                is_merge = merge_head.exists()
                has_unmerged = bool(repo.index.unmerged_blobs())

                if not is_merge and not has_unmerged:
                    return ResolveResult(success=False, error="No conflicts to resolve")

                # Apply resolutions
                for cpath, resolution in resolutions.items():
                    try:
                        if resolution == "ours":
                            repo.git.checkout("--ours", cpath)
                        elif resolution == "theirs":
                            repo.git.checkout("--theirs", cpath)
                        repo.git.add(cpath)
                    except Exception:
                        # DU/UD conflict — one side deleted, checkout fails
                        try:
                            repo.git.rm(cpath)
                        except Exception:
                            repo.git.add(cpath)

                # Complete the operation (merge commit is local)
                if is_merge:
                    # Use git commit (not index.commit) to properly finalize
                    # the merge — this cleans up MERGE_HEAD, MERGE_MSG, etc.
                    repo.git.commit("-m", "Merge with conflict resolution")
                else:
                    repo.index.commit("Apply stashed changes with conflict resolution")

                # Compute ahead/behind so the UI can update immediately
                ahead = 0
                behind = 0
                try:
                    ahead = int(repo.git.rev_list("--count", f"origin/{self.branch}..HEAD"))
                except Exception as e:
                    # No origin/<branch> ref locally — leave ahead=0
                    logger.debug(f"could not compute commits ahead of origin/{self.branch}: {e}")
                try:
                    behind = int(repo.git.rev_list("--count", f"HEAD..origin/{self.branch}"))
                except Exception as e:
                    # No origin/<branch> ref locally — leave behind=0
                    logger.debug(f"could not compute commits behind origin/{self.branch}: {e}")

                logger.info("Resolved conflicts, created merge commit (local)")
                return ResolveResult(success=True, commits_ahead=ahead, commits_behind=behind)
        except Exception as e:
            logger.error(f"Resolve failed: {e}", exc_info=True)
            return ResolveResult(success=False, error=str(e))

    async def desktop_diff(self, path: str) -> "DiffResult":
        """Get file diff: HEAD content vs working tree content. No S3 sync."""
        from src.models.contracts.github import DiffResult

        try:
            async with self.repo_manager.lock() as work_dir:
                repo = self._open_or_init(work_dir)

                # Get HEAD content
                head_content = None
                if repo.head.is_valid():
                    try:
                        head_content = repo.git.show(f"HEAD:{path}")
                    except Exception:
                        pass  # File doesn't exist in HEAD (new file)

                # Get working tree content
                working_content = None
                working_path = work_dir / path
                if working_path.exists():
                    working_content = working_path.read_text(errors="replace")

                return DiffResult(
                    path=path,
                    head_content=head_content,
                    working_content=working_content,
                )
        except Exception as e:
            logger.error(f"Diff failed: {e}", exc_info=True)
            return DiffResult(path=path)

    async def desktop_discard(self, paths: list[str]) -> "DiscardResult":
        """Discard working tree changes for specific files. S3 sync up so API sees revert."""
        from src.models.contracts.github import DiscardResult

        try:
            async with self.repo_manager.lock() as work_dir:
                repo = self._open_or_init(work_dir)
                discarded = []

                for path in paths:
                    file_path = work_dir / path
                    try:
                        if repo.head.is_valid():
                            try:
                                # File exists in HEAD — restore it
                                repo.git.checkout("HEAD", "--", path)
                                discarded.append(path)
                                continue
                            except Exception as e:
                                # Path isn't tracked in HEAD — fall through to delete branch
                                logger.debug(f"git checkout HEAD failed for {path}, falling back to unlink: {e}")
                        # Untracked or not in HEAD — just delete
                        if file_path.exists():
                            file_path.unlink()
                            discarded.append(path)
                    except Exception as e:
                        logger.warning(f"Failed to discard {path}: {e}")

                # S3 sync up so other containers see the reverted files
                await self.repo_manager.sync_up(work_dir)

                # Refresh Redis module cache so editor + workers see reverted .py content
                from src.core.module_cache import refresh_modules_from_directory
                await refresh_modules_from_directory(work_dir)

                logger.info(f"Discarded {len(discarded)} file(s)")
                return DiscardResult(success=True, discarded=discarded)
        except Exception as e:
            logger.error(f"Discard failed: {e}", exc_info=True)
            return DiscardResult(success=False, error=str(e))

    # -----------------------------------------------------------------
    # Helpers for desktop-style operations
    # -----------------------------------------------------------------

    def _open_or_init(self, work_dir: Path) -> GitRepo:
        """Open existing .git/ or clone fresh. Ensure remote URL and user identity are set."""
        if (work_dir / ".git").exists():
            repo = GitRepo(str(work_dir))
            if "origin" in [r.name for r in repo.remotes]:
                repo.remotes.origin.set_url(self.repo_url)
            else:
                repo.create_remote("origin", self.repo_url)
        else:
            repo = self._clone_or_init(work_dir)

        # Ensure git user identity is configured (needed for merge/commit)
        with repo.config_writer() as cw:
            try:
                cw.get_value("user", "name")
            except Exception:
                cw.set_value("user", "name", "Bifrost")
            try:
                cw.get_value("user", "email")
            except Exception:
                cw.set_value("user", "email", "bifrost@localhost")

        return repo

    async def _execute_ops(self, ops: "list[SyncOp]") -> int:
        """Execute a list of SyncOps against the DB in order.

        Returns the number of ops executed.
        """
        from src.services.sync_ops import SyncOp  # noqa: F401
        for op in ops:
            await op.execute(self.db)
        return len(ops)

    @staticmethod
    def _ops_to_issues(ops: "list[SyncOp]") -> list[str]:
        """Convert a list of SyncOps to human-readable validation issues.

        Currently returns an empty list — resolution methods detect missing
        refs by logging warnings and skipping. Future work can add issue
        markers to ops for richer dry-run output.
        """
        return []


    async def _import_all_entities(
        self,
        work_dir: Path,
        progress_fn=None,
        validate_only: bool = False,
    ) -> "tuple[int, list]":
        """Import entities from the working tree into the DB (incremental).

        Computes a diff against current DB state and only resolves changed
        entities, matching the incremental approach used by CLI push.

        Returns tuple of (count of entities resolved, list of entity changes).
        """
        from src.models.contracts.github import EntityChange
        from src.services.manifest_generator import generate_manifest
        from src.services.manifest_import import _diff_and_collect

        bifrost_dir = work_dir / ".bifrost"
        manifest = read_manifest_from_dir(bifrost_dir)

        has_entities = (
            manifest.organizations or manifest.roles
            or manifest.workflows or manifest.forms or manifest.agents or manifest.apps
            or manifest.integrations or manifest.configs or manifest.tables
            or manifest.events or manifest.policy_rules
        )
        if not has_entities:
            return 0, []

        # Diff against current DB state to find what actually changed
        db_manifest = await generate_manifest(self.db)
        diff_changes, changed_ids = _diff_and_collect(manifest, db_manifest)

        if not changed_ids:
            # No entity-level changes detected — nothing to import
            return 0, []

        # Resolve only changed entities
        await self._resolver.plan_import(
            manifest,
            work_dir,
            progress_fn=progress_fn,
            dry_run=False,
            changed_ids=changed_ids,
            sync_app_previews=False,
        )

        # Build entity change list from the diff
        # Diff uses add/change/delete; EntityChange uses added/updated/removed
        _action_map: dict[str, "Literal['added', 'updated', 'removed']"] = {
            "add": "added", "change": "updated", "delete": "removed",
        }
        entity_changes: list[EntityChange] = []
        for c in diff_changes:
            mapped = _action_map.get(c["action"])
            if mapped and mapped != "removed":  # deletions handled separately
                entity_changes.append(EntityChange(
                    action=mapped,
                    entity_type=c["entity_type"],
                    name=c["name"],
                ))

        count = len(changed_ids)

        if validate_only:
            return count, entity_changes

        # Indexer side-effects: WorkflowIndexer for changed workflows
        from src.models.orm.workflows import Workflow as WfORM
        from src.services.file_storage.indexers.workflow import WorkflowIndexer

        wf_paths = {
            mwf.path for mwf in manifest.workflows.values()
            if (work_dir / mwf.path).exists() and mwf.id in changed_ids
        }
        if wf_paths:
            # _repo/-scoped (solution_id IS NULL): the workspace indexer manages
            # _repo/ workflows only, and a solution-managed workflow sharing a path
            # must not enter this prefetch cache (Codex #14).
            wf_result = await self.db.execute(
                select(WfORM).where(
                    WfORM.path.in_(wf_paths), WfORM.solution_id.is_(None)
                )
            )
            wf_cache: dict[tuple[str, str], WfORM] = {}
            for wf in wf_result.scalars().all():
                if wf.path and wf.function_name:
                    wf_cache[(wf.path, wf.function_name)] = wf
        else:
            wf_cache = {}

        workflow_indexer = WorkflowIndexer(self.db)
        workflow_indexer.set_prefetch_cache(wf_cache)
        for _wf_name, mwf in manifest.workflows.items():
            if mwf.id not in changed_ids:
                continue
            wf_path = work_dir / mwf.path
            if wf_path.exists():
                content = wf_path.read_bytes()
                await workflow_indexer.index_python_file(mwf.path, content)

        # Index forms from manifest
        async def _read_work_dir(path: str) -> bytes | None:
            p = work_dir / path
            return p.read_bytes() if p.exists() else None

        await self._resolver._index_forms_from_manifest(manifest, _read_work_dir, changed_ids)

        # Index agents from manifest
        await self._resolver._index_agents_from_manifest(manifest, _read_work_dir, changed_ids)

        return count, entity_changes

    # -----------------------------------------------------------------
    # App preview sync
    # -----------------------------------------------------------------

    async def _sync_app_previews(self, work_dir: Path) -> None:
        """Sync app preview files from repo working tree to _apps/{id}/preview/ in S3.

        Reads the manifest to find app entries, derives the source directory
        from each app's path, and copies files to the preview store.
        """
        from src.services.app_storage import AppStorageService

        bifrost_dir = work_dir / ".bifrost"
        manifest = read_manifest_from_dir(bifrost_dir)

        if not manifest.apps:
            return

        import asyncio

        app_storage = AppStorageService(self.repo_manager._settings)

        async def _sync_one_app(mapp_id: str, source_dir: str) -> None:
            try:
                synced, compile_errors = await app_storage.sync_preview_compiled(
                    mapp_id, source_dir
                )
                logger.info(f"Synced {synced} compiled preview files for app {mapp_id}")
                if compile_errors:
                    logger.warning(
                        f"Compile errors for app {mapp_id}: {compile_errors}"
                    )
            except Exception as e:
                logger.warning(f"Failed to sync preview for app {mapp_id}: {e}")

        # Process all apps concurrently
        await asyncio.gather(*(
            _sync_one_app(mapp.id, mapp.path)
            for mapp in manifest.apps.values()
        ))

    # -----------------------------------------------------------------
    # Reimport from repo (no git operations)
    # -----------------------------------------------------------------

    async def reimport_from_repo(self) -> int:
        """Re-import all entities from S3 _repo/ without git operations.

        Downloads the working tree from S3, imports entities into DB,
        updates file_index, and syncs app previews.

        Returns count of entities imported.
        """
        async with self.repo_manager.checkout() as work_dir:
            # Regenerate manifest from current DB state
            await self._regenerate_manifest_to_dir(self.db, work_dir)

            # Import entities atomically with savepoint
            async with self.db.begin_nested():
                count, _changes = await self._import_all_entities(work_dir)
                await self._resolver._resolve_deletions(work_dir=work_dir)
                await self._update_file_index(work_dir)
            await self.db.commit()

            # Re-run indexers on all registered workflow files
            await self._reindex_registered_workflows(work_dir)
            await self.db.commit()

            # Sync app preview files
            await self._sync_app_previews(work_dir)

            logger.info(f"Reimport complete: {count} entities")
            return count

    # -----------------------------------------------------------------
    # Internal: git operations
    # -----------------------------------------------------------------

    def _clone_or_init(self, target: Path) -> GitRepo:
        """Clone from repo_url, or init if repo is empty.

        Handles the case where target already has files (e.g. entity files
        written by RepoSyncWriter before the first clone). Clones into a
        temp dir and merges .git/ + remote files into target.
        """
        import shutil
        import tempfile

        try:
            # Clone into a temp dir first (git clone requires clean dir)
            clone_dir = Path(tempfile.mkdtemp(prefix="bifrost-clone-"))
            try:
                GitRepo.clone_from(
                    self.repo_url,
                    str(clone_dir),
                    branch=self.branch,
                )
                # Move .git/ to the target
                shutil.move(str(clone_dir / ".git"), str(target / ".git"))
                # Copy any tracked files from clone that aren't in target
                for item in clone_dir.iterdir():
                    if item.name == ".git":
                        continue
                    dest = target / item.name
                    if not dest.exists():
                        if item.is_dir():
                            shutil.copytree(str(item), str(dest))
                        else:
                            shutil.copy2(str(item), str(dest))
                    else:
                        logger.info(f"Skipping remote file {item.name} — already exists in working tree")
                # Open the repo at target
                return GitRepo(str(target))
            finally:
                shutil.rmtree(clone_dir, ignore_errors=True)
        except Exception as e:
            err_str = str(e)
            # Empty repo or branch doesn't exist yet
            if "not found" in err_str.lower() or "empty" in err_str.lower() or "could not find remote branch" in err_str.lower():
                repo = GitRepo.init(str(target))
                repo.create_remote("origin", self.repo_url)
                return repo
            raise SyncError(f"Failed to clone {self.repo_url}: {e}") from e

    async def _update_file_index(self, work_dir: Path) -> None:
        """Update file_index from all files in the working tree, remove stale entries.

        Optimized: prefetches existing (path, content_hash) pairs in one query,
        skips files whose hash hasn't changed, and batch-upserts the rest in
        chunks of 100.
        """
        from sqlalchemy import delete, text
        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.file_index import FileIndex
        from src.services.file_index_service import MAX_INDEXABLE_TEXT_BYTES, _is_text_file

        files = iter_tree_metadata(work_dir)
        repo_paths: set[str] = set()

        # Prefetch all existing (path, content_hash) in one query
        existing_result = await self.db.execute(
            select(FileIndex.path, FileIndex.content_hash)
        )
        existing_hashes = {row[0]: row[1] for row in existing_result.all()}

        # Flush changed text rows as they are discovered.  A row's content must
        # be materialized for the database write, but retaining every changed
        # text file until traversal completes can otherwise exhaust a worker.
        pending_upserts: list[dict] = []
        pending_bytes = 0
        upserted_count = 0

        async def flush_pending_upserts() -> None:
            nonlocal pending_bytes, upserted_count
            if not pending_upserts:
                return
            stmt = insert(FileIndex).values(pending_upserts).on_conflict_do_update(
                index_elements=[FileIndex.path],
                set_={
                    "content": insert(FileIndex).excluded.content,
                    "content_hash": insert(FileIndex).excluded.content_hash,
                    "updated_at": text("NOW()"),
                },
            )
            await self.db.execute(stmt)
            upserted_count += len(pending_upserts)
            pending_upserts.clear()
            pending_bytes = 0

        for entry in files:
            rel_path = entry.path
            repo_paths.add(rel_path)
            if not _is_text_file(rel_path):
                continue
            if entry.size > MAX_INDEXABLE_TEXT_BYTES:
                # Search indexing is a bounded projection. Delete a prior
                # smaller-file entry so searches cannot return stale content,
                # without loading the oversized repository file into memory.
                await self.db.execute(delete(FileIndex).where(FileIndex.path == rel_path))
                continue
            try:
                content_str = (work_dir / rel_path).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            content_hash = entry.sha256

            # Skip if hash hasn't changed
            if existing_hashes.get(rel_path) == content_hash:
                continue

            if pending_upserts and (
                len(pending_upserts) >= FILE_INDEX_UPSERT_MAX_ROWS
                or pending_bytes + entry.size > FILE_INDEX_UPSERT_MAX_BYTES
            ):
                await flush_pending_upserts()
            pending_upserts.append({
                "path": rel_path,
                "content": content_str,
                "content_hash": content_hash,
            })
            pending_bytes += entry.size
            if (
                len(pending_upserts) >= FILE_INDEX_UPSERT_MAX_ROWS
                or pending_bytes >= FILE_INDEX_UPSERT_MAX_BYTES
            ):
                await flush_pending_upserts()

        await flush_pending_upserts()

        if upserted_count:
            logger.info("File index: upserted %d changed files", upserted_count)

        # Remove file_index entries that no longer exist in the repo
        stale_paths = set(existing_hashes.keys()) - repo_paths
        if stale_paths:
            await self.db.execute(
                delete(FileIndex).where(FileIndex.path.in_(stale_paths))
            )

    # -----------------------------------------------------------------
    # Internal: preflight validation
    # -----------------------------------------------------------------

    async def _run_preflight(self, repo_dir: Path) -> PreflightResult:
        """Run all preflight checks against a repo directory."""
        issues: list[PreflightIssue] = []

        # 1. Check manifest validity
        bifrost_dir = repo_dir / ".bifrost"
        manifest: Manifest | None = None
        if bifrost_dir.exists():
            try:
                manifest = read_manifest_from_dir(bifrost_dir)
                # Verify all paths exist
                from bifrost.manifest import get_all_paths
                for path in get_all_paths(manifest):
                    if not (repo_dir / path).exists():
                        issues.append(PreflightIssue(
                            path=".bifrost/",
                            message=f"Manifest references missing file: {path}",
                            severity="error",
                            category="manifest",
                            fix_hint="This file was deleted but the entity is still registered. Use 'Clean up & Retry' to remove orphaned references.",
                            auto_fixable=True,
                        ))
            except Exception as e:
                issues.append(PreflightIssue(
                    path=".bifrost/",
                    message=f"Invalid manifest: {e}",
                    severity="error",
                    category="manifest",
                    fix_hint="The manifest is malformed. Run 'Reimport' from Settings > Maintenance.",
                ))

        # 2. Syntax check all .py files
        for py_file in repo_dir.rglob("*.py"):
            rel = str(py_file.relative_to(repo_dir))
            if rel.startswith(".git/"):
                continue
            try:
                source = py_file.read_text()
                compile(source, rel, "exec")
            except SyntaxError as e:
                issues.append(PreflightIssue(
                    path=rel,
                    line=e.lineno,
                    message=f"Syntax error: {e.msg}",
                    severity="error",
                    category="syntax",
                    fix_hint=f"Fix the syntax error in {rel} at line {e.lineno}.",
                ))

        # 3. Ruff lint check
        py_files = [
            str(f) for f in repo_dir.rglob("*.py")
            if not str(f.relative_to(repo_dir)).startswith(".git/")
        ]
        if py_files:
            try:
                result = subprocess.run(
                    ["ruff", "check", "--output-format=json", "--no-fix", *py_files],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=str(repo_dir),
                )
                if result.stdout.strip():
                    import json
                    for violation in json.loads(result.stdout):
                        rel_path = str(Path(violation["filename"]).relative_to(repo_dir))
                        issues.append(PreflightIssue(
                            path=rel_path,
                            line=violation.get("location", {}).get("row"),
                            message=f"{violation.get('code', '?')}: {violation.get('message', '')}",
                            severity="warning",
                            category="lint",
                            fix_hint="This is a style warning and won't block your commit.",
                        ))
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass  # ruff not available or timed out — skip lint

        # 4. Ref resolution (UUID references in forms)
        # Forms now carry workflow_id / launch_workflow_id inline in the manifest.
        # Back-compat: if a form has no inline workflow_id but has a path,
        # parse the companion file (legacy split layout).
        def _form_workflow_refs(mform) -> tuple[str | None, str | None]:
            """Return (workflow_id, launch_workflow_id) for a manifest form.

            Prefers inline fields; falls back to companion file for back-compat.
            """
            if mform.workflow_id is not None or mform.launch_workflow_id is not None:
                return mform.workflow_id, mform.launch_workflow_id
            if mform.path:
                form_path = repo_dir / mform.path
                if form_path.exists():
                    try:
                        data = yaml.safe_load(form_path.read_text()) or {}
                        return (
                            data.get("workflow_id") or data.get("workflow"),
                            data.get("launch_workflow_id") or data.get("launch_workflow"),
                        )
                    except Exception:
                        pass
            return None, None

        if manifest:
            entity_ids = get_all_entity_ids(manifest)
            for form_name, mform in manifest.forms.items():
                wf_ref, launch_ref = _form_workflow_refs(mform)
                ref_path = mform.path or f"forms/{mform.id}"
                if wf_ref and wf_ref not in entity_ids:
                    issues.append(PreflightIssue(
                        path=ref_path,
                        message=f"Form references unknown workflow UUID: {wf_ref}",
                        severity="error",
                        category="ref",
                        fix_hint="Edit this form and assign a valid workflow.",
                    ))
                if launch_ref and launch_ref not in entity_ids:
                    issues.append(PreflightIssue(
                        path=ref_path,
                        message=f"Form references unknown launch workflow UUID: {launch_ref}",
                        severity="error",
                        category="ref",
                        fix_hint="Edit this form and assign a valid launch workflow.",
                    ))

            # 5. Orphan detection — workflows referenced by forms but missing from manifest
            wf_ids = {mwf.id for mwf in manifest.workflows.values()}
            for form_name, mform in manifest.forms.items():
                wf_ref, _ = _form_workflow_refs(mform)
                ref_path = mform.path or f"forms/{mform.id}"
                if wf_ref and wf_ref not in wf_ids:
                    issues.append(PreflightIssue(
                        path=ref_path,
                        message=f"Form '{form_name}' references workflow {wf_ref} which is not in the manifest (will be orphaned)",
                        severity="warning",
                        category="orphan",
                        fix_hint="Register the referenced workflow, or update the form to reference an active one.",
                    ))

            # 6. Cross-reference validation for new entity types
            from bifrost.manifest import validate_manifest
            ref_errors = validate_manifest(manifest)
            for err in ref_errors:
                issues.append(PreflightIssue(
                    path=".bifrost/",
                    message=err,
                    severity="error",
                    category="ref",
                    fix_hint="Check that all referenced entity IDs exist and are active.",
                ))

            # 7. Health warnings (non-blocking)
            # Secret configs with null values
            for cfg_key, mcfg in manifest.configs.items():
                if mcfg.config_type == "secret" and mcfg.value is None:
                    issues.append(PreflightIssue(
                        path=".bifrost/configs.yaml",
                        message=f"Config '{cfg_key}' (type=secret) needs a value after import",
                        severity="warning",
                        category="health",
                        fix_hint="Set a value for this config in Settings > Integrations.",
                    ))

            # OAuth providers needing setup
            for integ_name, minteg in manifest.integrations.items():
                if minteg.oauth_provider and minteg.oauth_provider.client_id == "__NEEDS_SETUP__":
                    issues.append(PreflightIssue(
                        path=".bifrost/integrations.yaml",
                        message=f"Integration '{integ_name}' OAuth provider needs client_id and client_secret setup",
                        severity="warning",
                        category="health",
                        fix_hint="Configure OAuth client_id and client_secret in Settings > Integrations.",
                    ))

            # Webhook sources needing external registration
            for es_name, mes in manifest.events.items():
                if mes.source_type == "webhook":
                    issues.append(PreflightIssue(
                        path=".bifrost/events.yaml",
                        message=f"Webhook source '{es_name}' will need external registration after import",
                        severity="warning",
                        category="health",
                        fix_hint="Register this webhook URL with the external service after import.",
                    ))

        has_errors = any(i.severity == "error" for i in issues)
        return PreflightResult(valid=not has_errors, issues=issues)
