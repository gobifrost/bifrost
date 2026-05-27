"""Unit tests for requirements_cache module."""

import hashlib
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.requirements_cache import (
    REQUIREMENTS_CACHE_TTL,
    REQUIREMENTS_KEY,
    CachedRequirements,
    append_package_to_requirements,
    get_requirements,
    get_requirements_sync,
    save_requirements,
    set_requirements,
    warm_requirements_cache,
)


class TestGetRequirements:
    """Tests for get_requirements function."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock async Redis client."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        return mock_client

    async def test_returns_cached_data(self, mock_redis_client):
        """Test get_requirements returns cached data."""
        cached = {"content": "flask==2.3.0\n", "hash": "abc123"}
        mock_redis_client.get.return_value = json.dumps(cached)

        with patch(
            "src.core.requirements_cache.get_redis_client",
            return_value=mock_redis_client,
        ):
            result = await get_requirements()

            assert result == cached
            mock_redis_client.get.assert_called_once_with(REQUIREMENTS_KEY)

    async def test_falls_back_to_object_storage_on_cache_miss(self, mock_redis_client):
        """Test get_requirements falls back to object storage when Redis cache is empty."""
        content = "flask==2.3.0\n"
        cached = {"content": content, "hash": "abc123"}

        # First call returns None (cache miss), second call returns data (after warm)
        mock_redis_client.get.side_effect = [None, json.dumps(cached)]
        mock_redis_client.setex = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.read.return_value = content.encode()

        with (
            patch(
                "src.core.requirements_cache.get_redis_client",
                return_value=mock_redis_client,
            ),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            result = await get_requirements()

            assert result == cached
            # Called twice: first miss, then after warm
            assert mock_redis_client.get.call_count == 2
            # Object storage was read for fallback
            mock_repo.read.assert_called_once_with("requirements.txt")

    async def test_returns_none_when_not_in_cache_or_object_storage(
        self, mock_redis_client
    ):
        """Test get_requirements returns None when not in Redis or object storage."""
        mock_redis_client.get.return_value = None
        mock_redis_client.setex = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.read.side_effect = Exception("NoSuchKey")

        with (
            patch(
                "src.core.requirements_cache.get_redis_client",
                return_value=mock_redis_client,
            ),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            result = await get_requirements()

            assert result is None


class TestGetRequirementsSync:
    """Tests for sync requirements cache lookup used by worker startup."""

    def test_returns_cached_data(self):
        """Test get_requirements_sync returns Redis cached content."""
        cached = {"content": "flask==2.3.0\n", "hash": "abc123"}
        mock_redis_client = Mock()
        mock_redis_client.get.return_value = json.dumps(cached)

        with patch(
            "src.core.module_cache_sync._get_sync_redis",
            return_value=mock_redis_client,
        ):
            assert get_requirements_sync() == cached["content"]
            mock_redis_client.get.assert_called_once_with(REQUIREMENTS_KEY)

    def test_azure_blob_provider_uses_blob_fallback_not_s3(self):
        """Test Azure Blob deployments do not use the S3 dead-man fallback."""
        content = "flask==2.3.0\n"
        mock_redis_client = Mock()
        mock_redis_client.get.return_value = None
        mock_blob_client = Mock()
        mock_blob_client.download_blob.return_value.readall.return_value = (
            content.encode()
        )
        mock_s3_client = Mock()

        with (
            patch.dict("os.environ", {"BIFROST_OBJECT_STORAGE_PROVIDER": "azure_blob"}),
            patch(
                "src.core.module_cache_sync._get_sync_redis",
                return_value=mock_redis_client,
            ),
            patch(
                "src.core.module_cache_sync._get_blob_container_client",
                return_value=mock_blob_client,
            ),
            patch(
                "src.core.module_cache_sync._get_s3_client", return_value=mock_s3_client
            ),
        ):
            assert get_requirements_sync() == content

        mock_blob_client.download_blob.assert_called_once_with("_repo/requirements.txt")
        mock_s3_client.get_object.assert_not_called()
        mock_redis_client.setex.assert_called_once()

    def test_s3_provider_keeps_s3_fallback(self):
        """Test S3 deployments still use S3 fallback on Redis miss."""
        content = "flask==2.3.0\n"
        mock_redis_client = Mock()
        mock_redis_client.get.return_value = None
        mock_s3_client = Mock()
        mock_s3_client.get_object.return_value = {
            "Body": Mock(read=Mock(return_value=content.encode()))
        }

        with (
            patch.dict(
                "os.environ",
                {
                    "BIFROST_OBJECT_STORAGE_PROVIDER": "s3",
                    "BIFROST_S3_BUCKET": "bucket",
                },
            ),
            patch(
                "src.core.module_cache_sync._get_sync_redis",
                return_value=mock_redis_client,
            ),
            patch(
                "src.core.module_cache_sync._get_s3_client", return_value=mock_s3_client
            ),
        ):
            assert get_requirements_sync() == content

        mock_s3_client.get_object.assert_called_once_with(
            Bucket="bucket",
            Key="_repo/requirements.txt",
        )
        mock_redis_client.setex.assert_called_once()


class TestSetRequirements:
    """Tests for set_requirements function."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock async Redis client."""
        mock_client = AsyncMock()
        mock_client.setex = AsyncMock()
        return mock_client

    async def test_caches_with_ttl(self, mock_redis_client):
        """Test set_requirements stores with correct TTL."""
        content = "flask==2.3.0\n"
        content_hash = "abc123"

        with patch(
            "src.core.requirements_cache.get_redis_client",
            return_value=mock_redis_client,
        ):
            await set_requirements(content, content_hash)

            mock_redis_client.setex.assert_called_once()
            call_args = mock_redis_client.setex.call_args
            assert call_args[0][0] == REQUIREMENTS_KEY
            assert call_args[0][1] == REQUIREMENTS_CACHE_TTL

            cached = json.loads(call_args[0][2])
            assert cached["content"] == content
            assert cached["hash"] == content_hash


class TestWarmRequirementsCache:
    """Tests for warm_requirements_cache function."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock async Redis client."""
        mock_client = AsyncMock()
        mock_client.setex = AsyncMock()
        return mock_client

    async def test_caches_from_s3(self, mock_redis_client):
        """Test warm_requirements_cache loads from object storage and caches."""
        content = "flask==2.3.0\nrequests==2.31.0\n"
        mock_repo = AsyncMock()
        mock_repo.read.return_value = content.encode()

        with (
            patch(
                "src.core.requirements_cache.get_redis_client",
                return_value=mock_redis_client,
            ),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            result = await warm_requirements_cache()

            assert result is True
            mock_repo.read.assert_called_once_with("requirements.txt")
            mock_redis_client.setex.assert_called_once()

            # Verify cached content
            call_args = mock_redis_client.setex.call_args
            cached = json.loads(call_args[0][2])
            assert cached["content"] == content

    async def test_returns_false_when_not_found(self, mock_redis_client):
        """Test warm_requirements_cache returns False when requirements.txt not in object storage."""
        mock_repo = AsyncMock()
        mock_repo.read.side_effect = Exception("NoSuchKey")

        with (
            patch(
                "src.core.requirements_cache.get_redis_client",
                return_value=mock_redis_client,
            ),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            result = await warm_requirements_cache()

            assert result is False
            mock_redis_client.setex.assert_not_called()

    async def test_returns_false_when_content_is_empty(self, mock_redis_client):
        """Test warm_requirements_cache returns False when file is empty."""
        mock_repo = AsyncMock()
        mock_repo.read.return_value = b"  \n  "

        with (
            patch(
                "src.core.requirements_cache.get_redis_client",
                return_value=mock_redis_client,
            ),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            result = await warm_requirements_cache()

            assert result is False
            mock_redis_client.setex.assert_not_called()


class TestSaveRequirements:
    """Tests for save_requirements function."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock async Redis client."""
        mock_client = AsyncMock()
        mock_client.setex = AsyncMock()
        return mock_client

    async def test_writes_to_object_storage_and_cache(self, mock_redis_client):
        """Test save_requirements writes to object storage and updates Redis cache."""
        content = "flask==2.3.0\nrequests==2.31.0\n"
        mock_repo = AsyncMock()

        with (
            patch(
                "src.core.requirements_cache.get_redis_client",
                return_value=mock_redis_client,
            ),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            await save_requirements(content)

            # Verify object storage write
            mock_repo.write.assert_called_once_with(
                "requirements.txt", content.encode()
            )

            # Verify cache was updated
            mock_redis_client.setex.assert_called_once()

    async def test_computes_correct_hash(self, mock_redis_client):
        """Test save_requirements computes SHA-256 hash correctly."""
        content = "flask==2.3.0\nrequests==2.31.0\n"
        expected_hash = hashlib.sha256(content.encode()).hexdigest()
        mock_repo = AsyncMock()

        with (
            patch(
                "src.core.requirements_cache.get_redis_client",
                return_value=mock_redis_client,
            ),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            await save_requirements(content)

            # Verify cache received correct hash
            call_args = mock_redis_client.setex.call_args
            cached = json.loads(call_args[0][2])
            assert cached["hash"] == expected_hash


class TestAppendPackageToRequirements:
    """Tests for append_package_to_requirements function."""

    def test_appends_new_package(self):
        """Test appending a new package returns is_update=False."""
        current = "flask==2.3.0\n"
        content, is_update = append_package_to_requirements(
            current, "requests", "2.31.0"
        )
        assert content == "flask==2.3.0\nrequests==2.31.0\n"
        assert is_update is False

    def test_updates_existing_package(self):
        """Test updating an existing package returns is_update=True."""
        current = "flask==2.3.0\nrequests==2.28.0\n"
        content, is_update = append_package_to_requirements(
            current, "requests", "2.31.0"
        )
        assert content == "flask==2.3.0\nrequests==2.31.0\n"
        assert is_update is True

    def test_case_insensitive_match(self):
        """Test case-insensitive package name matching returns is_update=True."""
        current = "Flask==2.3.0\n"
        content, is_update = append_package_to_requirements(current, "flask", "3.0.0")
        assert content == "flask==3.0.0\n"
        assert is_update is True

    def test_appends_without_version(self):
        """Test appending a package without a version returns is_update=False."""
        current = "flask==2.3.0\n"
        content, is_update = append_package_to_requirements(current, "requests", None)
        assert content == "flask==2.3.0\nrequests\n"
        assert is_update is False

    def test_empty_current(self):
        """Test appending to empty requirements returns is_update=False."""
        content, is_update = append_package_to_requirements("", "flask", "2.3.0")
        assert content == "flask==2.3.0\n"
        assert is_update is False

    def test_filters_empty_lines(self):
        """Test that empty lines are filtered out."""
        current = "flask==2.3.0\n\n\nrequests==2.31.0\n"
        content, is_update = append_package_to_requirements(current, "boto3", "1.0.0")
        assert content == "flask==2.3.0\nrequests==2.31.0\nboto3==1.0.0\n"
        assert is_update is False


class TestCachedRequirementsTypedDict:
    """Tests for the CachedRequirements TypedDict."""

    def test_cached_requirements_structure(self):
        """Verify CachedRequirements has expected fields."""
        requirements: CachedRequirements = {
            "content": "flask==2.3.0\nrequests==2.31.0\n",
            "hash": "abc123def456",
        }

        assert requirements["content"] == "flask==2.3.0\nrequests==2.31.0\n"
        assert requirements["hash"] == "abc123def456"


class TestKeyPatterns:
    """Tests for Redis key patterns."""

    def test_requirements_key(self):
        """Verify requirements key is correct."""
        assert REQUIREMENTS_KEY == "bifrost:requirements:content"
