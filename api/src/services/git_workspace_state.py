"""Durable local Git state, separate from the platform's published _repo/ tree.

Callers hold the deployment repository lock. An immutable existing workspace
checkpoint is made visible only by a conditional, single-object pointer update.
The pointer retains the original editor-write baseline, including during merge
conflicts. A local receipt prevents hydration over unacknowledged local work.
"""

import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from src.core.storage import object_store, optional_read
from src.core.storage.contracts import ContentProperties, ObjectStore, WriteOptions
from src.services.git_repo_manager import STORAGE_BASELINE_FILE, WORKSPACE_CHECKPOINT_PREFIX

if TYPE_CHECKING:
    from src.services.git_repo_manager import GitRepoManager


STATE_KEY = f"{WORKSPACE_CHECKPOINT_PREFIX}/local-state.json"


class GitWorkspaceStateError(RuntimeError):
    """A handoff cannot safely replace or acknowledge local Git state."""


def tree_fingerprint(root: Path, *, follow_links: bool = False) -> str:
    """Hash file names and bytes, including Git internals, with bounded memory."""
    digest = hashlib.sha256()
    if root.is_symlink():
        raise GitWorkspaceStateError("Git workspace state cannot contain symbolic links")
    root = root.absolute()
    files = []
    for directory, directories, names in os.walk(root, followlinks=follow_links):
        current = Path(directory)
        if follow_links:
            parent = current.parent
            while parent != root.parent:
                if current.resolve() == parent.resolve():
                    raise GitWorkspaceStateError("Git workspace contains a symbolic-link cycle")
                parent = parent.parent
        for name in directories + names:
            path = current / name
            if not follow_links and path.is_symlink():
                raise GitWorkspaceStateError("Git workspace state cannot contain symbolic links")
            if path.is_file() and path.relative_to(root) != Path(".git") / STORAGE_BASELINE_FILE:
                files.append(path)
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        content = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                content.update(chunk)
        digest.update(content.digest())
    return digest.hexdigest()


@dataclass(frozen=True)
class WorkspaceState:
    checkpoint: str
    baseline: str
    fingerprint: str
    version: int = 1

    @classmethod
    def parse(cls, raw: bytes) -> "WorkspaceState":
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"checkpoint", "baseline", "fingerprint", "version"}:
            raise GitWorkspaceStateError("Invalid durable Git workspace state")
        state = cls(**data)
        try:
            valid_checkpoint = str(UUID(state.checkpoint)) == state.checkpoint
        except (ValueError, TypeError, AttributeError):
            valid_checkpoint = False
        if not valid_checkpoint or type(state.version) is not int or state.version != 1 or any(
            not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in (state.baseline, state.fingerprint)
        ):
            raise GitWorkspaceStateError("Invalid durable Git workspace state")
        return state


class GitWorkspaceState:
    def __init__(self, manager: "GitRepoManager"):
        self.manager = manager
        self.settings = manager._settings

    async def fingerprint(self, root: Path) -> str:
        return await asyncio.to_thread(
            tree_fingerprint, root, follow_links=self.manager._mirror.follows_symlinks,
        )

    def _get_store(self) -> ObjectStore:
        return object_store(self.settings)

    async def read(self) -> tuple[WorkspaceState | None, str | None]:
        async with self._get_store() as store:
            async with optional_read(store, STATE_KEY) as read:
                if read is None:
                    return None, None
                raw = bytearray()
                async for chunk in read.body.iter_chunks(4097):
                    raw.extend(chunk)
                    if len(raw) > 4096:
                        raise GitWorkspaceStateError("Durable Git workspace state is too large")
                return WorkspaceState.parse(bytes(raw)), store.serialize_version(read.info.version)

    @staticmethod
    def _receipt_path(root: Path) -> Path:
        # Outside the working tree: never published, indexed or checkpointed.
        return root.parent / f".{root.name}-bifrost-git-state.json"

    def _receipt(self, root: Path) -> dict | None:
        path = self._receipt_path(root)
        return json.loads(path.read_text()) if path.is_file() else None

    def record(self, root: Path, etag: str | None, fingerprint: str) -> None:
        path = self._receipt_path(root)
        temporary = path.with_name(f"{path.name}.{uuid4()}.tmp")
        try:
            temporary.write_text(json.dumps({"etag": etag, "fingerprint": fingerprint}))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    async def restore(self, root: Path, state: WorkspaceState | None, etag: str | None) -> None:
        receipt = self._receipt(root)
        if receipt is not None and receipt["etag"] == etag:
            if state is not None and not (root / ".git" / STORAGE_BASELINE_FILE).is_file():
                self.manager._write_storage_baseline(root, state.baseline)
            return
        fingerprint = await self.fingerprint(root)
        if receipt is not None and receipt["fingerprint"] != fingerprint:
            raise GitWorkspaceStateError(
                "Another executor changed Git state while this checkout has unsaved local work. "
                "Nothing was overwritten. Preserve that work before running Fetch."
            )
        if state is not None:
            if receipt is None and any(root.iterdir()):
                raise GitWorkspaceStateError(
                    "This checkout has no durable-state receipt. Nothing was overwritten. "
                    "Preserve local work before running Fetch."
                )
            with TemporaryDirectory(prefix=".bifrost-handoff-", dir=root) as temporary:
                staging = Path(temporary)
                await self.manager.restore_workspace_checkpoint(state.checkpoint, staging)
                # Retry restoration refreshes its baseline. Local handoff must not.
                self.manager._write_storage_baseline(staging, state.baseline)
                fingerprint = await self.fingerprint(staging)
                if fingerprint != state.fingerprint:
                    raise GitWorkspaceStateError("Durable Git workspace checkpoint changed")
                installing = asyncio.create_task(asyncio.to_thread(self._install, staging, root))
                try:
                    await asyncio.shield(installing)
                finally:
                    # Cancelling the await cannot stop a filesystem thread.
                    # Do not leave staging or the caller's repository-lock context
                    # while it writes, including repeated cancellation requests.
                    while not installing.done():
                        try:
                            await asyncio.shield(installing)
                        except asyncio.CancelledError:
                            # Defer repeated cancellation until the filesystem
                            # thread releases staging and the repository lock.
                            pass
                    installing.result()
            self.manager._restore_git_directories(root)
        elif receipt is not None and receipt["etag"] is not None:
            raise GitWorkspaceStateError("Durable Git workspace state disappeared; run Fetch")
        self.record(root, etag, fingerprint)

    @staticmethod
    def _install(staging: Path, root: Path) -> None:
        files = {path.relative_to(staging): path for path in staging.rglob("*") if path.is_file()}
        if any(name.parts[0] == staging.name for name in files):
            raise GitWorkspaceStateError("Workspace checkpoint collides with handoff staging")
        for path in root.rglob("*"):
            if staging in path.parents or path == staging:
                continue
            if path.is_symlink() or (path.is_file() and path.relative_to(root) not in files):
                path.unlink()
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path != staging and staging not in path.parents and path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        for name, path in files.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.replace(destination)

    async def persist(
        self, root: Path, previous: WorkspaceState | None, etag: str | None, *, force: bool = False,
    ) -> None:
        if not (root / ".git" / "HEAD").is_file():
            return
        marker = root / ".git" / STORAGE_BASELINE_FILE
        if marker.is_file():
            baseline = marker.read_text()
        elif previous is not None:
            # A failed sync_up deliberately removes the local marker. Retain
            # the last acknowledged source baseline; never adopt newer edits.
            baseline = previous.baseline
        else:
            raise GitWorkspaceStateError("Cannot retain Git state without a source baseline")
        fingerprint = await self.fingerprint(root)
        if (
            not force and previous is not None
            and previous.fingerprint == fingerprint and previous.baseline == baseline
        ):
            self.record(root, etag, fingerprint)
            return
        checkpoint = await self.manager.checkpoint_workspace(root)
        # Verify the stored bytes, not just a before/after hash of a mutable
        # local tree. A transient edit could otherwise disappear before the
        # second local hash while leaving a mixed snapshot remotely.
        with TemporaryDirectory(prefix="bifrost-git-verify-") as temporary:
            verification = Path(temporary) / "workspace"
            await self.manager.restore_workspace_checkpoint(checkpoint, verification)
            if await self.fingerprint(verification) != fingerprint:
                raise GitWorkspaceStateError("Git checkpoint readback did not match local state")
        if await self.fingerprint(root) != fingerprint:
            raise GitWorkspaceStateError("Git workspace changed during checkpoint creation")
        state = WorkspaceState(checkpoint, baseline, fingerprint)
        # Do not delete either checkpoint after an ambiguous PUT: its pointer
        # may already be committed. A later reader sees one complete snapshot.
        async with self._get_store() as store:
            result = await store.write(STATE_KEY, json.dumps(asdict(state)).encode(), options=WriteOptions(
                properties=ContentProperties(content_type="application/json"),
                create_only=etag is None,
                expected_version=store.restore_version(etag) if etag is not None else None,
            ))
            witness = store.serialize_version(result.version)
        self.record(root, witness, fingerprint)
        if previous is not None:
            await self.manager.delete_workspace_checkpoint(previous.checkpoint)
