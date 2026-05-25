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
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from git import Repo as GitRepo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.models.contracts.github import (
    PreflightIssue,
    PreflightResult,
)
from src.services.git_repo_manager import GitRepoManager
from src.services.github_sync_entity_metadata import extract_entity_metadata

if TYPE_CHECKING:
    from typing import Literal  # used only in string annotations below
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
    _collect_removed_entity_ids,
    _filter_manifest_to_scope,
    _safe_app_repo_path,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Errors
# =============================================================================


class SyncError(Exception):
    """Error during sync operation."""
    pass


# =============================================================================
# Helpers
# =============================================================================


def _content_hash(content: bytes) -> str:
    """SHA-256 of bytes."""
    return hashlib.sha256(content).hexdigest()


def _walk_tree(root: Path) -> dict[str, bytes]:
    """Walk a directory tree and return {relative_path: content} for all files."""
    files: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        rel = str(p.relative_to(root))
        # Skip .git internals
        if rel.startswith(".git/") or rel == ".git":
            continue
        files[rel] = p.read_bytes()
    return files


def _filter_manifest_to_work_dir(manifest: Manifest, work_dir: Path) -> None:
    """Restrict regenerated manifest entries to entities owned by this repo."""
    _filter_manifest_to_scope(
        manifest,
        path_exists=lambda path: (work_dir / path).exists(),
        dir_exists=lambda path: (work_dir / path).is_dir(),
    )


def _filter_db_manifest_to_import_scope(
    db_manifest: Manifest,
    incoming_manifest: Manifest,
    work_dir: Path,
) -> None:
    """Restrict DB-side diff candidates to this repo/import scope."""
    _filter_manifest_to_scope(
        db_manifest,
        path_exists=lambda path: (work_dir / path).exists(),
        dir_exists=lambda path: (work_dir / path).is_dir(),
        scope_manifest=incoming_manifest,
    )


def _deleted_paths_in_head(repo: GitRepo) -> set[str]:
    """Return paths deleted by the current HEAD commit."""
    try:
        output = repo.git.diff_tree("--no-commit-id", "--name-status", "-r", "HEAD")
    except Exception as exc:
        logger.debug("Could not inspect deleted paths in HEAD: %s", exc)
        return set()
    deleted: set[str] = set()
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0] == "D":
            deleted.add(parts[1])
    return deleted


def _three_way_merge_dicts(
    base: dict, ours: dict, theirs: dict
) -> dict:
    """3-way merge two dicts against a common base.

    - Keys deleted by either side (present in base but absent in ours/theirs)
      stay deleted unless the other side modified the value.
    - Keys added by either side are included.
    - When both sides modify the same key, theirs wins.
    """
    merged = {}
    # Preserve key ordering: ours first, then theirs additions, then base-only
    seen: set = set()
    ordered_keys: list = []
    for key in ours:
        if key not in seen:
            ordered_keys.append(key)
            seen.add(key)
    for key in theirs:
        if key not in seen:
            ordered_keys.append(key)
            seen.add(key)
    for key in base:
        if key not in seen:
            ordered_keys.append(key)
            seen.add(key)

    for key in ordered_keys:
        in_base = key in base
        in_ours = key in ours
        in_theirs = key in theirs

        if in_ours and in_theirs:
            # Both have it — if both are dicts, recurse; otherwise theirs wins
            if isinstance(ours[key], dict) and isinstance(theirs[key], dict):
                base_val = base.get(key, {}) if isinstance(base.get(key), dict) else {}
                merged[key] = _three_way_merge_dicts(base_val, ours[key], theirs[key])
            else:
                merged[key] = theirs[key]
        elif in_ours and not in_theirs:
            if in_base:
                # Theirs deleted it — honor the deletion unless ours modified it
                if base.get(key) != ours[key]:
                    merged[key] = ours[key]  # Ours modified, keep it
                # else: theirs deleted, ours unchanged → delete
            else:
                merged[key] = ours[key]  # Added by ours
        elif in_theirs and not in_ours:
            if in_base:
                # Ours deleted it — honor the deletion unless theirs modified it
                if base.get(key) != theirs[key]:
                    merged[key] = theirs[key]  # Theirs modified, keep it
                # else: ours deleted, theirs unchanged → delete
            else:
                merged[key] = theirs[key]  # Added by theirs
        # else: neither has it (shouldn't happen since key came from one of them)

    return merged


def _auto_resolve_manifest_conflicts(repo: GitRepo, work_dir: Path, unmerged: dict) -> set[str]:
    """Auto-resolve .bifrost/*.yaml manifest conflicts via 3-way YAML merge.

    For each conflicted manifest file:
    1. Parse base (stage 1), ours (stage 2), and theirs (stage 3) YAML
    2. 3-way merge respecting additions and deletions from both sides
    3. Write merged YAML to working tree and git add
    4. On failure, accept theirs entirely

    Returns set of paths that were auto-resolved (removed from conflict list).
    """
    resolved_paths: set[str] = set()

    for cpath in list(unmerged.keys()):
        cpath_str = str(cpath)
        if not (cpath_str.startswith(".bifrost/") and cpath_str.endswith(".yaml")):
            continue

        try:
            # Parse all three sides (base, ours, theirs)
            base_yaml: dict = {}
            ours_yaml: dict = {}
            theirs_yaml: dict = {}
            try:
                base_raw = repo.git.show(f":1:{cpath_str}")
                base_yaml = yaml.safe_load(base_raw) or {}
            except Exception:
                base_yaml = {}
            try:
                ours_raw = repo.git.show(f":2:{cpath_str}")
                ours_yaml = yaml.safe_load(ours_raw) or {}
            except Exception:
                ours_yaml = {}
            try:
                theirs_raw = repo.git.show(f":3:{cpath_str}")
                theirs_yaml = yaml.safe_load(theirs_raw) or {}
            except Exception:
                theirs_yaml = {}

            if not isinstance(ours_yaml, dict) or not isinstance(theirs_yaml, dict):
                # Not a dict-shaped YAML — fall back to accepting theirs
                raise ValueError("Non-dict YAML")
            if not isinstance(base_yaml, dict):
                base_yaml = {}

            # 3-way merge respecting deletions
            merged = _three_way_merge_dicts(base_yaml, ours_yaml, theirs_yaml)

            # Write merged YAML
            merged_yaml = yaml.dump(merged, default_flow_style=False, sort_keys=True, allow_unicode=True)
            file_path = work_dir / cpath_str
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(merged_yaml)
            repo.git.add(cpath_str)
            resolved_paths.add(cpath_str)
            logger.info(f"Auto-resolved manifest conflict: {cpath_str}")

        except Exception as e:
            # Fall back to accepting theirs entirely
            logger.warning(f"Manifest auto-merge failed for {cpath_str}, accepting theirs: {e}")
            try:
                theirs_raw = repo.git.show(f":3:{cpath_str}")
                file_path = work_dir / cpath_str
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(theirs_raw)
                repo.git.add(cpath_str)
                resolved_paths.add(cpath_str)
            except Exception as fallback_err:
                logger.error(f"Failed to accept theirs for {cpath_str}: {fallback_err}")

    return resolved_paths


def _classify_conflict_type(unmerged: dict, cpath: str) -> str:
    """Classify conflict type from git unmerged blob stages.

    Stage 1 = common ancestor, Stage 2 = ours, Stage 3 = theirs.
    """
    # unmerged keys may be PathLike — find matching entry by str comparison
    entries = None
    for key, val in unmerged.items():
        if str(key) == cpath:
            entries = val
            break
    if not entries:
        return "both_modified"
    stages = {stage for stage, _blob in entries}
    if stages >= {1, 2, 3}:
        return "both_modified"
    elif stages == {2, 3}:
        return "both_added"
    elif stages == {1, 3}:
        return "deleted_by_us"
    elif stages == {1, 2}:
        return "deleted_by_them"
    else:
        return "both_modified"


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

        # Check for unresolved conflicts BEFORE git add (which would resolve them)
        conflict_list: list[MergeConflict] = []
        try:
            unmerged = repo.index.unmerged_blobs()
        except Exception:
            unmerged = {}

        if unmerged:
            # Auto-resolve .bifrost/*.yaml manifest conflicts
            try:
                _auto_resolve_manifest_conflicts(repo, work_dir, unmerged)
                unmerged = repo.index.unmerged_blobs()
            except Exception as e:
                logger.warning(f"Manifest auto-resolve in status failed: {e}")

            for cpath in sorted(str(k) for k in unmerged.keys()):
                ours_content = None
                theirs_content = None
                try:
                    ours_content = repo.git.show(f":2:{cpath}")
                except Exception as e:
                    # Stage 2 (ours) may not exist in some conflict types (e.g. delete/modify)
                    logger.debug(f"could not read stage 2 for {cpath}: {e}")
                try:
                    theirs_content = repo.git.show(f":3:{cpath}")
                except Exception as e:
                    # Stage 3 (theirs) may not exist (e.g. modify/delete)
                    logger.debug(f"could not read stage 3 for {cpath}: {e}")
                metadata = extract_entity_metadata(cpath)
                conflict_list.append(MergeConflict(
                    path=cpath,
                    ours_content=ours_content,
                    theirs_content=theirs_content,
                    display_name=metadata.display_name,
                    entity_type=metadata.entity_type,
                    conflict_type=_classify_conflict_type(unmerged, cpath),
                ))

        # Detect merge state and ahead/behind
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

        if conflict_list:
            return WorkingTreeStatus(
                changed_files=[],
                total_changes=0,
                conflicts=conflict_list,
                commits_ahead=ahead,
                commits_behind=behind,
                merging=merging,
            )

        # Stage everything to get accurate diff
        repo.git.add(A=True)

        changed: list[ChangedFile] = []

        if repo.head.is_valid():
            porcelain = repo.git.status("--porcelain")
            for line in porcelain.strip().split("\n"):
                if not line.strip():
                    continue
                status_code = line[:2].strip()
                path = line[3:].strip()
                if path.startswith('"') and path.endswith('"'):
                    path = path[1:-1]

                if status_code in ("A", "??"):
                    change_type = "added"
                elif status_code == "D":
                    change_type = "deleted"
                elif status_code == "R":
                    change_type = "renamed"
                    if " -> " in path:
                        path = path.split(" -> ", 1)[1]
                else:
                    change_type = "modified"

                metadata = extract_entity_metadata(path)
                changed.append(ChangedFile(
                    path=path,
                    change_type=change_type,
                    display_name=metadata.display_name,
                    entity_type=metadata.entity_type,
                ))
        else:
            for path in repo.untracked_files:
                metadata = extract_entity_metadata(path)
                changed.append(ChangedFile(
                    path=path,
                    change_type="added",
                    display_name=metadata.display_name,
                    entity_type=metadata.entity_type,
                ))

        # Unstage (reset) so we don't pollute the working tree
        if repo.head.is_valid():
            repo.git.reset("HEAD")

        return WorkingTreeStatus(
            changed_files=changed,
            total_changes=len(changed),
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

    async def _do_pull(self, work_dir: Path, repo: GitRepo, job_id: str | None = None) -> "PullResult":
        """Core pull logic. Fetches, merges, imports entities."""
        from src.models.contracts.github import MergeConflict, PullResult

        async def _progress(phase: str, current: int = 0, total: int = 0) -> None:
            if job_id:
                from src.core.pubsub import publish_git_progress
                await publish_git_progress(job_id, phase, current, total)

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
                conflicts: list[MergeConflict] = []
                try:
                    unmerged = repo.index.unmerged_blobs()
                except Exception:
                    unmerged = {}

                if unmerged:
                    try:
                        _auto_resolve_manifest_conflicts(repo, work_dir, unmerged)
                        unmerged = repo.index.unmerged_blobs()
                    except Exception as e:
                        logger.warning(f"Manifest auto-resolve in pull failed: {e}")

                conflicted_files = sorted(str(k) for k in unmerged.keys())

                if not conflicted_files:
                    # All conflicts were auto-resolved, commit the merge
                    logger.info("All merge conflicts auto-resolved, completing merge")
                    repo.index.commit(
                        "Merge remote-tracking branch (auto-resolved)",
                        parent_commits=[
                            repo.head.commit,
                            repo.commit("MERGE_HEAD"),
                        ],
                    )
                    # Remove MERGE_HEAD to complete merge state
                    merge_head = work_dir / ".git" / "MERGE_HEAD"
                    if merge_head.exists():
                        merge_head.unlink()
                else:
                    for cpath in conflicted_files:
                        ours_content = None
                        theirs_content = None
                        try:
                            ours_content = repo.git.show(f":2:{cpath}")
                        except Exception as e:
                            # Stage 2 may not exist in some conflict types
                            logger.debug(f"could not read stage 2 for {cpath}: {e}")
                        try:
                            theirs_content = repo.git.show(f":3:{cpath}")
                        except Exception as e:
                            # Stage 3 may not exist in some conflict types
                            logger.debug(f"could not read stage 3 for {cpath}: {e}")

                        metadata = extract_entity_metadata(cpath)
                        conflicts.append(MergeConflict(
                            path=cpath,
                            ours_content=ours_content,
                            theirs_content=theirs_content,
                            display_name=metadata.display_name,
                            entity_type=metadata.entity_type,
                            conflict_type=_classify_conflict_type(unmerged, cpath),
                        ))

                    logger.info(f"Merge conflict: returning {len(conflicts)} conflicts to UI")
                    return PullResult(
                        success=False,
                        conflicts=conflicts,
                        error="Merge conflicts detected",
                    )
            else:
                raise

        # Entity import is handled by desktop_sync() after push succeeds.
        pulled = 0  # Will be counted during entity import in desktop_sync

        # Sync app preview files from repo to _apps/{id}/preview/
        await self._sync_app_previews(work_dir)

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

    # -----------------------------------------------------------------
    # Desktop-style operations: fetch, status, commit, sync, resolve, diff
    # -----------------------------------------------------------------

    async def desktop_fetch(self, job_id: str | None = None) -> "FetchResult":
        """Git fetch origin. S3 sync down → regenerate manifest → git fetch → ahead/behind."""
        from src.models.contracts.github import FetchResult

        async def _progress(phase: str, current: int = 0, total: int = 0) -> None:
            if job_id:
                from src.core.pubsub import publish_git_progress
                await publish_git_progress(job_id, phase, current, total)

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
        """Get working tree status. No lock, no S3. Returns empty if not initialized."""
        from src.models.contracts.github import WorkingTreeStatus

        try:
            if not self.repo_manager.is_initialized:
                return WorkingTreeStatus()
            async with self.repo_manager.lock() as work_dir:
                repo = self._open_or_init(work_dir)
                return self._do_status(work_dir, repo)
        except Exception as e:
            logger.error(f"Status failed: {e}", exc_info=True)
            return WorkingTreeStatus()

    @staticmethod
    async def _regenerate_manifest_to_dir(db, work_dir) -> None:
        """Generate manifest from DB and write split files to work_dir/.bifrost/."""
        from bifrost.manifest import serialize_manifest_dir, MANIFEST_FILES
        from src.services.manifest_generator import generate_manifest

        manifest = await generate_manifest(db)

        _filter_manifest_to_work_dir(manifest, work_dir)

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

    async def desktop_sync(self, job_id: str | None = None, confirm_deletes: bool = False) -> "SyncResult":
        """Combined pull + push. The ONLY place entity import + S3 sync-up happen.

        Lock → git pull (stash, merge, pop) → if conflicts: return early.
        If clean: git push → S3 sync up → entity import.
        If stale entities detected and confirm_deletes=False, returns early
        with needs_delete_confirmation=True and the list of pending deletes.
        Returns SyncResult.
        """
        from src.models.contracts.github import SyncResult

        async def _progress(phase: str, current: int = 0, total: int = 0) -> None:
            if job_id:
                from src.core.pubsub import publish_git_progress
                await publish_git_progress(job_id, phase, current, total)

        try:
            async with self.repo_manager.lock() as work_dir:
                repo = self._open_or_init(work_dir)

                # Step 1: Pull (fetch + merge, stash local changes)
                pull_result = await self._do_pull(work_dir, repo, job_id)

                if not pull_result.success:
                    if pull_result.conflicts:
                        # Return conflicts to UI — user resolves via desktop_resolve
                        return SyncResult(
                            success=False,
                            pull_success=False,
                            conflicts=pull_result.conflicts,
                            error="Merge conflicts detected",
                        )
                    return SyncResult(
                        success=False,
                        pull_success=False,
                        error=pull_result.error,
                    )

                # Step 2: Push
                await _progress("Pushing to remote...")
                push_result = self._do_push(work_dir, repo)

                if not push_result.success:
                    return SyncResult(
                        success=False,
                        pull_success=True,
                        push_success=False,
                        error=push_result.error,
                    )

                # Step 3: S3 sync up (so other containers see the changes)
                await _progress("Syncing to storage...")
                await self.repo_manager.sync_up(work_dir)

                # Refresh Redis module cache so editor + workers see new .py content
                from src.core.module_cache import refresh_modules_from_directory
                await refresh_modules_from_directory(work_dir)

                # Step 4: Entity import — always full import (safe, idempotent upserts)
                await _progress("Importing entities...")
                all_entity_changes: list = []
                removed_paths = _deleted_paths_in_head(repo)
                async with self.db.begin_nested():
                    entities_imported, entity_changes, removed_entity_ids = await self._import_all_entities(
                        work_dir, progress_fn=_progress,
                    )
                    all_entity_changes.extend(entity_changes)
                    await _progress("Updating file index...")
                    await self._update_file_index(work_dir, removed_paths=removed_paths)
                await self.db.commit()

                # Step 5: Clean up removed entities (gated on confirmation)
                await _progress("Checking for removed entities...")
                pending_deletes = await self._resolver._resolve_deletions(
                    work_dir=work_dir,
                    dry_run=True,
                    removed_entity_ids=removed_entity_ids,
                    removed_paths=removed_paths,
                )
                # Filter out "keep" entries (e.g. tables) — only actual removals need confirmation
                pending_removals = [e for e in pending_deletes if e.action != "keep"]
                if pending_removals and not confirm_deletes:
                    # Block sync — user must confirm deletions first
                    logger.info(
                        f"Sync blocked: {len(pending_removals)} entity deletion(s) require confirmation"
                    )
                    return SyncResult(
                        success=True,
                        needs_delete_confirmation=True,
                        pending_deletes=pending_removals,
                        pulled=pull_result.pulled,
                        pushed_commits=push_result.pushed_commits,
                        commit_sha=push_result.commit_sha,
                        entities_imported=entities_imported,
                        entity_changes=all_entity_changes,
                    )

                if pending_removals:
                    await _progress("Deleting removed entities...")
                    async with self.db.begin_nested():
                        deletion_changes = await self._resolver._resolve_deletions(
                            work_dir=work_dir,
                            removed_entity_ids=removed_entity_ids,
                            removed_paths=removed_paths,
                        )
                        all_entity_changes.extend(deletion_changes)
                    await self.db.commit()

                # Step 6: Sync app previews
                await _progress("Syncing app previews...")
                await self._sync_app_previews(work_dir)

                logger.info(
                    f"Sync complete: pushed={push_result.pushed_commits}, "
                    f"imported={entities_imported}, sha={push_result.commit_sha}"
                )
                return SyncResult(
                    success=True,
                    pulled=pull_result.pulled,
                    pushed_commits=push_result.pushed_commits,
                    commit_sha=push_result.commit_sha,
                    entities_imported=entities_imported,
                    entity_changes=all_entity_changes,
                )
        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            return SyncResult(success=False, error=str(e))

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

    def _do_resolve(self, work_dir: Path, repo: GitRepo, resolutions: dict[str, str]) -> "ResolveResult":
        """Core resolve logic for inline conflict resolution during sync_execute."""
        from src.models.contracts.github import ResolveResult

        merge_head = work_dir / ".git" / "MERGE_HEAD"
        is_merge = merge_head.exists()
        has_unmerged = bool(repo.index.unmerged_blobs())

        if not is_merge and not has_unmerged:
            return ResolveResult(success=False, error="No conflicts to resolve")

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

        if is_merge:
            repo.index.commit("Merge with conflict resolution")
        else:
            repo.index.commit("Apply stashed changes with conflict resolution")

        return ResolveResult(success=True)

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
    ) -> "tuple[int, list, dict[str, set[str]]]":
        """Import entities from the working tree into the DB (incremental).

        Computes a diff against current DB state and only resolves changed
        entities, matching the incremental approach used by CLI push.

        Returns tuple of (count, non-delete entity changes, explicit removed IDs).
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
            or manifest.events
        )
        if not has_entities:
            return 0, [], {}

        # Diff against current DB state to find what actually changed
        db_manifest = await generate_manifest(self.db)
        _filter_db_manifest_to_import_scope(db_manifest, manifest, work_dir)
        diff_changes, changed_ids = _diff_and_collect(manifest, db_manifest)
        removed_entity_ids = _collect_removed_entity_ids(diff_changes)

        if not changed_ids:
            # No entity-level changes detected — nothing to import
            return 0, [], {}

        # Resolve only changed entities
        await self._resolver.plan_import(
            manifest, work_dir, progress_fn=progress_fn, changed_ids=changed_ids,
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

        # Indexer side-effects: WorkflowIndexer for changed workflows
        from src.models.orm.workflows import Workflow as WfORM
        from src.services.file_storage.indexers.workflow import WorkflowIndexer

        wf_paths = {
            mwf.path for mwf in manifest.workflows.values()
            if (work_dir / mwf.path).exists() and mwf.id in changed_ids
        }
        if wf_paths:
            wf_result = await self.db.execute(
                select(WfORM).where(WfORM.path.in_(wf_paths))
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

        return count, entity_changes, removed_entity_ids

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
        preview_tasks = []
        for mapp in manifest.apps.values():
            try:
                preview_tasks.append(_sync_one_app(mapp.id, _safe_app_repo_path(mapp)))
            except ValueError as exc:
                logger.warning("Skipping unsafe app preview path: %s", exc)

        await asyncio.gather(*preview_tasks)

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
                count, _changes, removed_entity_ids = await self._import_all_entities(work_dir)
                await self._resolver._resolve_deletions(
                    work_dir=work_dir,
                    removed_entity_ids=removed_entity_ids,
                    removed_paths=_deleted_paths_in_head(GitRepo(str(work_dir))),
                )
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

    async def _update_file_index(
        self,
        work_dir: Path,
        removed_paths: set[str] | None = None,
    ) -> None:
        """Update file_index from all files in the working tree, remove stale entries.

        Optimized: prefetches existing (path, content_hash) pairs in one query,
        skips files whose hash hasn't changed, and batch-upserts the rest in
        chunks of 100.
        """
        from sqlalchemy import delete, text
        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.file_index import FileIndex
        from src.services.file_index_service import _is_text_file

        files = _walk_tree(work_dir)
        repo_paths = set(files.keys())

        # Prefetch all existing (path, content_hash) in one query
        existing_result = await self.db.execute(
            select(FileIndex.path, FileIndex.content_hash)
        )
        existing_hashes = {row[0]: row[1] for row in existing_result.all()}

        # Build list of rows that need upserting (changed or new)
        pending_upserts: list[dict] = []
        for rel_path, content in files.items():
            if not _is_text_file(rel_path):
                continue
            try:
                content_str = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            content_hash = _content_hash(content)

            # Skip if hash hasn't changed
            if existing_hashes.get(rel_path) == content_hash:
                continue

            pending_upserts.append({
                "path": rel_path,
                "content": content_str,
                "content_hash": content_hash,
            })

        # Batch upsert in chunks of 100
        CHUNK_SIZE = 100
        for i in range(0, len(pending_upserts), CHUNK_SIZE):
            chunk = pending_upserts[i : i + CHUNK_SIZE]
            stmt = insert(FileIndex).values(chunk).on_conflict_do_update(
                index_elements=[FileIndex.path],
                set_={
                    "content": insert(FileIndex).excluded.content,
                    "content_hash": insert(FileIndex).excluded.content_hash,
                    "updated_at": text("NOW()"),
                },
            )
            await self.db.execute(stmt)

        if pending_upserts:
            logger.info(f"File index: upserted {len(pending_upserts)} changed files, skipped {len(files) - len(pending_upserts)} unchanged")

        # Remove file_index entries only when git explicitly deleted those
        # paths in this sync. A partial checkout/import must not turn absence
        # from the current working tree into global file-index deletion.
        stale_paths = set(existing_hashes.keys()) - repo_paths
        if removed_paths is not None:
            stale_paths &= removed_paths
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
