"""
App Storage Service — Object storage scoped to _apps/ prefix.

Manages the app serving store:
  _apps/{app_id}/preview/   ← draft/editor files
  _apps/{app_id}/live/      ← published files for end users

Data flow:
1. Git sync/import: copy from _repo/{app_path}/ to _apps/{app_id}/preview/
2. Editor write: write to _apps/{app_id}/preview/
3. Publish: copy preview → live
4. Serve draft: read from preview (Redis cache → object storage fallback)
5. Serve live: read from live (Redis cache → object storage fallback)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Literal

from src.config import Settings, get_settings
from src.core.log_safety import log_safe
from src.core.storage import object_pages, object_store, optional_read
from src.core.storage.contracts import ObjectStore

logger = logging.getLogger(__name__)

APPS_PREFIX = "_apps/"
PUBLISH_COPY_CONCURRENCY = 16

AppMode = Literal["preview", "live"]


class AppStorageService:
    """Object storage scoped to _apps/ prefix for app serving."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    def _key(self, app_id: str, mode: AppMode, relative_path: str = "") -> str:
        """Build object key: _apps/{app_id}/{mode}/{relative_path}"""
        base = f"{APPS_PREFIX}{app_id}/{mode}/"
        if relative_path:
            return f"{base}{relative_path.lstrip('/')}"
        return base

    # -----------------------------------------------------------------
    # Sync preview from repo
    # -----------------------------------------------------------------

    async def sync_preview(self, app_id: str, source_dir_in_repo: str) -> int:
        """Copy all files from _repo/{source_dir}/ to _apps/{app_id}/preview/.

        Removes stale preview files that no longer exist in the source.

        Args:
            app_id: Application UUID as string.
            source_dir_in_repo: Directory path within _repo/ (e.g. "apps/tickbox-grc/").

        Returns:
            Number of files synced.
        """
        from src.services.app_resource_limits import validate_app_source_metadata
        from src.services.repo_storage import REPO_PREFIX

        source_prefix = f"{REPO_PREFIX}{source_dir_in_repo.rstrip('/')}/"
        preview_prefix = self._key(app_id, "preview")

        async with self._get_store() as client:
            source_entries = await self._list_keys_with_sizes(client, source_prefix)
            source_keys = list(source_entries)
            validate_app_source_metadata({
                key[len(source_prefix):]: size
                for key, size in source_entries.items()
                if key[len(source_prefix):]
            })

            # List existing preview files
            existing_preview_keys = await self._list_keys(client, preview_prefix)
            existing_relative = {
                k[len(preview_prefix):] for k in existing_preview_keys
            }

            synced = 0
            new_relative: set[str] = set()

            for source_key in source_keys:
                # Derive relative path within the app dir
                rel_path = source_key[len(source_prefix):]
                if not rel_path:
                    continue

                new_relative.add(rel_path)
                dest_key = f"{preview_prefix}{rel_path}"

                # Copy from source to preview
                await client.copy(await client.stat(source_key), dest_key)
                synced += 1

            # Remove stale preview files
            stale = existing_relative - new_relative
            for rel_path in stale:
                await client.delete(f"{preview_prefix}{rel_path}")

            if stale:
                logger.info(f"Removed {len(stale)} stale preview files for app {app_id}")

            logger.info(f"Synced {synced} files to preview for app {app_id}")

        await self.invalidate_render_cache(app_id)
        return synced

    async def sync_preview_compiled(
        self, app_id: str, source_dir_in_repo: str
    ) -> tuple[int, list[str]]:
        """Compile source files and write compiled JS to preview.

        Like sync_preview, but compiles .tsx/.ts files via AppCompilerService
        before writing to _apps/{app_id}/preview/.

        Args:
            app_id: Application UUID as string.
            source_dir_in_repo: Directory path within _repo/ (e.g. "apps/tickbox-grc/").

        Returns:
            Tuple of (files_synced, compile_errors).
        """
        from src.services.app_compiler import (
            AppCompilerInfrastructureError,
            AppCompilerService,
        )
        from src.services.app_resource_limits import (
            AppSourceCompilationError,
            validate_app_source_metadata,
            validate_app_source_write,
        )
        from src.services.repo_storage import REPO_PREFIX

        source_prefix = f"{REPO_PREFIX}{source_dir_in_repo.rstrip('/')}/"
        preview_prefix = self._key(app_id, "preview")

        async with self._get_store() as client:
            # List source metadata before any object body is read. This keeps a
            # Git preview from turning an arbitrary source prefix into an
            # unbounded API allocation.
            source_entries = await self._list_keys_with_sizes(client, source_prefix)
            source_keys = list(source_entries)
            source_sizes = {
                key[len(source_prefix):]: size
                for key, size in source_entries.items()
                if key[len(source_prefix):]
            }
            validate_app_source_metadata(source_sizes)

            # List existing preview files
            existing_preview_keys = await self._list_keys(client, preview_prefix)
            existing_relative = {
                k[len(preview_prefix):] for k in existing_preview_keys
            }

            # Read all source files and collect TS/TSX for compilation. No
            # preview object or cache changes occur until this complete staged
            # input compiles successfully.
            source_files: list[tuple[str, bytes]] = []  # (rel_path, content)
            for source_key in source_keys:
                rel_path = source_key[len(source_prefix):]
                if not rel_path:
                    continue
                async with client.open_read(source_key) as read:
                    content = await read.body.read()
                validate_app_source_write(rel_path, content)
                source_files.append((rel_path, content))

            # Batch-compile TS/TSX files
            ts_files = [
                (rel, content)
                for rel, content in source_files
                if rel.endswith((".tsx", ".ts"))
            ]
            compiled_map: dict[str, str] = {}
            compile_errors: list[str] = []

            if ts_files:
                compiler = AppCompilerService()
                batch_input = [
                    {"path": rel, "source": content.decode("utf-8")}
                    for rel, content in ts_files
                ]
                results = await compiler.compile_batch(batch_input)
                expected_paths = {path for path, _ in ts_files}
                result_paths = [result.path for result in results]
                if len(result_paths) != len(expected_paths) or set(result_paths) != expected_paths:
                    raise AppCompilerInfrastructureError(
                        "Compiler response did not cover every preview source file"
                    )

                for result in results:
                    if result.success and result.compiled:
                        compiled_map[result.path] = result.compiled
                    elif result.error:
                        compile_errors.append(f"{result.path}: {result.error}")
                        logger.warning(
                            f"Compilation failed for {result.path}: {result.error}"
                        )

            if compile_errors:
                raise AppSourceCompilationError(
                    "App preview compilation failed; existing preview artifacts were retained: "
                    + "; ".join(compile_errors[:5])
                )

            # Write to preview (compiled JS for TS/TSX, raw for others)
            synced = 0
            new_relative: set[str] = set()

            for rel_path, content in source_files:
                new_relative.add(rel_path)
                dest_key = f"{preview_prefix}{rel_path}"

                if rel_path in compiled_map:
                    write_content = compiled_map[rel_path].encode("utf-8")
                else:
                    write_content = content

                await client.write(dest_key, write_content)
                synced += 1

            # Remove stale preview files
            stale = existing_relative - new_relative
            for rel_path in stale:
                await client.delete(f"{preview_prefix}{rel_path}")

            if stale:
                logger.info(f"Removed {len(stale)} stale preview files for app {app_id}")

            logger.info(
                f"Synced {synced} compiled files to preview for app {app_id}"
                f" ({len(compile_errors)} compile errors)"
            )

        await self.invalidate_render_cache(app_id)
        return synced, compile_errors

    # -----------------------------------------------------------------
    # Single file operations
    # -----------------------------------------------------------------

    async def write_preview_file(
        self, app_id: str, relative_path: str, content: bytes
    ) -> None:
        """Write a single file to _apps/{app_id}/preview/ and bust render cache."""
        key = self._key(app_id, "preview", relative_path)
        async with self._get_store() as client:
            await client.write(key, content)
        await self.invalidate_render_cache(app_id)

    async def delete_preview_file(self, app_id: str, relative_path: str) -> None:
        """Delete a single file from _apps/{app_id}/preview/ and bust render cache."""
        key = self._key(app_id, "preview", relative_path)
        async with self._get_store() as client:
            await client.delete(key)
        await self.invalidate_render_cache(app_id)

    async def read_file(
        self, app_id: str, mode: AppMode, relative_path: str
    ) -> bytes:
        """Read a single file from _apps/{app_id}/{mode}/{path}.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        key = self._key(app_id, mode, relative_path)
        async with self._get_store() as client:
            async with optional_read(client, key) as read:
                if read is None:
                    raise FileNotFoundError(f"App file not found: {relative_path} (mode={mode})")
                return await read.body.read()

    async def list_files(self, app_id: str, mode: AppMode) -> list[str]:
        """List relative file paths in _apps/{app_id}/{mode}/.

        Returns:
            List of relative paths (e.g. ["pages/index.tsx", "components/Button.tsx"]).
        """
        prefix = self._key(app_id, mode)
        async with self._get_store() as client:
            keys = await self._list_keys(client, prefix)
            return [k[len(prefix):] for k in keys if k[len(prefix):]]

    async def list_files_with_metadata(self, app_id: str, mode: AppMode) -> dict[str, int]:
        """List App files with byte sizes before a caller reads their bodies."""
        prefix = self._key(app_id, mode)
        async with self._get_store() as client:
            entries = await self._list_keys_with_sizes(client, prefix)
        return {
            key[len(prefix):]: size
            for key, size in entries.items()
            if key[len(prefix):]
        }

    # -----------------------------------------------------------------
    # Publish: copy preview → live
    # -----------------------------------------------------------------

    async def publish(
        self,
        app_id: str,
        progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> int:
        """Promote the current preview bundle to live.

        Only artifacts declared by the freshly built ``manifest.json`` are
        promoted. Hashed chunks from older preview builds can accumulate under
        the preview prefix; copying all of them made publish time grow without
        bound and could promote stale artifacts. Bundle outputs are copied with
        bounded concurrency, then ``manifest.json`` is copied last as the live
        pointer. Stale live and preview objects are removed in provider batches.

        Returns:
            Number of files published.
        """
        preview_prefix = self._key(app_id, "preview")
        live_prefix = self._key(app_id, "live")

        async with self._get_store() as client:
            preview_keys = await self._list_keys(client, preview_prefix)
            preview_relative = {
                key[len(preview_prefix):]
                for key in preview_keys
                if key[len(preview_prefix):]
            }
            if "manifest.json" not in preview_relative:
                logger.warning(f"No preview files to publish for app {log_safe(app_id)}")
                return 0

            async with client.open_read(f"{preview_prefix}manifest.json") as read:
                manifest_bytes = await read.body.read()
                manifest_info = read.info
            artifacts = self._bundle_artifacts(manifest_bytes)
            missing = artifacts - preview_relative
            if missing:
                missing_sample = ", ".join(sorted(missing)[:5])
                raise ValueError(
                    "Preview bundle is incomplete; missing artifact(s): "
                    f"{missing_sample}"
                )

            output_artifacts = sorted(artifacts - {"manifest.json"})
            total = len(artifacts)
            completed = 0
            if progress_callback:
                await progress_callback(completed, total)

            semaphore = asyncio.Semaphore(PUBLISH_COPY_CONCURRENCY)

            async def _copy_output(rel_path: str) -> None:
                async with semaphore:
                    await client.copy(
                        await client.stat(f"{preview_prefix}{rel_path}"), f"{live_prefix}{rel_path}",
                    )

            tasks = [
                asyncio.create_task(_copy_output(rel_path))
                for rel_path in output_artifacts
            ]
            try:
                for task in asyncio.as_completed(tasks):
                    await task
                    completed += 1
                    if progress_callback:
                        await progress_callback(completed, total)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            # The manifest is the live bundle pointer, so publish it only after
            # every referenced output is durable.
            await client.copy(manifest_info, f"{live_prefix}manifest.json")
            completed += 1
            if progress_callback:
                await progress_callback(completed, total)

            live_keys = await self._list_keys(client, live_prefix)
            stale_live = [
                key for key in live_keys if key[len(live_prefix):] not in artifacts
            ]
            try:
                await self._delete_keys(client, stale_live)
            except Exception:
                logger.warning(
                    "Published current app manifest but failed to clean stale "
                    "live objects",
                    extra={"app_id": log_safe(app_id)},
                    exc_info=True,
                )

            stale_preview = [
                key
                for key in preview_keys
                if key[len(preview_prefix):] not in artifacts
            ]
            try:
                await self._delete_keys(client, stale_preview)
            except Exception:
                logger.warning(
                    "Published current app manifest but failed to clean stale "
                    "preview objects",
                    extra={"app_id": log_safe(app_id)},
                    exc_info=True,
                )

            published = len(artifacts)
            logger.info(
                f"Published {published} files for app {log_safe(app_id)}"
                f" (removed {len(stale_live)} stale live and "
                f"{len(stale_preview)} stale preview objects)"
            )

        await self.invalidate_render_cache(app_id)
        return published

    @staticmethod
    def _bundle_artifacts(manifest_bytes: bytes) -> set[str]:
        """Return the exact preview objects referenced by a bundle manifest."""
        try:
            manifest: Any = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Preview bundle manifest is invalid JSON") from exc

        outputs = manifest.get("outputs") if isinstance(manifest, dict) else None
        entry = manifest.get("entry") if isinstance(manifest, dict) else None
        if not isinstance(outputs, list) or not all(
            isinstance(item, str) and item for item in outputs
        ):
            raise ValueError("Preview bundle manifest has invalid outputs")
        artifacts = set(outputs)
        if not isinstance(entry, str) or entry not in artifacts:
            raise ValueError("Preview bundle manifest entry is not in outputs")
        artifacts.add("manifest.json")
        return artifacts

    async def _delete_keys(self, client: ObjectStore, keys: Iterable[str]) -> None:
        """Let the provider batch cleanup without exposing native request shapes."""
        await client.delete_many(list(keys))

    # -----------------------------------------------------------------
    # Render cache (Redis → object storage fallback)
    # -----------------------------------------------------------------

    @staticmethod
    def _render_cache_key(app_id: str, mode: AppMode) -> str:
        return f"bifrost:app_render:{app_id}:{mode}"

    async def get_render_cache(
        self, app_id: str, mode: AppMode
    ) -> dict[str, str] | None:
        """Try to read cached render bundle from Redis.

        Returns:
            dict of {rel_path: code} or None on cache miss.
        """
        try:
            from src.core.cache import get_shared_redis

            r = await get_shared_redis()
            data = await r.get(self._render_cache_key(app_id, mode))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Render cache read failed for app {app_id} ({mode}): {e}")
        return None

    async def set_render_cache(
        self, app_id: str, mode: AppMode, files: dict[str, str]
    ) -> None:
        """Write render bundle to Redis cache.

        Args:
            files: dict of {rel_path: code}
        """
        try:
            from src.core.cache import get_shared_redis

            r = await get_shared_redis()
            await r.set(
                self._render_cache_key(app_id, mode),
                json.dumps(files),
            )
        except Exception as e:
            logger.warning(f"Failed to set render cache for app {app_id} ({mode}): {e}")

    async def invalidate_render_cache(self, app_id: str) -> None:
        """Invalidate render cache for both draft and live modes."""
        try:
            from src.core.cache import get_shared_redis

            r = await get_shared_redis()
            await r.delete(
                self._render_cache_key(app_id, "preview"),
                self._render_cache_key(app_id, "live"),
            )
        except Exception as e:
            logger.warning(f"Failed to invalidate render cache for app {log_safe(app_id)}: {log_safe(e)}")

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    async def _list_keys(self, client: ObjectStore, prefix: str) -> list[str]:
        return list(await self._list_keys_with_sizes(client, prefix))

    async def _list_keys_with_sizes(self, client: ObjectStore, prefix: str) -> dict[str, int]:
        return {
            item.key: item.size
            async for page in object_pages(client, prefix)
            for item in page.objects if not item.key.endswith("/")
        }
