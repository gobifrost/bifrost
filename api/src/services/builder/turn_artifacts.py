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
        self.prefix = f"{_ROOT}/{self.turn_id}/{self.dispatch_attempt}/"
        self.key = f"{self.prefix}output.zip"

    def tool_workspace_key(self, execution_id: UUID | str) -> str:
        """Return an execution-fenced key for an intermediate tool snapshot."""

        return f"{self.prefix}tools/{UUID(str(execution_id))}/workspace.zip"

    async def _write(
        self,
        key: str,
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
            key,
            bounded(),
            content_type="application/zip",
        )

    async def write_output(
        self,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        return await self._write(self.key, chunks, max_bytes=max_bytes)

    async def write_tool_workspace(
        self,
        execution_id: UUID | str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        return await self._write(
            self.tool_workspace_key(execution_id),
            chunks,
            max_bytes=max_bytes,
        )

    def iter_tool_workspace(
        self,
        execution_id: UUID | str,
    ) -> AsyncIterator[bytes]:
        return self.storage.iter_object_chunks(
            self.tool_workspace_key(execution_id),
            chunk_size=_CHUNK_SIZE,
        )

    async def write_from_path(
        self,
        path: Path,
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        """Stage a local worker archive through the same bounded stream path."""

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as source:
                while chunk := source.read(_CHUNK_SIZE):
                    yield chunk

        return await self.write_output(chunks(), max_bytes=max_bytes)

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
        """Delete final output and every execution-scoped tool snapshot."""

        async with self.storage.get_client() as client:
            while True:
                response = await client.list_objects_v2(
                    Bucket=self.settings.s3_bucket,
                    Prefix=self.prefix,
                )
                objects = [
                    {"Key": key}
                    for entry in response.get("Contents", [])
                    if (key := entry.get("Key"))
                ]
                if not objects:
                    return
                await client.delete_objects(
                    Bucket=self.settings.s3_bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )


__all__ = [
    "BuilderTurnArtifactStorage",
    "BuilderTurnOutputTooLarge",
]
