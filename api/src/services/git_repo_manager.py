"""
Git Repo Manager — S3-backed persistent git working tree.

Manages the lifecycle of a persistent local git working directory backed by
S3 _repo/. Uses `aws s3 sync` for efficient incremental transfer of the
entire directory tree including .git/ objects.

The working directory is persistent at PERSISTENT_WORK_DIR and is NOT deleted
between operations. This allows incremental syncs (only changed files are
transferred) and preserves the .git/ directory across operations.

Individual file writes (code editor, form/agent CRUD) continue using
the Python S3 client (RepoStorage/FileIndexService). This manager is
only used for bulk sync operations (git clone/fetch/merge/push).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

import redis.asyncio as redis

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)


GIT_LOCK_KEY = "bifrost:git-lock"
GIT_LOCK_TIMEOUT = 300  # 5 minutes

PERSISTENT_WORK_DIR = Path("/tmp/git")


class GitRepoManager:
    """Context manager that syncs _repo/ between S3 and a persistent local working dir."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

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
    async def checkout(self) -> AsyncIterator[Path]:
        """
        Acquire a deployment-scoped Redis lock, sync _repo/ from S3 to the
        persistent working dir, yield it, then sync back and release the lock.

        The lock prevents concurrent git operations from overwriting each
        other's changes in the shared S3 _repo/ prefix.

        The working directory is NOT deleted on exit — it persists for
        incremental syncs on subsequent operations.

        Usage:
            async with repo_manager.checkout() as work_dir:
                # work_dir contains the full _repo/ contents incl .git/
                repo = GitRepo(str(work_dir))
                ...
            # On exit: changes synced back to S3, lock released
        """
        async with self._acquire_lock():
            await self.sync_down(self.work_dir)
            yield self.work_dir
            await self.sync_up(self.work_dir)

    @asynccontextmanager
    async def checkout_readonly(self) -> AsyncIterator[Path]:
        """
        Like checkout() but skips sync_up. For read-only git operations
        (diff, status) that don't modify persistent state.

        The working directory is NOT deleted on exit.
        """
        async with self._acquire_lock():
            await self.sync_down(self.work_dir)
            yield self.work_dir
            # No sync_up — caller promises not to modify persistent state

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
        """Sync _repo/ from S3 to a local directory."""
        target.mkdir(parents=True, exist_ok=True)
        s3_uri = self._s3_uri()
        cmd = self._build_sync_cmd(source=s3_uri, dest=str(target))
        logger.info(f"sync_down: {s3_uri} -> {target}")
        await self._run_aws_cli(cmd)

    async def sync_up(self, source: Path) -> None:
        """Sync a local directory back to S3 _repo/ with --delete."""
        s3_uri = self._s3_uri()
        cmd = self._build_sync_cmd(source=str(source), dest=s3_uri, delete=True)
        logger.info(f"sync_up: {source} -> {s3_uri}")
        await self._run_aws_cli(cmd)

    async def has_git_dir(self) -> bool:
        """Check if .git/HEAD exists in S3 _repo/ (quick existence check)."""
        from src.services.repo_storage import RepoStorage
        storage = RepoStorage(self._settings)
        return await storage.exists(".git/HEAD")

    def _s3_uri(self) -> str:
        """Build the S3 URI for _repo/."""
        bucket = self._settings.s3_bucket
        return f"s3://{bucket}/_repo/"

    def _build_sync_cmd(
        self,
        source: str,
        dest: str,
        delete: bool = False,
    ) -> list[str]:
        """Build the aws s3 sync command with proper flags."""
        cmd = ["aws", "s3", "sync", source, dest]
        if delete:
            cmd.append("--delete")
        # For self-hosted or custom S3 endpoints
        endpoint_url = self._settings.s3_endpoint_url
        if endpoint_url:
            cmd.extend(["--endpoint-url", endpoint_url])
        # Quiet output to avoid noisy logs
        cmd.append("--only-show-errors")
        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the aws CLI process."""
        import os
        env = {**os.environ}
        if self._settings.s3_access_key:
            env["AWS_ACCESS_KEY_ID"] = self._settings.s3_access_key
        if self._settings.s3_secret_key:
            env["AWS_SECRET_ACCESS_KEY"] = self._settings.s3_secret_key
        if self._settings.s3_region:
            env["AWS_DEFAULT_REGION"] = self._settings.s3_region
        return env

    async def _run_aws_cli(self, cmd: list[str]) -> None:
        """Run an aws CLI command as a subprocess."""
        env = self._build_env()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            cmd_str = " ".join(cmd)
            raise RuntimeError(
                f"aws s3 sync failed (exit {process.returncode}): {stderr_text}\n"
                f"Command: {cmd_str}"
            )
        if stderr:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            if stderr_text:
                logger.debug(f"aws s3 sync stderr: {stderr_text}")
