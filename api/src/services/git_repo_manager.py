"""Git working-tree lifecycle over provider-owned bulk storage mirroring.

The manager owns repository locks, freshness baselines, Git directory recovery
and durable-checkpoint publication. Providers own transfer acceleration and
credential/signing details. Individual editor/SDK writes use the same selected
object-store service through source and file-index storage.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from uuid import UUID, uuid4
from contextlib import asynccontextmanager, nullcontext
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import redis.asyncio as redis

from src.config import Settings, get_settings
from src.core.storage import object_pages, object_store, optional_read, workspace_mirror
from src.core.storage.contracts import ObjectVersion, WriteOptions

if TYPE_CHECKING:
    from git import Repo as GitRepo

logger = logging.getLogger(__name__)


GIT_LOCK_KEY = "bifrost:git-lock"
GIT_LOCK_TIMEOUT = 300  # 5 minutes
WORKSPACE_CHECKPOINT_PREFIX = "_workspace_sync_checkpoints"

PERSISTENT_WORK_DIR = Path(os.environ.get("BIFROST_GIT_WORK_DIR", "/tmp/git"))
TREE_HASH_CHUNK_SIZE = 8 * 1024 * 1024
# Fingerprint of S3 _repo/ taken when the working dir last matched it. Kept
# inside .git/ so it lives and dies with the working dir it describes.
STORAGE_BASELINE_FILE = "bifrost-storage-baseline"


# user:password@ in an http(s) URL, as earlier releases wrote into .git/config.
_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@")


class WorkspaceStorageChanged(RuntimeError):
    """S3 _repo/ changed since the persistent working dir last matched it."""


def workspace_version_fingerprint(versions: Mapping[str, str]) -> str:
    """Fingerprint canonical opaque versions without interpreting provider tokens."""
    digest = hashlib.sha256()
    for path in sorted(versions):
        if path.startswith(".bifrost/"):
            continue
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        # Versions are canonicalized by their provider, never interpreted here.
        digest.update(versions[path].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class TreeEntryMetadata:
    """Content-independent metadata for a workspace file."""

    path: str
    size: int
    sha256: str


def iter_repo_files(root: Path) -> Iterator[Path]:
    """Yield workspace files without descending into Git internals."""
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        yield path


def hash_file(path: Path) -> tuple[int, str]:
    """Stream one file's hash without retaining its content."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(TREE_HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def iter_tree_metadata(root: Path) -> Iterator[TreeEntryMetadata]:
    """Yield metadata for each workspace file while retaining bounded memory."""
    for path in iter_repo_files(root):
        size, sha256 = hash_file(path)
        yield TreeEntryMetadata(path.relative_to(root).as_posix(), size, sha256)


def merge_in_progress(work_dir: Path, repo: GitRepo) -> bool:
    """Whether a merge or stash pop left conflicts that are not yet resolved."""
    return (work_dir / ".git" / "MERGE_HEAD").exists() or bool(repo.index.unmerged_blobs())


def _unsynced_work_warning(work_dir: Path, branch: str) -> str:
    """Describe local-only git work that Fetch, a full mirror of S3, discards."""
    from git import GitCommandError, InvalidGitRepositoryError
    from git import Repo as GitRepo

    try:
        repo = GitRepo(str(work_dir))
    except InvalidGitRepositoryError:
        return ""
    if merge_in_progress(work_dir, repo):
        return (
            " You have an unfinished merge with conflict resolutions. Fetch "
            "discards it, so you'll need to sync and resolve the conflicts again."
        )
    if not repo.head.is_valid():
        return ""
    try:
        ahead = int(repo.git.rev_list("--count", f"origin/{branch}..HEAD"))
    except GitCommandError:
        # Never fetched: no commit has been published yet.
        ahead = int(repo.git.rev_list("--count", "HEAD"))
    if ahead == 0:
        return ""
    noun = "commit" if ahead == 1 else "commits"
    pronoun = "it" if ahead == 1 else "them"
    return (
        f" You have {ahead} local {noun} not yet synced. Fetch discards "
        f"{pronoun}, so you'll need to redo {pronoun} after fetching."
    )


class GitRepoManager:
    """Context manager that syncs _repo/ between S3 and a persistent local working dir."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._mirror = workspace_mirror(self._settings)


    @property
    def work_dir(self) -> Path:
        """Return the persistent working directory, creating it if needed."""
        PERSISTENT_WORK_DIR.mkdir(parents=True, exist_ok=True)
        return PERSISTENT_WORK_DIR

    @property
    def is_initialized(self) -> bool:
        """Check if the persistent working directory has a .git/ directory."""
        return (PERSISTENT_WORK_DIR / ".git").is_dir()

    @asynccontextmanager
    async def checkout(
        self, *, reset_local_state: bool = False, isolated: bool = False,
    ) -> AsyncIterator[Path]:
        """
        Acquire a deployment-scoped Redis lock, sync _repo/ from S3 to the
        working dir, yield it, then sync back and release the lock.

        Reimport uses an isolated snapshot so published-file maintenance cannot
        replace pending Git state or its local receipt.

        The lock prevents concurrent git operations from overwriting each
        other's changes in the shared S3 _repo/ prefix.

        By default the working directory persists for incremental syncs.
        An isolated checkout and its receipt are removed on exit.

        Usage:
            async with repo_manager.checkout() as work_dir:
                # work_dir contains the full _repo/ contents incl .git/
                repo = GitRepo(str(work_dir))
                ...
            # On exit: changes synced back to S3, lock released
        """
        async with self._acquire_lock():
            from src.services.git_workspace_state import GitWorkspaceState

            with TemporaryDirectory(prefix="bifrost-git-checkout-") if isolated else nullcontext() as temporary:
                work_dir = Path(temporary) / "repo" if temporary is not None else self.work_dir
                state = GitWorkspaceState(self)
                previous, etag = await state.read() if reset_local_state else (None, None)
                await self.sync_down(work_dir)
                yield work_dir
                await self.sync_up(work_dir)
                if reset_local_state:
                    await state.persist(work_dir, previous, etag, force=True)

    @asynccontextmanager
    async def checkout_readonly(self) -> AsyncIterator[Path]:
        """Read published files without replacing pending executor-local work.

        Preflight and connection preview must not discard unacknowledged state
        left by a failed operation. Keep the snapshot and its source receipt in
        one temporary parent, cleaned up even when validation raises.
        """
        async with self._acquire_lock():
            with TemporaryDirectory(prefix="bifrost-git-readonly-") as temporary:
                snapshot = Path(temporary) / "repo"
                await self.sync_down(snapshot)
                yield snapshot

    @asynccontextmanager
    async def lock(self) -> AsyncIterator[Path]:
        """
        Acquire the Redis lock and yield the persistent working dir WITHOUT
        any S3 sync. For operations that don't need an S3 round-trip (e.g.,
        local-only git commands on an already-synced working tree).
        """
        async with self._acquire_lock():
            yield self.work_dir

    @asynccontextmanager
    async def workspace(self, *, readonly: bool = False) -> AsyncIterator[Path]:
        """Restore and durably retain local Git state across executor handoffs.

        Conflict markers stay in a private checkpoint, never in _repo/. Persist
        even when a merge raises: the next resolve/abort job needs that state.
        A killed process cannot acknowledge state it has not published.
        """
        from src.services.git_workspace_state import GitWorkspaceState

        async with self._acquire_lock():
            state = GitWorkspaceState(self)
            previous, etag = await state.read()
            work_dir = self.work_dir
            await state.restore(work_dir, previous, etag)
            try:
                yield work_dir
            finally:
                if not readonly:
                    await state.persist(work_dir, previous, etag)

    @asynccontextmanager
    async def _acquire_lock(self) -> AsyncIterator[None]:
        """Acquire a Redis lock for the duration of a git operation."""
        redis_url = self._settings.redis_url
        if not redis_url:
            # No Redis — skip locking (e.g., in tests)
            logger.debug("No Redis URL configured, skipping git lock")
            yield
            return

        client = redis.from_url(redis_url)
        lock = client.lock(GIT_LOCK_KEY, timeout=GIT_LOCK_TIMEOUT, blocking_timeout=GIT_LOCK_TIMEOUT)
        try:
            acquired = await lock.acquire()
            if not acquired:
                raise RuntimeError("Failed to acquire git lock — another git operation is in progress")
            logger.debug("Acquired git lock")
            yield
        finally:
            try:
                await lock.release()
                logger.debug("Released git lock")
            except Exception:
                pass  # Lock may have expired
            await client.aclose()

    async def sync_down(self, target: Path) -> None:
        """Mirror S3 _repo/ into a local directory, removing stale local files."""
        target.mkdir(parents=True, exist_ok=True)
        self._clear_storage_baseline(target)
        # Take the witness before transfer: concurrent editor writes remain detectable.
        fingerprint = await self._storage_fingerprint()
        await self._mirror.download("_repo/", target)
        self._restore_git_directories(target)
        self._write_storage_baseline(target, fingerprint)
        await self._record_source_receipt(target)

    async def _record_source_receipt(self, target: Path) -> None:
        from src.services.git_workspace_state import GitWorkspaceState

        state = GitWorkspaceState(self)
        state.record(target, None, await state.fingerprint(target))

    @staticmethod
    def _restore_git_directories(target: Path) -> None:
        # Object storage preserves files, not empty directories. Git requires
        # both directories even for an unborn branch or fully packed refs.
        # Restore the repository layout without initializing or changing HEAD.
        git_dir = target / ".git"
        if (git_dir / "HEAD").is_file():
            for name in ("objects", "refs"):
                (git_dir / name).mkdir(exist_ok=True)

    async def sync_up(self, source: Path) -> None:
        """Sync a local directory back to S3 _repo/ with --delete."""
        marker = source / ".git" / STORAGE_BASELINE_FILE
        baseline = marker.read_text() if marker.is_file() else None
        self._clear_storage_baseline(source)
        if baseline is None:
            await self._mirror.upload(source, "_repo/")
        else:
            def validate_snapshot(versions: Mapping[str, ObjectVersion]) -> None:
                if workspace_version_fingerprint(versions) != baseline:
                    raise WorkspaceStorageChanged("Workspace changed before upload; run Fetch before publishing local changes")
            await self._mirror.upload(source, "_repo/", validate_snapshot=validate_snapshot)
        self._write_storage_baseline(source, await self._storage_fingerprint())

    async def ensure_storage_unchanged(self, work_dir: Path, branch: str) -> None:
        """Refuse to act on a working dir that predates the current S3 _repo/.

        Editor, MCP and CLI writes reach S3 only. An operation that commits the
        working dir or syncs it back to S3 with --delete would otherwise revert
        or delete them.

        Callers hold the repository lock. A newly assigned worker has no local
        checkout, so hydrate it from the persisted repository first. Never do
        this over existing files: they may contain local commits or an unfinished
        merge, and must retain the normal fail-closed freshness check.
        """
        if work_dir.is_dir() and not any(work_dir.iterdir()):
            await self.sync_down(work_dir)
        baseline = work_dir / ".git" / STORAGE_BASELINE_FILE
        if not baseline.is_file() or baseline.read_text() != await self._storage_fingerprint():
            raise WorkspaceStorageChanged(
                "Workspace files changed since this working copy was last fetched, "
                "so continuing could overwrite them. Nothing was changed. "
                "Run Fetch to pick up the changes, then try again."
                + _unsynced_work_warning(work_dir, branch)
            )

    async def _storage_fingerprint(self) -> str:
        """Hash every S3 _repo/ key and ETag except .bifrost/, which commit regenerates."""
        from src.services.repo_storage import RepoStorage

        listing = await RepoStorage(self._settings).list_with_metadata("")
        return workspace_version_fingerprint({path: item.version for path, item in listing.items()})

    @staticmethod
    def _clear_storage_baseline(work_dir: Path) -> None:
        (work_dir / ".git" / STORAGE_BASELINE_FILE).unlink(missing_ok=True)

    @staticmethod
    def _write_storage_baseline(work_dir: Path, fingerprint: str) -> None:
        git_dir = work_dir / ".git"
        if git_dir.is_dir():
            (git_dir / STORAGE_BASELINE_FILE).write_text(fingerprint)

    async def checkpoint_workspace(self, source: Path) -> str:
        """Persist an exact workspace snapshot for a post-DB publication retry."""
        checkpoint_id = str(uuid4())
        await self._mirror.upload(source, self._checkpoint_prefix(checkpoint_id))
        return checkpoint_id

    async def restore_workspace_checkpoint(self, checkpoint_id: str, target: Path) -> None:
        """Restore a checkpoint exactly, including Git objects and uncommitted files."""
        target.mkdir(parents=True, exist_ok=True)
        await self._mirror.download(self._checkpoint_prefix(checkpoint_id), target)
        self._restore_git_directories(target)

    async def delete_workspace_checkpoint(self, checkpoint_id: str) -> None:
        """Remove a checkpoint only after successful publication."""
        await self._mirror.delete(self._checkpoint_prefix(checkpoint_id))

    async def remove_stored_credentials(self) -> None:
        """Sanitize published, checkpointed and durable local Git configurations."""
        from tempfile import TemporaryDirectory

        from src.services.git_workspace_state import (
            GitWorkspaceState, GitWorkspaceStateError,
        )

        async with self._acquire_lock():
            state = GitWorkspaceState(self)
            previous, etag = await state.read()
            with TemporaryDirectory(prefix="bifrost-git-credentials-") as raw:
                target = Path(raw) / "workspace"
                if previous is not None:
                    await self.restore_workspace_checkpoint(previous.checkpoint, target)
                    self._write_storage_baseline(target, previous.baseline)
                    if await state.fingerprint(target) != previous.fingerprint:
                        raise GitWorkspaceStateError("Durable Git workspace checkpoint changed")
                    config = target / ".git" / "config"
                    if config.is_file():
                        config.write_text(_URL_CREDENTIALS.sub(r"\1", config.read_text()))
                await self._remove_stored_credentials(
                    skip_checkpoint=previous.checkpoint if previous is not None else None,
                )
                if previous is not None:
                    await state.persist(target, previous, etag, force=True)

    async def _remove_stored_credentials(self, *, skip_checkpoint: str | None) -> None:
        """Strip credentials from every stored copy of .git/config.

        Covers this process's working dir, S3 _repo/ and every retry
        checkpoint. Another process's working dir is rewritten the next time a
        git operation opens it there.
        """
        local = PERSISTENT_WORK_DIR / ".git" / "config"
        if local.is_file():
            local.write_text(_URL_CREDENTIALS.sub(r"\1", local.read_text()))
        async with object_store(self._settings) as store:
            keys = ["_repo/.git/config"]
            async for page in object_pages(store, f"{WORKSPACE_CHECKPOINT_PREFIX}/"):
                keys.extend(item.key for item in page.objects if item.key.endswith("/.git/config")
                            and (skip_checkpoint is None or item.key != self._checkpoint_prefix(skip_checkpoint) + ".git/config"))
            for key in keys:
                async with optional_read(store, key) as read:
                    if read is None:
                        continue
                    config = (await read.body.read()).decode("utf-8")
                    version = read.info.version
                stripped = _URL_CREDENTIALS.sub(r"\1", config)
                if stripped != config:
                    await store.write(key, stripped.encode("utf-8"), options=WriteOptions(expected_version=version))

    async def has_git_dir(self) -> bool:
        """Check if .git/HEAD exists in S3 _repo/ (quick existence check)."""
        from src.services.repo_storage import RepoStorage
        storage = RepoStorage(self._settings)
        return await storage.exists(".git/HEAD")

    @staticmethod
    def _checkpoint_prefix(checkpoint_id: str) -> str:
        try:
            checkpoint = UUID(checkpoint_id)
        except ValueError as error:
            raise ValueError("Invalid workspace checkpoint ID") from error
        return f"{WORKSPACE_CHECKPOINT_PREFIX}/{checkpoint}/"
