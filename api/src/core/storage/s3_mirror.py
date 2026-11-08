"""Native S3 workspace acceleration; AWS CLI details stay inside the provider."""
import asyncio
import hashlib
import logging
import os
from pathlib import Path, PurePosixPath

from src.config import Settings
from src.core.storage import object_pages
from src.core.storage.contracts import SnapshotGuard, StorageError
from src.core.storage.s3 import S3ObjectStore

logger = logging.getLogger(__name__)


class S3WorkspaceMirror:
    follows_symlinks = True

    def __init__(self, settings: Settings):
        self._settings = settings

    def _uri(self, prefix: str) -> str:
        if not prefix or not prefix.endswith("/") or prefix.startswith("/") or any(
            part in {".", ".."} for part in prefix.split("/")
        ) or "\\" in prefix or PurePosixPath(prefix).as_posix() + "/" != prefix:
            raise ValueError("Invalid workspace object prefix")
        return f"s3://{self._settings.s3_bucket}/{prefix}"

    async def download(self, prefix: str, target: Path) -> None:
        uri = self._uri(prefix)
        target.mkdir(parents=True, exist_ok=True)
        await self._invalidate_ambiguous_sync_timestamps(prefix, target)
        await self._run_aws_cli(self._build_sync_cmd(uri, str(target), delete=True, exact_timestamps=True))

    async def upload(self, source: Path, prefix: str, *, validate_snapshot: SnapshotGuard | None = None) -> None:
        uri = self._uri(prefix)
        if validate_snapshot is not None:
            async with S3ObjectStore.from_settings(self._settings) as store:
                snapshot = {}
                async for page in object_pages(store, prefix):
                    for item in page.objects:
                        if not item.key.startswith(prefix):
                            raise StorageError("backend_error", provider_code="WorkspacePrefixEscape")
                        snapshot[item.key[len(prefix):]] = item.version
            validate_snapshot(snapshot)
        await self._run_aws_cli(self._build_sync_cmd(str(source), uri, delete=True))

    async def delete(self, prefix: str) -> None:
        cmd = ["aws", "s3", "rm", self._uri(prefix), "--recursive"]
        if self._settings.s3_endpoint_url:
            cmd.extend(["--endpoint-url", self._settings.s3_endpoint_url])
        await self._run_aws_cli([*cmd, "--only-show-errors"])

    async def _invalidate_ambiguous_sync_timestamps(self, prefix: str, target: Path) -> None:
        files = [path for path in target.rglob("*") if path.is_file()]
        if not files:
            return
        async with S3ObjectStore.from_settings(self._settings) as store:
            listing = {}
            async for page in object_pages(store, prefix):
                for item in page.objects:
                    if not item.key.startswith(prefix):
                        raise StorageError("backend_error", provider_code="WorkspacePrefixEscape")
                    listing[item.key[len(prefix):]] = item
        for path in files:
            remote = listing.get(path.relative_to(target).as_posix())
            local = path.stat()
            if remote is None:
                continue
            if remote.modified_at is None:
                raise StorageError("backend_error", provider_code="MissingWorkspaceTimestamp")
            if local.st_size != remote.size or local.st_mtime != remote.modified_at.timestamp():
                continue
            # Only this S3-specific optimization interprets a single-part ETag as MD5.
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, lambda: hashlib.md5(usedforsecurity=False)).hexdigest()
            if digest != remote.version:
                os.utime(path, (local.st_atime, 0))

    def _build_sync_cmd(
        self,
        source: str,
        dest: str,
        delete: bool = False,
        exact_timestamps: bool = False,
    ) -> list[str]:
        """Build the aws s3 sync command with proper flags."""
        cmd = ["aws", "s3", "sync", source, dest]
        if delete:
            cmd.append("--delete")
        if exact_timestamps:
            cmd.append("--exact-timestamps")
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
        communicating = asyncio.create_task(process.communicate())
        try:
            _stdout, stderr = await asyncio.shield(communicating)
        except BaseException:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    # The child exited after the return-code check; drain below.
                    pass
            # Do not release the repository lock while the owned process or its
            # pipe readers can still write. Repeated cancellation is deferred.
            draining = asyncio.gather(communicating, process.wait(), return_exceptions=True)
            while not draining.done():
                try:
                    await asyncio.shield(draining)
                except asyncio.CancelledError:
                    # Keep the lock until this same process and its pipes drain.
                    pass
            raise
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
