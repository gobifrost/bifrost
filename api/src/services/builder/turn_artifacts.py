"""Staging for untrusted external Builder-turn workspace archives."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.services.file_storage.s3_client import S3StorageClient

_ROOT = "_solution_builder_turns"
_BUILDER_ROOT = "_solution_builder"
_CHUNK_SIZE = 8 * 1024 * 1024


class BuilderTurnOutputTooLarge(ValueError):
    pass


class BuilderHarnessStateTooLarge(ValueError):
    pass


class BuilderTurnArtifactStorage:
    def __init__(
        self,
        turn_id: UUID | str,
        dispatch_attempt: int,
        settings: Settings | None = None,
    ) -> None:
        if dispatch_attempt < 1:
            raise ValueError("dispatch_attempt must be positive")
        self.turn_id = str(turn_id)
        self.dispatch_attempt = dispatch_attempt
        self.settings = settings or get_settings()
        self.storage = S3StorageClient(self.settings)
        self.key = (
            f"{_ROOT}/{self.turn_id}/{self.dispatch_attempt}/output.zip"
        )

    async def write_output(
        self,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        size = 0

        async def bounded() -> AsyncIterator[bytes]:
            nonlocal size
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise BuilderTurnOutputTooLarge(
                        f"Builder turn output exceeds the {max_bytes} byte limit"
                    )
                yield chunk

        return await self.storage.put_object_from_chunks(
            self.key,
            bounded(),
            content_type="application/zip",
        )

    async def copy_to_path(self, destination: Path) -> None:
        with destination.open("xb") as output:
            async for chunk in self.storage.iter_object_chunks(
                self.key,
                chunk_size=_CHUNK_SIZE,
            ):
                output.write(chunk)

    @staticmethod
    def checkpoint_key(
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
    ) -> str:
        return (
            f"{_BUILDER_ROOT}/{solution_id}/checkpoints/"
            f"{session_id}/{turn_id}/workspace.zip"
        )

    async def promote_checkpoint(
        self,
        *,
        solution_id: UUID | str,
        session_id: UUID | str,
    ) -> None:
        destination = self.checkpoint_key(solution_id, session_id, self.turn_id)
        async with self.storage.get_client() as client:
            try:
                await client.copy_object(
                    Bucket=self.settings.s3_bucket,
                    CopySource={"Bucket": self.settings.s3_bucket, "Key": self.key},
                    Key=destination,
                    ContentType="application/zip",
                    MetadataDirective="REPLACE",
                )
            except client.exceptions.NoSuchKey as exc:
                raise FileNotFoundError(self.key) from exc
            except Exception as exc:  # noqa: BLE001 - S3-compatible backends vary
                if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc):
                    raise FileNotFoundError(self.key) from exc
                raise

    def iter_checkpoint(
        self,
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
    ) -> AsyncIterator[bytes]:
        return self.storage.iter_object_chunks(
            self.checkpoint_key(solution_id, session_id, turn_id),
            chunk_size=_CHUNK_SIZE,
        )

    async def delete_checkpoint(
        self,
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
    ) -> None:
        async with self.storage.get_client() as client:
            await client.delete_object(
                Bucket=self.settings.s3_bucket,
                Key=self.checkpoint_key(solution_id, session_id, turn_id),
            )

    async def delete(self) -> None:
        async with self.storage.get_client() as client:
            await client.delete_object(Bucket=self.settings.s3_bucket, Key=self.key)


class BuilderHarnessStateStorage:
    """Attempt-staged and accepted OpenCode session archives.

    A runner can only upload beneath its fenced turn attempt. Completion copies
    that object to an immutable Solution/session/turn key. Normal turns select
    the latest successful database turn; an explicit checkpoint resume selects
    one failed/cancelled turn. Orphaned uploads never become active context.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.storage = S3StorageClient(self.settings)

    @staticmethod
    def staged_key(turn_id: UUID | str, dispatch_attempt: int) -> str:
        if dispatch_attempt < 1:
            raise ValueError("dispatch_attempt must be positive")
        return f"{_ROOT}/{turn_id}/{dispatch_attempt}/harness-state.zip"

    @staticmethod
    def accepted_key(
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
    ) -> str:
        return (
            f"{_BUILDER_ROOT}/{solution_id}/harness/"
            f"{session_id}/{turn_id}/state.zip"
        )

    async def write_staged(
        self,
        turn_id: UUID | str,
        dispatch_attempt: int,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        size = 0

        async def bounded() -> AsyncIterator[bytes]:
            nonlocal size
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise BuilderHarnessStateTooLarge(
                        f"Builder harness state exceeds the {max_bytes} byte limit"
                    )
                yield chunk

        return await self.storage.put_object_from_chunks(
            self.staged_key(turn_id, dispatch_attempt),
            bounded(),
            content_type="application/zip",
        )

    async def promote(
        self,
        *,
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
        dispatch_attempt: int,
    ) -> None:
        source = self.staged_key(turn_id, dispatch_attempt)
        destination = self.accepted_key(solution_id, session_id, turn_id)
        async with self.storage.get_client() as client:
            try:
                await client.copy_object(
                    Bucket=self.settings.s3_bucket,
                    CopySource={"Bucket": self.settings.s3_bucket, "Key": source},
                    Key=destination,
                    ContentType="application/zip",
                    MetadataDirective="REPLACE",
                )
            except client.exceptions.NoSuchKey as exc:
                raise FileNotFoundError(source) from exc
            except Exception as exc:  # noqa: BLE001 - S3-compatible backends vary
                if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc):
                    raise FileNotFoundError(source) from exc
                raise

    async def exists_accepted(
        self,
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
    ) -> bool:
        key = self.accepted_key(solution_id, session_id, turn_id)
        async with self.storage.get_client() as client:
            try:
                await client.head_object(Bucket=self.settings.s3_bucket, Key=key)
                return True
            except client.exceptions.NoSuchKey:
                return False
            except Exception as exc:  # noqa: BLE001 - S3-compatible backends vary
                if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc):
                    return False
                raise

    def iter_accepted(
        self,
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
    ) -> AsyncIterator[bytes]:
        return self.storage.iter_object_chunks(
            self.accepted_key(solution_id, session_id, turn_id),
            chunk_size=_CHUNK_SIZE,
        )

    async def delete_staged(
        self,
        turn_id: UUID | str,
        dispatch_attempt: int,
    ) -> None:
        async with self.storage.get_client() as client:
            await client.delete_object(
                Bucket=self.settings.s3_bucket,
                Key=self.staged_key(turn_id, dispatch_attempt),
            )

    async def delete_accepted(
        self,
        solution_id: UUID | str,
        session_id: UUID | str,
        turn_id: UUID | str,
    ) -> None:
        async with self.storage.get_client() as client:
            await client.delete_object(
                Bucket=self.settings.s3_bucket,
                Key=self.accepted_key(solution_id, session_id, turn_id),
            )


__all__ = [
    "BuilderHarnessStateStorage",
    "BuilderHarnessStateTooLarge",
    "BuilderTurnArtifactStorage",
    "BuilderTurnOutputTooLarge",
]
