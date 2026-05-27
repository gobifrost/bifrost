"""Tests for repo storage service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Settings
from src.services.repo_storage import RepoStorage


@pytest.fixture
def mock_s3_client():
    client = AsyncMock()
    client.put_object = AsyncMock()
    client.get_object = AsyncMock()
    client.delete_object = AsyncMock()
    client.list_objects_v2 = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_write_prepends_repo_prefix(mock_s3_client):
    """Writing to 'workflows/test.py' should write to '_repo/workflows/test.py' in S3."""
    from src.services.repo_storage import RepoStorage

    storage = RepoStorage.__new__(RepoStorage)
    storage._bucket = "test-bucket"

    mock_s3_client.put_object = AsyncMock()

    await storage._write_to_s3(mock_s3_client, "workflows/test.py", b"print('hello')")

    mock_s3_client.put_object.assert_called_once()
    call_kwargs = mock_s3_client.put_object.call_args[1]
    assert call_kwargs["Key"] == "_repo/workflows/test.py"


@pytest.mark.asyncio
async def test_read_prepends_repo_prefix(mock_s3_client):
    """Reading 'workflows/test.py' should read from '_repo/workflows/test.py' in S3."""
    from src.services.repo_storage import RepoStorage

    storage = RepoStorage.__new__(RepoStorage)
    storage._bucket = "test-bucket"

    body_mock = AsyncMock()
    body_mock.read = AsyncMock(return_value=b"print('hello')")
    mock_s3_client.get_object = AsyncMock(return_value={"Body": body_mock})

    content = await storage._read_from_s3(mock_s3_client, "workflows/test.py")

    mock_s3_client.get_object.assert_called_once()
    call_kwargs = mock_s3_client.get_object.call_args[1]
    assert call_kwargs["Key"] == "_repo/workflows/test.py"
    assert content == b"print('hello')"


@pytest.mark.asyncio
async def test_list_prepends_and_strips_prefix(mock_s3_client):
    """Listing should use _repo/ prefix and strip it from results."""
    from src.services.repo_storage import RepoStorage

    storage = RepoStorage.__new__(RepoStorage)
    storage._bucket = "test-bucket"

    mock_s3_client.list_objects_v2 = AsyncMock(
        return_value={
            "Contents": [
                {"Key": "_repo/workflows/a.py"},
                {"Key": "_repo/workflows/b.py"},
            ],
            "IsTruncated": False,
        }
    )

    paths = await storage._list_from_s3(mock_s3_client, prefix="workflows/")

    assert paths == ["workflows/a.py", "workflows/b.py"]
    # Verify the prefix was sent to S3 with _repo/ prepended
    call_kwargs = mock_s3_client.list_objects_v2.call_args[1]
    assert call_kwargs["Prefix"] == "_repo/workflows/"


def test_compute_hash():
    """Compute SHA-256 hash of content."""
    from src.services.repo_storage import RepoStorage

    h = RepoStorage.compute_hash(b"hello world")
    assert len(h) == 64  # SHA-256 hex


def _mock_settings():
    s = MagicMock()
    s.object_storage_provider = "s3"
    s.s3_bucket = "test-bucket"
    s.s3_endpoint_url = "http://localhost:8333"
    s.s3_access_key = "test"
    s.s3_secret_key = "test"
    s.s3_region = "us-east-1"
    return s


def _settings(**overrides):
    values = {
        "secret_key": "x" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def test_repo_storage_uses_s3_backend_by_default():
    settings = _settings()
    repo = RepoStorage(settings=settings)

    assert repo._storage.__class__.__module__ == "src.services.file_storage.s3_client"
    assert repo._storage.__class__.__name__ == "S3StorageClient"
    assert repo._bucket == (settings.s3_bucket or "")


def test_repo_storage_uses_azure_blob_backend_when_configured():
    repo = RepoStorage(
        settings=_settings(
            object_storage_provider="azure_blob",
            azure_blob_account_url="https://example.blob.core.windows.net",
            azure_blob_container="bifrost-objects",
            azure_blob_auth="default_credential",
        )
    )

    assert (
        repo._storage.__class__.__module__
        == "src.services.file_storage.azure_blob_client"
    )
    assert repo._storage.__class__.__name__ == "AzureBlobStorageClient"
    assert repo._bucket == "bifrost-objects"


class TestListDirectory:
    """Test RepoStorage.list_directory() using S3 Delimiter."""

    @pytest.mark.asyncio
    async def test_list_directory_returns_files_and_folders(self):
        """Non-recursive list returns direct files and folder prefixes from S3."""
        repo = RepoStorage(settings=_mock_settings())

        s3_files = ["file_at_root.py"]
        s3_folders = ["apps/", "workflows/"]

        with patch.object(
            repo,
            "_list_directory_from_s3",
            new_callable=AsyncMock,
            return_value=(s3_files, s3_folders),
        ):
            files, folders = await repo.list_directory("")

        assert sorted(files) == ["file_at_root.py"]
        assert sorted(folders) == ["apps/", "workflows/"]

    @pytest.mark.asyncio
    async def test_list_directory_with_prefix(self):
        """List directory scoped to a prefix."""
        repo = RepoStorage(settings=_mock_settings())

        s3_files = ["apps/myapp/_layout.tsx"]
        s3_folders = ["apps/myapp/components/", "apps/myapp/pages/"]

        with patch.object(
            repo,
            "_list_directory_from_s3",
            new_callable=AsyncMock,
            return_value=(s3_files, s3_folders),
        ):
            files, folders = await repo.list_directory("apps/myapp/")

        assert sorted(files) == ["apps/myapp/_layout.tsx"]
        assert sorted(folders) == ["apps/myapp/components/", "apps/myapp/pages/"]

    @pytest.mark.asyncio
    async def test_list_directory_excludes_system_files(self):
        """Excluded paths (.git, __pycache__) are filtered out."""
        repo = RepoStorage(settings=_mock_settings())

        s3_files = ["__pycache__/test.cpython-312.pyc", ".git/config"]
        s3_folders = ["workflows/"]

        with patch.object(
            repo,
            "_list_directory_from_s3",
            new_callable=AsyncMock,
            return_value=(s3_files, s3_folders),
        ):
            files, folders = await repo.list_directory("")

        assert files == []
        assert folders == ["workflows/"]

    @pytest.mark.asyncio
    async def test_list_directory_empty(self):
        """Empty directory returns empty lists."""
        repo = RepoStorage(settings=_mock_settings())

        with patch.object(
            repo,
            "_list_directory_from_s3",
            new_callable=AsyncMock,
            return_value=([], []),
        ):
            files, folders = await repo.list_directory("")

        assert files == []
        assert folders == []


class TestListDirectoryFromS3:
    """Test _list_directory_from_s3 handles S3 Delimiter response correctly."""

    @pytest.mark.asyncio
    async def test_parses_contents_and_common_prefixes(self):
        """S3 Contents become files, CommonPrefixes become folders."""
        repo = RepoStorage(settings=_mock_settings())

        mock_client = AsyncMock()
        mock_client.list_objects_v2 = AsyncMock(
            return_value={
                "Contents": [
                    {"Key": "_repo/file_at_root.py"},
                ],
                "CommonPrefixes": [
                    {"Prefix": "_repo/apps/"},
                    {"Prefix": "_repo/workflows/"},
                ],
                "IsTruncated": False,
            }
        )

        files, folders = await repo._list_directory_from_s3(mock_client, "")

        assert files == ["file_at_root.py"]
        assert folders == ["apps/", "workflows/"]
        mock_client.list_objects_v2.assert_called_once()
        call_kwargs = mock_client.list_objects_v2.call_args[1]
        assert call_kwargs["Delimiter"] == "/"
        assert call_kwargs["Prefix"] == "_repo/"

    @pytest.mark.asyncio
    async def test_paginates_with_delimiter(self):
        """Paginated S3 response accumulates files and folders across pages."""
        repo = RepoStorage(settings=_mock_settings())

        mock_client = AsyncMock()
        mock_client.list_objects_v2 = AsyncMock(
            side_effect=[
                {
                    "Contents": [{"Key": "_repo/a.py"}],
                    "CommonPrefixes": [{"Prefix": "_repo/dir1/"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "token123",
                },
                {
                    "Contents": [{"Key": "_repo/b.py"}],
                    "CommonPrefixes": [{"Prefix": "_repo/dir2/"}],
                    "IsTruncated": False,
                },
            ]
        )

        files, folders = await repo._list_directory_from_s3(mock_client, "")

        assert files == ["a.py", "b.py"]
        assert folders == ["dir1/", "dir2/"]
        assert mock_client.list_objects_v2.call_count == 2
