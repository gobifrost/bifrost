"""Unit tests for requirements_cache module."""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestGetRequirementsSync:
    """Tests for the worker's synchronous requirements lookup."""

    def test_caches_authoritative_absence_without_s3_fallback(self):
        redis_client = MagicMock()
        redis_client.get.return_value = None

        with (
            patch(
                "src.core.module_cache_sync._get_sync_redis",
                return_value=redis_client,
            ),
            patch(
                "src.core.module_cache_sync._fetch_requirements_from_api",
                return_value=(True, None),
            ),
            patch("src.core.requirements_cache._read_requirements_from_s3") as read_s3,
        ):
            assert get_requirements_sync() is None

        read_s3.assert_not_called()
        cached = json.loads(redis_client.setex.call_args.args[2])
        assert cached["content"] == ""
        assert cached["hash"] == hashlib.sha256(b"").hexdigest()

    def test_api_failure_preserves_legacy_s3_fallback(self):
        redis_client = MagicMock()
        redis_client.get.return_value = None

        with (
            patch(
                "src.core.module_cache_sync._get_sync_redis",
                return_value=redis_client,
            ),
            patch(
                "src.core.module_cache_sync._fetch_requirements_from_api",
                return_value=(False, None),
            ),
            patch(
                "src.core.requirements_cache._read_requirements_from_s3",
                return_value="requests==2.32.0\n",
            ) as read_s3,
        ):
            assert get_requirements_sync() == "requests==2.32.0\n"

        read_s3.assert_called_once()


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

        with patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client):
            result = await get_requirements()

            assert result == cached
            mock_redis_client.get.assert_called_once_with(REQUIREMENTS_KEY)

    async def test_falls_back_to_s3_on_cache_miss(self, mock_redis_client):
        """Test get_requirements falls back to S3 when Redis cache is empty."""
        content = "flask==2.3.0\n"
        cached = {"content": content, "hash": "abc123"}

        # First call returns None (cache miss), second call returns data (after warm)
        mock_redis_client.get.side_effect = [None, json.dumps(cached)]
        mock_redis_client.setex = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.read.return_value = content.encode()

        with (
            patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            result = await get_requirements()

            assert result == cached
            # Called twice: first miss, then after warm
            assert mock_redis_client.get.call_count == 2
            # S3 was read for fallback
            mock_repo.read.assert_called_once_with("requirements.txt")

    async def test_returns_none_when_not_in_cache_or_s3(self, mock_redis_client):
        """Test get_requirements returns None when not in Redis or S3."""
        mock_redis_client.get.return_value = None
        mock_redis_client.setex = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.read.side_effect = Exception("NoSuchKey")

        with (
            patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            result = await get_requirements()

            assert result is None


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

        with patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client):
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
        """Test warm_requirements_cache loads from S3 and caches."""
        content = "flask==2.3.0\nrequests==2.31.0\n"
        mock_repo = AsyncMock()
        mock_repo.read.return_value = content.encode()

        with (
            patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client),
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
        """Test warm_requirements_cache returns False when requirements.txt not in S3."""
        mock_repo = AsyncMock()
        mock_repo.read.side_effect = Exception("NoSuchKey")

        with (
            patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client),
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
            patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client),
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

    async def test_writes_to_s3_and_cache(self, mock_redis_client):
        """Test save_requirements writes to S3 and updates Redis cache."""
        content = "flask==2.3.0\nrequests==2.31.0\n"
        mock_repo = AsyncMock()

        with (
            patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client),
            patch("src.services.repo_storage.RepoStorage", return_value=mock_repo),
        ):
            await save_requirements(content)

            # Verify S3 write
            mock_repo.write.assert_called_once_with("requirements.txt", content.encode())

            # Verify cache was updated
            mock_redis_client.setex.assert_called_once()

    async def test_computes_correct_hash(self, mock_redis_client):
        """Test save_requirements computes SHA-256 hash correctly."""
        content = "flask==2.3.0\nrequests==2.31.0\n"
        expected_hash = hashlib.sha256(content.encode()).hexdigest()
        mock_repo = AsyncMock()

        with (
            patch("src.core.requirements_cache.get_redis_client", return_value=mock_redis_client),
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
        content, is_update = append_package_to_requirements(current, "requests", "2.31.0")
        assert content == "flask==2.3.0\nrequests==2.31.0\n"
        assert is_update is False

    def test_updates_existing_package(self):
        """Test updating an existing package returns is_update=True."""
        current = "flask==2.3.0\nrequests==2.28.0\n"
        content, is_update = append_package_to_requirements(current, "requests", "2.31.0")
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
