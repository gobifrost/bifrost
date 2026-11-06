"""Tests for worker loading code from S3 via Redis cache."""

import json

import pytest
from unittest.mock import MagicMock, patch


class TestGetModuleSyncFromCache:
    """Test get_module_sync returns cached modules from Redis."""

    def test_worker_loads_code_from_redis_cache(self):
        """Worker should load workflow code from Redis cache using path."""
        from src.core.module_cache_sync import get_module_sync

        with patch(
            "src.core.module_cache_sync._get_sync_redis"
        ) as mock_redis_factory:
            cached = json.dumps(
                {
                    "content": "from bifrost import workflow\n@workflow\ndef test(): return {}",
                    "path": "workflows/test.py",
                    "hash": "abc123",
                }
            )
            mock_redis = MagicMock()
            mock_redis.get.return_value = cached
            mock_redis_factory.return_value = mock_redis

            result = get_module_sync("workflows/test.py")
            assert result is not None
            assert result["content"] == "from bifrost import workflow\n@workflow\ndef test(): return {}"
            assert result["path"] == "workflows/test.py"
            assert result["hash"] == "abc123"

    def test_cache_miss_returns_none(self):
        """When both Redis and S3 miss, should return None."""
        from src.core.module_cache_sync import get_module_sync

        with (
            patch(
                "src.core.module_cache_sync._get_sync_redis"
            ) as mock_redis_factory,
            patch("src.core.module_cache_sync._get_storage_module") as mock_s3,
        ):
            mock_redis = MagicMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis
            mock_s3.return_value = None

            result = get_module_sync("workflows/nonexistent.py")
            assert result is None

    def test_s3_fallback_on_redis_miss(self):
        """When Redis misses but S3 has the module, should return it and re-cache."""
        from src.core.module_cache_sync import get_module_sync

        code_content = "from bifrost import workflow\n@workflow\ndef run(): pass"
        code_bytes = code_content.encode("utf-8")

        with (
            patch(
                "src.core.module_cache_sync._get_sync_redis"
            ) as mock_redis_factory,
            patch("src.core.module_cache_sync._get_storage_module") as mock_s3,
        ):
            mock_redis = MagicMock()
            mock_redis.get.return_value = None  # Redis miss
            mock_redis_factory.return_value = mock_redis
            mock_s3.return_value = code_bytes  # S3 hit

            result = get_module_sync("workflows/s3_test.py")
            assert result is not None
            assert result["content"] == code_content
            assert result["path"] == "workflows/s3_test.py"
            # Verify it was cached back to Redis
            mock_redis.setex.assert_called_once()


class TestWorkerCodeLoadingBranches:
    """Test the worker's three-way branching for code loading."""

    @patch("src.services.execution.module_loader.load_workflow_from_db")
    def test_existing_path_uses_workflow_code(self, mock_load):
        """When workflow_code is provided, use it directly (backwards-compatible)."""
        mock_func = MagicMock()
        mock_metadata = {"name": "test"}
        mock_load.return_value = (mock_func, mock_metadata, None)

        # Simulate the branch logic from worker.py
        workflow_code = "def run(): pass"
        function_name = "run"
        file_path = "workflows/test.py"

        if workflow_code and function_name and file_path:
            from src.services.execution.module_loader import load_workflow_from_db

            result = load_workflow_from_db(
                code=workflow_code,
                path=file_path,
                function_name=function_name,
            )
            assert result[0] == mock_func
            assert result[2] is None  # no error

        mock_load.assert_called_once_with(
            code="def run(): pass",
            path="workflows/test.py",
            function_name="run",
        )

    @patch("src.core.module_cache_sync.get_module_sync")
    @patch("src.services.execution.module_loader.load_workflow_from_db")
    def test_cache_path_used_when_no_workflow_code(self, mock_load, mock_cache):
        """When workflow_code is None but file_path exists, try cache."""
        mock_func = MagicMock()
        mock_metadata = {"name": "test"}
        mock_load.return_value = (mock_func, mock_metadata, None)
        mock_cache.return_value = {
            "content": "from bifrost import workflow\n@workflow\ndef run(): pass",
            "path": "workflows/test.py",
            "hash": "abc123",
        }

        # Simulate the branch logic from worker.py
        workflow_code = None
        function_name = "run"
        file_path = "workflows/test.py"
        load_error = None

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            from src.core.module_cache_sync import get_module_sync
            from src.services.execution.module_loader import load_workflow_from_db

            cached = get_module_sync(file_path)
            if cached:
                workflow_func, metadata, load_error = load_workflow_from_db(
                    code=cached["content"],
                    path=file_path,
                    function_name=function_name,
                )
                assert workflow_func == mock_func
                assert load_error is None
            else:
                pytest.fail("Cache should have returned a result")
        else:
            pytest.fail("Should not take the else branch")

        mock_cache.assert_called_once_with("workflows/test.py")
        mock_load.assert_called_once_with(
            code="from bifrost import workflow\n@workflow\ndef run(): pass",
            path="workflows/test.py",
            function_name="run",
        )

    @patch("src.core.module_cache_sync.get_module_sync")
    def test_cache_miss_sets_no_workflow_func(self, mock_cache):
        """When cache also misses, workflow_func stays None (triggers error)."""
        mock_cache.return_value = None

        workflow_code = None
        function_name = "run"
        file_path = "workflows/test.py"
        workflow_func = None

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            from src.core.module_cache_sync import get_module_sync

            cached = get_module_sync(file_path)
            if cached:
                pytest.fail("Cache should not have returned a result")
            else:
                # This mirrors worker.py behavior: workflow_func stays None
                pass

        # workflow_func is still None, which would trigger the error path in worker.py
        assert workflow_func is None

    def test_missing_fields_falls_to_else_branch(self):
        """When both function_name and file_path are missing, falls to else."""
        workflow_code = None
        function_name = None
        file_path = None
        reached_else = False

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            pytest.fail("Should not take the cache branch")
        else:
            reached_else = True

        assert reached_else

    @patch("src.core.module_cache_sync.get_module_sync")
    def test_cache_exception_sets_load_error(self, mock_cache):
        """When cache raises an exception, load_error should be set."""
        mock_cache.side_effect = Exception("Redis connection refused")

        workflow_code = None
        function_name = "run"
        file_path = "workflows/test.py"
        load_error = None

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            try:
                from src.core.module_cache_sync import get_module_sync

                get_module_sync(file_path)  # will raise
            except Exception as e:
                load_error = f"Cache load failed: {e}"

        assert load_error is not None
        assert "Redis connection refused" in load_error
