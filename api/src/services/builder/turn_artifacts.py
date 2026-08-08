"""Staging for untrusted external Builder-turn workspace archives."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.services.file_storage.s3_client import S3StorageClient

_ROOT = "_solution_builder_turns"
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

    async def delete(self) -> None:
        async with self.storage.get_client() as client:
            await client.delete_object(Bucket=self.settings.s3_bucket, Key=self.key)


__all__ = [
    "BuilderTurnArtifactStorage",
    "BuilderTurnOutputTooLarge",
]
