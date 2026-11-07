"""Unit tests for read_file module cache guard."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bifrost.definition_validation import DefinitionValidationError


class TestReadFileModuleCacheGuard:
    """Verify get_module() is only called for .py files."""

    @pytest.mark.asyncio
    async def test_read_py_file_checks_module_cache(self):
        """Python files should check Redis module cache."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"content": "print('hello')", "hash": "abc123"}

            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock(spec=FileOperationsService)
            service.read_file = FileOperationsService.read_file.__get__(service)

            result = await service.read_file("workflows/test.py")

            mock_get.assert_called_once_with("workflows/test.py")
            assert result[0] == b"print('hello')"

    @pytest.mark.asyncio
    async def test_read_tsx_file_skips_module_cache(self):
        """Non-Python files should NOT check Redis module cache."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock()
            service._object_storage.read_uploaded_file = AsyncMock(return_value=b"<div>hello</div>")
            service.read_file = FileOperationsService.read_file.__get__(service)
            assert await service.read_file("apps/myapp/pages/index.tsx") == (b"<div>hello</div>", None)
            service._object_storage.read_uploaded_file.assert_awaited_once_with("_repo/apps/myapp/pages/index.tsx")

            mock_get.assert_not_called()


class TestWriteFileDecoratorAcceptance:
    @pytest.mark.asyncio
    async def test_python_cache_is_updated_before_search_index_failure(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = FileOperationsService.__new__(FileOperationsService)
        service.db = AsyncMock()
        service.settings = SimpleNamespace(s3_bucket="test-bucket")
        service._object_storage = MagicMock()
        s3 = AsyncMock()
        service._object_storage.get_store.return_value.__aenter__.return_value = s3
        service._compute_hash = MagicMock(return_value="source-hash")
        service._guess_content_type = MagicMock(return_value="text/plain")
        service._find_app_by_path = AsyncMock(return_value=None)

        with (
            patch("src.services.file_storage.file_ops.set_module", new_callable=AsyncMock) as cached,
            patch("src.services.file_storage.file_ops.FileIndexService.index", new_callable=AsyncMock,
                  side_effect=RuntimeError("index unavailable")),
            pytest.raises(RuntimeError, match="index unavailable"),
        ):
            await service.write_file("workflows/current.py", b"VALUE = 2\n")

        s3.write.assert_awaited_once()
        cached.assert_awaited_once_with("workflows/current.py", "VALUE = 2\n", "source-hash")

    @pytest.mark.asyncio
    async def test_rejects_decorator_source_before_storage_or_metadata_side_effects(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = FileOperationsService.__new__(FileOperationsService)
        service._object_storage = MagicMock()
        service._compute_hash = MagicMock()
        service._guess_content_type = MagicMock()
        service.db = AsyncMock()

        with pytest.raises(DefinitionValidationError):
            await service.write_file(
                "workflows/example.py",
                b"from bifrost import workflow\n@workflow(timeout_seconds=1)\ndef run(): pass\n",
            )

        service._object_storage.get_store.assert_not_called()
        service._compute_hash.assert_not_called()
        service._guess_content_type.assert_not_called()
        service.db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_syntax_error_uses_full_metadata_path_for_diagnostics(self):
        """Syntax failures must not be mistaken for decorator-free modules."""
        from src.services.file_storage.file_ops import FileOperationsService

        service = FileOperationsService.__new__(FileOperationsService)
        service.db = AsyncMock()
        service.settings = SimpleNamespace(s3_bucket="test-bucket")
        service._object_storage = MagicMock()
        s3 = AsyncMock()
        service._object_storage.get_store.return_value.__aenter__.return_value = s3
        service._compute_hash = MagicMock(return_value="source-hash")
        service._guess_content_type = MagicMock(return_value="text/plain")
        service._find_app_by_path = AsyncMock(return_value=None)
        service._extract_metadata = AsyncMock(
            return_value=(b"", False, False, None, [], None, None)
        )
        service._diagnostics = AsyncMock()

        with (
            patch(
                "bifrost.decorator_validation.validate_python_decorators"
            ),
            patch(
                "src.services.file_storage.file_ops.set_module",
                new_callable=AsyncMock,
            ),
            patch(
                "src.services.file_storage.file_ops.FileIndexService.index",
                new_callable=AsyncMock,
            ),
            patch(
                "src.core.pubsub.publish_file_activity", new_callable=AsyncMock
            ),
        ):
            await service.write_file(
                "workflows/broken.py",
                b"@workflow(\ndef run():\n    pass\n",
                skip_dirty_flag=True,
            )

        assert service._extract_metadata.await_args.kwargs["cached_ast"] is None
        assert service._extract_metadata.await_args.kwargs["cached_content_str"] is None


class TestMoveFileModuleCache:
    @staticmethod
    def _service(old_record):
        from src.services.file_storage.file_ops import FileOperationsService

        service = FileOperationsService.__new__(FileOperationsService)
        service.db = AsyncMock()
        service.settings = SimpleNamespace(s3_bucket="test-bucket")
        s3 = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=s3)
        context.__aexit__ = AsyncMock(return_value=None)
        service._object_storage = MagicMock()
        service._object_storage.get_store.return_value = context

        old_result = MagicMock()
        old_result.scalar_one_or_none.return_value = old_record
        new_result = MagicMock()
        new_result.scalar_one_or_none.return_value = None
        service.db.execute.side_effect = [old_result, new_result, MagicMock()]
        return service, s3

    @pytest.mark.asyncio
    async def test_move_invalidates_python_destination_after_storage_move_without_index_content(self):
        """Path-only source rows cannot leave a cached destination miss alive."""
        old_record = SimpleNamespace(content=None, content_hash="source-hash")
        service, s3 = self._service(old_record)
        events: list[str] = []

        async def copied(*_args, **_kwargs):
            events.append("copy")

        async def deleted(*_args, **_kwargs):
            events.append("delete")

        async def invalidated(path):
            events.append(f"invalidate:{path}")

        s3.copy.side_effect = copied
        s3.delete.side_effect = deleted
        with (
            patch("src.services.file_storage.file_ops.invalidate_module", side_effect=invalidated) as invalidate,
            patch("src.services.file_storage.file_ops.FileIndexService.move_index", new_callable=AsyncMock) as move_index,
        ):
            await service.move_file("workflows/old.py", "workflows/new.py")

        assert events == [
            "copy", "delete", "invalidate:workflows/old.py", "invalidate:workflows/new.py",
        ]
        assert [call.args for call in invalidate.await_args_list] == [
            ("workflows/old.py",), ("workflows/new.py",),
        ]
        move_index.assert_awaited_once_with("workflows/old.py", "workflows/new.py")

    @pytest.mark.asyncio
    async def test_move_propagates_copy_failure_after_invalidating_both_python_paths(self):
        """A failed S3 move is never reported as a successful file rename."""
        old_record = SimpleNamespace(content="VALUE = 1", content_hash="source-hash")
        service, s3 = self._service(old_record)
        error = RuntimeError("storage unavailable")
        s3.copy.side_effect = error

        with (
            patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock) as invalidate,
            patch("src.services.file_storage.file_ops.FileIndexService.move_index", new_callable=AsyncMock) as move_index,
            pytest.raises(RuntimeError, match="storage unavailable"),
        ):
            await service.move_file("workflows/old.py", "workflows/new.py")

        assert service.db.execute.await_count == 2
        assert [call.args for call in invalidate.await_args_list] == [
            ("workflows/old.py",), ("workflows/new.py",),
        ]
        move_index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_move_invalidates_both_paths_when_source_delete_fails_after_copy(self):
        old_record = SimpleNamespace(content=None, content_hash="source-hash")
        service, s3 = self._service(old_record)
        events: list[str] = []

        async def copied(*_args, **_kwargs):
            events.append("copy")

        async def delete_failed(*_args, **_kwargs):
            events.append("delete")
            raise RuntimeError("source delete failed")

        async def invalidated(path):
            events.append(f"invalidate:{path}")

        s3.copy.side_effect = copied
        s3.delete.side_effect = delete_failed
        with (
            patch("src.services.file_storage.file_ops.invalidate_module", side_effect=invalidated),
            patch("src.services.file_storage.file_ops.FileIndexService.move_index", new_callable=AsyncMock) as move_index,
            pytest.raises(RuntimeError, match="source delete failed"),
        ):
            await service.move_file("workflows/old.py", "workflows/new.py")

        assert events == [
            "copy", "delete", "invalidate:workflows/old.py", "invalidate:workflows/new.py",
        ]
        assert service.db.execute.await_count == 2
        move_index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_move_invalidates_both_paths_before_reraising_storage_cancellation(self):
        old_record = SimpleNamespace(content=None, content_hash="source-hash")
        service, s3 = self._service(old_record)

        async def source_delete_cancelled(*_args, **_kwargs):
            raise asyncio.CancelledError()

        s3.delete.side_effect = source_delete_cancelled
        with (
            patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock) as invalidate,
            patch("src.services.file_storage.file_ops.FileIndexService.move_index", new_callable=AsyncMock) as move_index,
            pytest.raises(asyncio.CancelledError),
        ):
            await service.move_file("workflows/old.py", "workflows/new.py")

        assert [call.args for call in invalidate.await_args_list] == [
            ("workflows/old.py",), ("workflows/new.py",),
        ]
        move_index.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("storage_error_type", [None, RuntimeError, asyncio.CancelledError])
    async def test_move_preserves_storage_failure_when_cache_cleanup_also_fails(self, storage_error_type):
        service, s3 = self._service(SimpleNamespace(content=None, content_hash="source-hash"))
        storage_error = storage_error_type() if storage_error_type else None
        s3.delete.side_effect = storage_error
        cache_error = RuntimeError("cache unavailable")
        expected = storage_error if storage_error is not None else cache_error
        with (
            patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock,
                  side_effect=cache_error),
            patch("src.services.file_storage.file_ops.FileIndexService.move_index", new_callable=AsyncMock) as move_index,
            pytest.raises(type(expected)) as raised,
        ):
            await service.move_file("workflows/old.py", "workflows/new.py")

        assert raised.value is expected
        move_index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_move_invalidates_before_database_failure(self):
        old_record = SimpleNamespace(content=None, content_hash="source-hash")
        service, s3 = self._service(old_record)
        existing_reads = list(service.db.execute.side_effect)[:2]
        service.db.execute.side_effect = [
            *existing_reads,
            RuntimeError("database unavailable"),
        ]

        with (
            patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock) as invalidate,
            patch("src.services.file_storage.file_ops.FileIndexService.move_index", new_callable=AsyncMock) as move_index,
            pytest.raises(RuntimeError, match="database unavailable"),
        ):
            await service.move_file("workflows/old.py", "workflows/new.py")

        s3.copy.assert_awaited_once()
        s3.delete.assert_awaited_once()
        assert [call.args for call in invalidate.await_args_list] == [
            ("workflows/old.py",), ("workflows/new.py",),
        ]
        move_index.assert_not_awaited()


class TestDeleteFileModuleCache:
    @pytest.mark.asyncio
    async def test_delete_propagates_module_cache_failure_before_best_effort_side_effects(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = FileOperationsService.__new__(FileOperationsService)
        service._delete_from_s3 = AsyncMock()
        service._invalidate_module_cache_if_python = AsyncMock(
            side_effect=RuntimeError("cache unavailable"),
        )
        service._remove_from_search_index = AsyncMock()
        service._handle_app_file_cleanup = AsyncMock()
        service._remove_metadata = AsyncMock()

        with pytest.raises(RuntimeError, match="cache unavailable"):
            await service.delete_file("workflows/deleted.py")

        service._delete_from_s3.assert_awaited_once_with("workflows/deleted.py")
        service._invalidate_module_cache_if_python.assert_awaited_once_with("workflows/deleted.py")
        service._remove_from_search_index.assert_not_awaited()
        service._handle_app_file_cleanup.assert_not_awaited()
        service._remove_metadata.assert_not_awaited()
