"""
Unit tests for GitHub API Client.

Tests the GitHubAPIClient class methods with mocked HTTP responses.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.github_api import (
    GitHubAPIClient,
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    TreeItem,
)


class TestGitHubAPIClientInit:
    """Tests for GitHubAPIClient initialization."""

    def test_creates_client_with_token(self):
        """Test client initialization with token."""
        client = GitHubAPIClient(token="test-token")

        assert client.token == "test-token"
        assert "Bearer test-token" in client._headers["Authorization"]
        assert client._headers["Accept"] == "application/vnd.github+json"
        assert client._headers["X-GitHub-Api-Version"] == "2022-11-28"

    def test_creates_client_with_custom_timeout(self):
        """Test client initialization with custom timeout."""
        client = GitHubAPIClient(token="test-token", timeout=60.0)

        assert client.timeout == 60.0

    def test_uses_default_timeout(self):
        """Test client uses default timeout."""
        client = GitHubAPIClient(token="test-token")

        assert client.timeout == 30.0


class TestGitHubAPIClientRequest:
    """Tests for the _request method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_successful_get_request(self, client):
        """Test successful GET request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sha": "abc123", "url": "https://api.github.com/test"}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await client._request("GET", "/repos/owner/repo/test")

            assert result == {"sha": "abc123", "url": "https://api.github.com/test"}
            mock_client.request.assert_called_once_with(
                method="GET",
                url="https://api.github.com/repos/owner/repo/test",
                headers=client._headers,
                json=None,
            )

    @pytest.mark.asyncio
    async def test_successful_post_request_with_json(self, client):
        """Test successful POST request with JSON body."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"sha": "def456"}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await client._request(
                "POST",
                "/repos/owner/repo/git/blobs",
                json_data={"content": "test", "encoding": "utf-8"},
            )

            assert result == {"sha": "def456"}
            mock_client.request.assert_called_once_with(
                method="POST",
                url="https://api.github.com/repos/owner/repo/git/blobs",
                headers=client._headers,
                json={"content": "test", "encoding": "utf-8"},
            )

    @pytest.mark.asyncio
    async def test_raises_auth_error_on_401(self, client):
        """Test raises GitHubAuthError on 401 response."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(GitHubAuthError) as exc_info:
                await client._request("GET", "/repos/owner/repo/test")

            assert exc_info.value.status_code == 401
            assert "authentication failed" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_raises_rate_limit_error_on_403_with_zero_remaining(self, client):
        """Test raises GitHubRateLimitError on 403 with rate limit exhausted."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1234567890",
        }

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(GitHubRateLimitError) as exc_info:
                await client._request("GET", "/repos/owner/repo/test")

            assert exc_info.value.status_code == 403
            assert "rate limit" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_raises_auth_error_on_403_without_rate_limit(self, client):
        """Test raises GitHubAuthError on 403 without rate limit."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"X-RateLimit-Remaining": "100"}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(GitHubAuthError) as exc_info:
                await client._request("GET", "/repos/owner/repo/test")

            assert exc_info.value.status_code == 403
            assert "forbidden" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_raises_not_found_error_on_404(self, client):
        """Test raises GitHubNotFoundError on 404 response."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(GitHubNotFoundError) as exc_info:
                await client._request("GET", "/repos/owner/repo/nonexistent")

            assert exc_info.value.status_code == 404
            assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_raises_api_error_on_http_status_error(self, client):
        """Test raises GitHubAPIError on other HTTP errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {}
        mock_response.json.return_value = {"message": "Internal Server Error"}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(GitHubAPIError) as exc_info:
                await client._request("GET", "/repos/owner/repo/test")

            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_raises_api_error_on_timeout(self, client):
        """Test raises GitHubAPIError on timeout."""
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.side_effect = httpx.TimeoutException("Timed out")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(GitHubAPIError) as exc_info:
                await client._request("GET", "/repos/owner/repo/test")

            assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_raises_api_error_on_request_error(self, client):
        """Test raises GitHubAPIError on request error."""
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.request.side_effect = httpx.RequestError("Connection failed")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(GitHubAPIError) as exc_info:
                await client._request("GET", "/repos/owner/repo/test")

            assert "failed" in str(exc_info.value).lower()


class TestGetTree:
    """Tests for get_tree method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_gets_tree_non_recursive(self, client):
        """Test getting a tree without recursive flag."""
        api_response = {
            "sha": "tree-sha",
            "url": "https://api.github.com/repos/owner/repo/git/trees/tree-sha",
            "tree": [
                {"path": "file1.py", "mode": "100644", "type": "blob", "sha": "blob1", "size": 100},
                {"path": "dir1", "mode": "040000", "type": "tree", "sha": "tree1"},
                {"path": "file2.py", "mode": "100644", "type": "blob", "sha": "blob2", "size": 200},
            ],
            "truncated": False,
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.get_tree("owner/repo", "main")

            # Should only include blobs, not trees
            assert len(result) == 2
            assert "file1.py" in result
            assert "file2.py" in result
            assert "dir1" not in result

            # Verify request
            mock_request.assert_called_once_with(
                "GET", "/repos/owner/repo/git/trees/main"
            )

    @pytest.mark.asyncio
    async def test_gets_tree_recursive(self, client):
        """Test getting a tree with recursive flag."""
        api_response = {
            "sha": "tree-sha",
            "url": "https://api.github.com/repos/owner/repo/git/trees/tree-sha",
            "tree": [
                {"path": "file1.py", "mode": "100644", "type": "blob", "sha": "blob1", "size": 100},
                {"path": "dir1/file2.py", "mode": "100644", "type": "blob", "sha": "blob2", "size": 200},
            ],
            "truncated": False,
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.get_tree("owner/repo", "abc123", recursive=True)

            # Should include nested files
            assert len(result) == 2
            assert "file1.py" in result
            assert "dir1/file2.py" in result

            # Verify request includes recursive param
            mock_request.assert_called_once_with(
                "GET", "/repos/owner/repo/git/trees/abc123?recursive=1"
            )


class TestGetBlobContent:
    """Tests for get_blob_content method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_gets_base64_encoded_content(self, client):
        """Test getting base64-encoded blob content."""
        content = b"Hello, World!"
        encoded = base64.b64encode(content).decode("ascii")

        api_response = {
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "size": len(content),
            "url": "https://api.github.com/repos/owner/repo/git/blobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "content": encoded,
            "encoding": "base64",
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.get_blob_content("owner/repo", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

            assert result == content
            mock_request.assert_called_once_with(
                "GET", "/repos/owner/repo/git/blobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )

    @pytest.mark.asyncio
    async def test_handles_content_with_newlines(self, client):
        """Test handles base64 content with newlines (GitHub's format)."""
        content = b"Hello, World! This is a longer content."
        encoded = base64.b64encode(content).decode("ascii")
        # Add newlines like GitHub does
        encoded_with_newlines = "\n".join([encoded[i : i + 10] for i in range(0, len(encoded), 10)])

        api_response = {
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "size": len(content),
            "url": "https://api.github.com/repos/owner/repo/git/blobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "content": encoded_with_newlines,
            "encoding": "base64",
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.get_blob_content("owner/repo", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

            assert result == content


class TestCreateBlob:
    """Tests for create_blob method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_creates_blob(self, client):
        """Test creating a blob."""
        content = b"print('Hello, World!')"
        encoded = base64.b64encode(content).decode("ascii")

        api_response = {
            "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "url": "https://api.github.com/repos/owner/repo/git/blobs/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.create_blob("owner/repo", content)

            assert result == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            mock_request.assert_called_once_with(
                "POST",
                "/repos/owner/repo/git/blobs",
                json_data={
                    "content": encoded,
                    "encoding": "base64",
                },
            )


class TestCreateTree:
    """Tests for create_tree method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_creates_tree_with_base(self, client):
        """Test creating a tree with base tree."""
        tree_items = [
            TreeItem(path="file1.py", sha="blob1"),
            TreeItem(path="file2.py", sha="blob2"),
        ]

        api_response = {
            "sha": "new-tree-sha",
            "url": "https://api.github.com/repos/owner/repo/git/trees/new-tree-sha",
            "tree": [],
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.create_tree("owner/repo", tree_items, base_tree="base-sha")

            assert result == "new-tree-sha"
            mock_request.assert_called_once()

            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "/repos/owner/repo/git/trees"

            json_data = call_args[1]["json_data"]
            assert json_data["base_tree"] == "base-sha"
            assert len(json_data["tree"]) == 2

    @pytest.mark.asyncio
    async def test_creates_tree_with_deletion(self, client):
        """Test creating a tree with file deletion (sha=None)."""
        tree_items = [
            TreeItem(path="deleted_file.py", sha=None),  # Delete
            TreeItem(path="new_file.py", sha="blob1"),  # Add
        ]

        api_response = {
            "sha": "new-tree-sha",
            "url": "https://api.github.com/repos/owner/repo/git/trees/new-tree-sha",
            "tree": [],
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.create_tree("owner/repo", tree_items)

            assert result == "new-tree-sha"

            call_args = mock_request.call_args
            json_data = call_args[1]["json_data"]

            # Deleted file should not have sha in entry
            deleted_entry = next(e for e in json_data["tree"] if e["path"] == "deleted_file.py")
            assert "sha" not in deleted_entry

            # New file should have sha
            new_entry = next(e for e in json_data["tree"] if e["path"] == "new_file.py")
            assert new_entry["sha"] == "blob1"


class TestCreateCommit:
    """Tests for create_commit method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_creates_commit(self, client):
        """Test creating a commit."""
        api_response = {
            "sha": "dddddddddddddddddddddddddddddddddddddddd",
            "url": "https://api.github.com/repos/owner/repo/git/commits/dddddddddddddddddddddddddddddddddddddddd",
            "tree": {"sha": "tree-sha", "url": "tree-url"},
            "parents": [{"sha": "parent-sha", "url": "parent-url"}],
            "author": {"name": "Test", "email": "test@example.com", "date": "2024-01-08T00:00:00Z"},
            "committer": {"name": "Test", "email": "test@example.com", "date": "2024-01-08T00:00:00Z"},
            "message": "Test commit",
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.create_commit(
                "owner/repo",
                message="Test commit",
                tree="tree-sha",
                parents=["parent-sha"],
            )

            assert result == "dddddddddddddddddddddddddddddddddddddddd"
            mock_request.assert_called_once_with(
                "POST",
                "/repos/owner/repo/git/commits",
                json_data={
                    "message": "Test commit",
                    "tree": "tree-sha",
                    "parents": ["parent-sha"],
                },
            )

    @pytest.mark.asyncio
    async def test_creates_commit_with_author(self, client):
        """Test creating a commit with custom author."""
        api_response = {
            "sha": "dddddddddddddddddddddddddddddddddddddddd",
            "url": "https://api.github.com/repos/owner/repo/git/commits/dddddddddddddddddddddddddddddddddddddddd",
            "tree": {"sha": "tree-sha", "url": "tree-url"},
            "parents": [],
            "author": {"name": "Custom", "email": "custom@example.com", "date": "2024-01-08T00:00:00Z"},
            "committer": {"name": "Custom", "email": "custom@example.com", "date": "2024-01-08T00:00:00Z"},
            "message": "Test commit",
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            await client.create_commit(
                "owner/repo",
                message="Test commit",
                tree="tree-sha",
                parents=["parent-sha"],
                author={"name": "Custom", "email": "custom@example.com"},
            )

            call_args = mock_request.call_args
            json_data = call_args[1]["json_data"]
            assert json_data["author"] == {"name": "Custom", "email": "custom@example.com"}


class TestGetRef:
    """Tests for get_ref method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_gets_ref(self, client):
        """Test getting a ref."""
        api_response = {
            "ref": "refs/heads/main",
            "url": "https://api.github.com/repos/owner/repo/git/refs/heads/main",
            "object": {
                "sha": "cccccccccccccccccccccccccccccccccccccccc",
                "type": "commit",
                "url": "https://api.github.com/repos/owner/repo/git/commits/cccccccccccccccccccccccccccccccccccccccc",
            },
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.get_ref("owner/repo", "heads/main")

            assert result == "cccccccccccccccccccccccccccccccccccccccc"
            mock_request.assert_called_once_with(
                "GET", "/repos/owner/repo/git/ref/heads/main"
            )


class TestUpdateRef:
    """Tests for update_ref method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_updates_ref(self, client):
        """Test updating a ref."""
        api_response = {
            "ref": "refs/heads/main",
            "url": "https://api.github.com/repos/owner/repo/git/refs/heads/main",
            "object": {
                "sha": "dddddddddddddddddddddddddddddddddddddddd",
                "type": "commit",
                "url": "https://api.github.com/repos/owner/repo/git/commits/dddddddddddddddddddddddddddddddddddddddd",
            },
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            await client.update_ref("owner/repo", "heads/main", "dddddddddddddddddddddddddddddddddddddddd")

            mock_request.assert_called_once_with(
                "PATCH",
                "/repos/owner/repo/git/refs/heads/main",
                json_data={
                    "sha": "dddddddddddddddddddddddddddddddddddddddd",
                    "force": False,
                },
            )

    @pytest.mark.asyncio
    async def test_updates_ref_with_force(self, client):
        """Test force updating a ref."""
        api_response = {
            "ref": "refs/heads/main",
            "url": "https://api.github.com/repos/owner/repo/git/refs/heads/main",
            "object": {
                "sha": "dddddddddddddddddddddddddddddddddddddddd",
                "type": "commit",
                "url": "https://api.github.com/repos/owner/repo/git/commits/dddddddddddddddddddddddddddddddddddddddd",
            },
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            await client.update_ref("owner/repo", "heads/main", "dddddddddddddddddddddddddddddddddddddddd", force=True)

            call_args = mock_request.call_args
            json_data = call_args[1]["json_data"]
            assert json_data["force"] is True


class TestHighLevelHelpers:
    """Tests for high-level helper methods."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_get_branch_sha(self, client):
        """Test get_branch_sha convenience method."""
        with patch.object(client, "get_ref", new_callable=AsyncMock) as mock_get_ref:
            mock_get_ref.return_value = "branch-sha"

            result = await client.get_branch_sha("owner/repo", "main")

            assert result == "branch-sha"
            mock_get_ref.assert_called_once_with("owner/repo", "heads/main")

    @pytest.mark.asyncio
    async def test_update_branch(self, client):
        """Test update_branch convenience method."""
        with patch.object(client, "update_ref", new_callable=AsyncMock) as mock_update_ref:
            await client.update_branch("owner/repo", "main", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")

            mock_update_ref.assert_called_once_with(
                "owner/repo", "heads/main", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", force=False
            )

    @pytest.mark.asyncio
    async def test_update_branch_with_force(self, client):
        """Test update_branch with force flag."""
        with patch.object(client, "update_ref", new_callable=AsyncMock) as mock_update_ref:
            await client.update_branch("owner/repo", "main", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", force=True)

            mock_update_ref.assert_called_once_with(
                "owner/repo", "heads/main", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", force=True
            )


class TestGetCommit:
    """Tests for get_commit method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_gets_commit(self, client):
        """Test getting a commit."""
        api_response = {
            "sha": "cccccccccccccccccccccccccccccccccccccccc",
            "url": "https://api.github.com/repos/owner/repo/git/commits/cccccccccccccccccccccccccccccccccccccccc",
            "tree": {"sha": "tree-sha", "url": "tree-url"},
            "parents": [{"sha": "parent-sha", "url": "parent-url"}],
            "author": {"name": "Author", "email": "author@example.com", "date": "2024-01-08T00:00:00Z"},
            "committer": {"name": "Committer", "email": "committer@example.com", "date": "2024-01-08T00:00:00Z"},
            "message": "Test commit message",
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.get_commit("owner/repo", "cccccccccccccccccccccccccccccccccccccccc")

            assert result.sha == "cccccccccccccccccccccccccccccccccccccccc"
            assert result.tree.sha == "tree-sha"
            assert len(result.parents) == 1
            assert result.parents[0].sha == "parent-sha"
            assert result.message == "Test commit message"
            assert result.author.name == "Author"

            mock_request.assert_called_once_with(
                "GET", "/repos/owner/repo/git/commits/cccccccccccccccccccccccccccccccccccccccc"
            )


class TestExceptionClasses:
    """Tests for exception classes."""

    def test_github_api_error_str(self):
        """Test GitHubAPIError string representation."""
        error = GitHubAPIError("Test error", status_code=500)
        assert "GitHubAPIError(500): Test error" in str(error)

    def test_github_api_error_str_without_status(self):
        """Test GitHubAPIError string without status code."""
        error = GitHubAPIError("Test error")
        assert "GitHubAPIError: Test error" in str(error)

    def test_github_api_error_stores_response_body(self):
        """Test GitHubAPIError stores response body."""
        error = GitHubAPIError(
            "Test error",
            status_code=400,
            response_body={"message": "Bad Request"},
        )
        assert error.response_body == {"message": "Bad Request"}

    def test_rate_limit_error_inherits_from_api_error(self):
        """Test GitHubRateLimitError inherits from GitHubAPIError."""
        error = GitHubRateLimitError("Rate limit exceeded")
        assert isinstance(error, GitHubAPIError)

    def test_not_found_error_inherits_from_api_error(self):
        """Test GitHubNotFoundError inherits from GitHubAPIError."""
        error = GitHubNotFoundError("Not found")
        assert isinstance(error, GitHubAPIError)

    def test_auth_error_inherits_from_api_error(self):
        """Test GitHubAuthError inherits from GitHubAPIError."""
        error = GitHubAuthError("Auth failed")
        assert isinstance(error, GitHubAPIError)


class TestListCommits:
    """Tests for list_commits method."""

    @pytest.fixture
    def client(self):
        """Create a GitHubAPIClient instance."""
        return GitHubAPIClient(token="test-token")

    @pytest.mark.asyncio
    async def test_lists_commits_default_params(self, client):
        """Test listing commits with default parameters."""
        api_response = [
            {
                "sha": "commit1-sha",
                "url": "https://api.github.com/repos/owner/repo/commits/commit1-sha",
                "html_url": "https://github.com/owner/repo/commit/commit1-sha",
                "commit": {
                    "message": "First commit",
                    "author": {"name": "Author", "email": "author@example.com", "date": "2024-01-08T00:00:00Z"},
                    "committer": {"name": "Committer", "email": "committer@example.com", "date": "2024-01-08T00:00:00Z"},
                    "tree": {"sha": "tree1-sha", "url": "tree1-url"},
                },
                "author": {"login": "author", "id": 123, "type": "User"},
                "committer": {"login": "committer", "id": 456, "type": "User"},
                "parents": [{"sha": "parent-sha", "url": "parent-url"}],
            },
            {
                "sha": "commit2-sha",
                "url": "https://api.github.com/repos/owner/repo/commits/commit2-sha",
                "commit": {
                    "message": "Second commit",
                    "author": {"name": "Author", "email": "author@example.com", "date": "2024-01-07T00:00:00Z"},
                    "committer": {"name": "Author", "email": "author@example.com", "date": "2024-01-07T00:00:00Z"},
                    "tree": {"sha": "tree2-sha", "url": "tree2-url"},
                },
                "author": None,
                "committer": None,
                "parents": [],
            },
        ]

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.list_commits("owner/repo")

            assert len(result) == 2
            assert result[0].sha == "commit1-sha"
            assert result[0].commit.message == "First commit"
            assert result[0].author is not None
            assert result[0].author.login == "author"
            assert result[1].sha == "commit2-sha"
            assert result[1].author is None

            # Default params - no query string
            mock_request.assert_called_once_with("GET", "/repos/owner/repo/commits")

    @pytest.mark.asyncio
    async def test_lists_commits_with_branch(self, client):
        """Test listing commits for a specific branch."""
        api_response = [
            {
                "sha": "cccccccccccccccccccccccccccccccccccccccc",
                "url": "https://api.github.com/repos/owner/repo/commits/cccccccccccccccccccccccccccccccccccccccc",
                "commit": {
                    "message": "Commit on feature branch",
                    "author": {"name": "Author", "email": "author@example.com", "date": "2024-01-08T00:00:00Z"},
                    "committer": {"name": "Author", "email": "author@example.com", "date": "2024-01-08T00:00:00Z"},
                    "tree": {"sha": "tree-sha", "url": "tree-url"},
                },
                "parents": [],
            },
        ]

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.list_commits("owner/repo", sha="feature-branch")

            assert len(result) == 1
            assert result[0].commit.message == "Commit on feature branch"

            mock_request.assert_called_once_with("GET", "/repos/owner/repo/commits?sha=feature-branch")

    @pytest.mark.asyncio
    async def test_lists_commits_with_pagination(self, client):
        """Test listing commits with custom pagination."""
        api_response = []

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.list_commits("owner/repo", per_page=50, page=2)

            assert result == []
            mock_request.assert_called_once_with(
                "GET", "/repos/owner/repo/commits?per_page=50&page=2"
            )

    @pytest.mark.asyncio
    async def test_lists_commits_with_all_params(self, client):
        """Test listing commits with all parameters."""
        api_response = []

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = api_response

            result = await client.list_commits("owner/repo", sha="main", per_page=100, page=3)

            assert result == []
            mock_request.assert_called_once_with(
                "GET", "/repos/owner/repo/commits?sha=main&per_page=100&page=3"
            )


class TestTreeItem:
    """Tests for TreeItem dataclass."""

    def test_creates_tree_item_with_defaults(self):
        """Test TreeItem with default values."""
        item = TreeItem(path="test.py", sha="abc123")

        assert item.path == "test.py"
        assert item.sha == "abc123"
        assert item.mode == "100644"
        assert item.type == "blob"

    def test_creates_tree_item_for_deletion(self):
        """Test TreeItem for file deletion."""
        item = TreeItem(path="deleted.py", sha=None)

        assert item.path == "deleted.py"
        assert item.sha is None


class TestPathSegmentValidators:
    """Tests for the path-segment validators that close py/partial-ssrf (#217).

    These validators sit between user input (router query params) and
    f-string-built GitHub API URLs. They enforce shape *and* return a
    quote()-cleansed value so CodeQL recognizes the data flow as
    sanitized.
    """

    def test_validate_repo_accepts_simple_owner_name(self):
        from src.services.github_api import _validate_repo

        assert _validate_repo("octocat/Hello-World") == "octocat/Hello-World"

    def test_validate_repo_preserves_separator_slash(self):
        from src.services.github_api import _validate_repo

        # The single owner/name slash must remain unencoded so the URL
        # still parses to the right path. The validator uses quote(safe="/").
        result = _validate_repo("a.b_c-d/e.f_g-h")
        assert result == "a.b_c-d/e.f_g-h"
        assert "%2F" not in result

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "owner/../etc",
            "owner/repo/extra",  # too many segments
            "owner",  # no slash
            "/owner/repo",  # leading slash
            "owner/repo/",  # trailing slash
            "owner/repo;@evil.com",
            "owner/repo space",
            "owner/repo?query=1",
            "owner/repo#frag",
            "owner/repo:8080",
            "..",
            "",
        ],
    )
    def test_validate_repo_rejects_unsafe(self, bad):
        from src.services.github_api import _validate_repo

        with pytest.raises(ValueError, match="Invalid GitHub repo"):
            _validate_repo(bad)

    @pytest.mark.parametrize(
        "good",
        [
            "abc1234",  # 7-char abbreviated
            "abc1234abc1234abc1234abc1234abc1234abcd",  # 40-char SHA-1
            "0" * 64,  # 64-char SHA-256
            "DEADBEEFCAFE" * 4 + "abcd",  # mixed case still hex
        ],
    )
    def test_validate_sha_accepts_hex(self, good):
        from src.services.github_api import _validate_sha

        assert _validate_sha(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "abc",  # too short
            "g" * 40,  # non-hex
            "abc1234..xyz",
            "/abc1234",
            "abc1234/branch",
            "",
        ],
    )
    def test_validate_sha_rejects_unsafe(self, bad):
        from src.services.github_api import _validate_sha

        with pytest.raises(ValueError, match="Invalid git SHA"):
            _validate_sha(bad)

    def test_validate_ref_accepts_branch_and_tag_paths(self):
        from src.services.github_api import _validate_ref

        assert _validate_ref("main") == "main"
        assert _validate_ref("heads/main") == "heads/main"
        assert _validate_ref("tags/v1.0.0") == "tags/v1.0.0"
        assert _validate_ref("feature/foo-bar") == "feature/foo-bar"

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape",
            "main/../etc",
            "-flag",
            "/leading-slash",
            "trailing-slash/",
            "name with space",
            "name?query",
            "name#frag",
            "name@{0}",  # git ref reflog suffix
            "",
        ],
    )
    def test_validate_ref_rejects_unsafe(self, bad):
        from src.services.github_api import _validate_ref

        with pytest.raises(ValueError, match="Invalid git ref"):
            _validate_ref(bad)

    @pytest.mark.parametrize(
        "good",
        ["octocat", "github", "a", "a-b-c", "Org123"],
    )
    def test_validate_org_accepts_logins(self, good):
        from src.services.github_api import _validate_org

        assert _validate_org(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "-leading-dash",
            "has space",
            "has/slash",
            "has.dot",
            "has_underscore",
            "x" * 40,  # too long
            "",
        ],
    )
    def test_validate_org_rejects_unsafe(self, bad):
        from src.services.github_api import _validate_org

        with pytest.raises(ValueError, match="Invalid GitHub organization"):
            _validate_org(bad)

    def test_validators_use_quote_for_codeql_sanitizer_recognition(self):
        """Regression guard for #217.

        Each validator must call urllib.parse.quote() on its return value.
        CodeQL's py/partial-ssrf sanitizer model recognizes quote() return
        as cleansed input — without it, the data-flow path from router
        query params to httpx.AsyncClient.request(url=...) re-opens.

        A future refactor that drops quote() would close this test AND
        silently re-introduce the SSRF alert.
        """
        import ast
        import inspect

        from src.services import github_api

        for name in ("_validate_repo", "_validate_sha", "_validate_ref", "_validate_org"):
            fn = getattr(github_api, name)
            src = inspect.getsource(fn)
            tree = ast.parse(src.lstrip())
            func = tree.body[0]
            assert isinstance(func, ast.FunctionDef)

            return_stmts = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
            assert return_stmts, f"{name} must have a return statement"

            # Final return must be a call to quote() — CodeQL only sees
            # quote()'s return as cleansed, not raw f-string concatenation.
            final_return = return_stmts[-1]
            assert isinstance(final_return.value, ast.Call), (
                f"{name} final return must call quote()"
            )
            callee = final_return.value.func
            assert isinstance(callee, ast.Name) and callee.id == "quote", (
                f"{name} final return must call quote() — see #217 for why "
                f"raw value passthrough re-opens py/partial-ssrf. Got: {callee}"
            )
