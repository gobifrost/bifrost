"""
GitHub API Client

Thin wrapper around GitHub REST API for git operations.
Provides async methods for Git Data API operations (trees, blobs, commits, refs).

This replaces file-based git operations with API-only operations,
eliminating multi-container issues with local git folders.
"""

import base64
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class GitHubAPIError(Exception):
    """Exception raised when GitHub API operations fail."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body or {}

    def __str__(self) -> str:
        if self.status_code:
            return f"GitHubAPIError({self.status_code}): {self.message}"
        return f"GitHubAPIError: {self.message}"


class GitHubRateLimitError(GitHubAPIError):
    """Exception raised when GitHub API rate limit is exceeded."""

    pass


class GitHubNotFoundError(GitHubAPIError):
    """Exception raised when a GitHub resource is not found."""

    pass


class GitHubAuthError(GitHubAPIError):
    """Exception raised when GitHub authentication fails."""

    pass


# =============================================================================
# Path-segment validators
# =============================================================================
#
# User-controlled identifiers (repo, sha, ref, organization) flow into
# f-string-built URLs that hit api.github.com. Without sanitization this is
# partial SSRF: a value like "../../malicious" or "evil/repo;@victim.com"
# can redirect requests to other GitHub endpoints — or off-host entirely.
#
# We validate each kind against its GitHub-documented shape, then route
# the cleansed value through urllib.parse.quote(safe=""). CodeQL's
# py/partial-ssrf model recognizes quote() return values as cleansed input.
# Matches the urlunparse pattern used in services/embeddings/url_safety.py.

# GitHub repo: owner/name. Each segment 1-100 chars of [A-Za-z0-9._-].
# (GitHub's published limit is 39 for owners, 100 for repos; we go loose
# on owners since enterprise instances allow longer names.)
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$")

# Git SHA: 7-64 hex chars, case-insensitive (full SHA-1 is 40, SHA-256 is
# 64; GitHub also accepts 7+ as abbreviated).
_SHA_RE = re.compile(r"^[a-fA-F0-9]{7,64}$")

# Git ref: branch, tag, or "heads/<name>" / "tags/<name>". Disallow:
# starting with -, containing .., @{, control chars, spaces, ~^:?*[\
# Per git-check-ref-format(1) — simplified for paths we accept here.
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,250}$")

# GitHub org/user login: alphanumeric + dash, no leading dash, 1-39 chars.
_ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


def _validate_repo(repo: str) -> str:
    """Validate `owner/name` shape and return URL-quoted form.

    Returns urllib.parse.quote(repo, safe='/') — CodeQL recognizes the
    quote() return as cleansed input for py/partial-ssrf, closing the
    data-flow path from user input to httpx.AsyncClient.request(url=...).
    """
    if not _REPO_RE.match(repo) or ".." in repo:
        raise GitHubAPIError(
            f"Invalid GitHub repo identifier (expected 'owner/name'): {log_safe(repo)!r}"
        )
    return quote(repo, safe="/")


def _validate_sha(sha: str) -> str:
    """Validate hex SHA and return URL-quoted form."""
    if not _SHA_RE.match(sha):
        raise GitHubAPIError(f"Invalid git SHA: {log_safe(sha)!r}")
    return quote(sha, safe="")


def _validate_ref(ref: str) -> str:
    """Validate git ref shape and return URL-quoted form.

    Note: refs can legitimately contain `/` (e.g. `heads/main`,
    `tags/v1.0`), so we keep `/` unencoded.
    """
    if (
        not _REF_RE.match(ref)
        or ".." in ref
        or ref.startswith("-")
        or ref.startswith("/")
        or ref.endswith("/")
    ):
        raise GitHubAPIError(f"Invalid git ref: {log_safe(ref)!r}")
    return quote(ref, safe="/")


def _validate_org(org: str) -> str:
    """Validate GitHub org/user login shape and return URL-quoted form."""
    if not _ORG_RE.match(org):
        raise GitHubAPIError(
            f"Invalid GitHub organization/user login: {log_safe(org)!r}"
        )
    return quote(org, safe="")


def _validate_pagination_value(
    name: str,
    value: int,
    min_value: int,
    max_value: int | None = None,
) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise GitHubAPIError(f"Invalid {name}: {log_safe(value)!r}") from exc

    if normalized < min_value or (
        max_value is not None and normalized > max_value
    ):
        if max_value is None:
            expected = f">= {min_value}"
        else:
            expected = f"between {min_value} and {max_value}"
        raise GitHubAPIError(
            f"Invalid {name}: {log_safe(value)!r} (expected {expected})"
        )
    return normalized


# =============================================================================
# Pydantic Models for GitHub API Responses
# =============================================================================


class GitHubTreeEntry(BaseModel):
    """A single entry in a GitHub tree (file or directory)."""

    path: str = Field(..., description="Path relative to tree root")
    mode: str = Field(..., description="File mode (e.g., '100644' for file, '040000' for directory)")
    type: str = Field(..., description="Entry type: 'blob' for file, 'tree' for directory")
    sha: str = Field(..., description="SHA of the blob or tree")
    size: int | None = Field(default=None, description="Size in bytes (only for blobs)")
    url: str | None = Field(default=None, description="API URL for this entry")

    model_config = ConfigDict(from_attributes=True)


class GitHubTree(BaseModel):
    """GitHub tree response."""

    sha: str = Field(..., description="SHA of this tree")
    url: str = Field(..., description="API URL for this tree")
    tree: list[GitHubTreeEntry] = Field(default_factory=list, description="Tree entries")
    truncated: bool = Field(default=False, description="Whether response was truncated")

    model_config = ConfigDict(from_attributes=True)


class GitHubBlob(BaseModel):
    """GitHub blob (file content) response."""

    sha: str = Field(..., description="SHA of the blob")
    size: int = Field(..., description="Size in bytes")
    url: str = Field(..., description="API URL for this blob")
    content: str = Field(..., description="Base64-encoded content")
    encoding: str = Field(..., description="Content encoding (usually 'base64')")
    node_id: str | None = Field(default=None, description="GraphQL node ID")

    model_config = ConfigDict(from_attributes=True)


class GitHubCommitTree(BaseModel):
    """Tree reference within a commit."""

    sha: str = Field(..., description="SHA of the tree")
    url: str = Field(..., description="API URL for the tree")

    model_config = ConfigDict(from_attributes=True)


class GitHubCommitParent(BaseModel):
    """Parent commit reference."""

    sha: str = Field(..., description="SHA of the parent commit")
    url: str = Field(..., description="API URL for the parent commit")
    html_url: str | None = Field(default=None, description="HTML URL for the parent commit")

    model_config = ConfigDict(from_attributes=True)


class GitHubCommitAuthor(BaseModel):
    """Author/committer information."""

    name: str = Field(..., description="Name of the author/committer")
    email: str = Field(..., description="Email of the author/committer")
    date: str = Field(..., description="ISO 8601 timestamp")

    model_config = ConfigDict(from_attributes=True)


class GitHubCommit(BaseModel):
    """GitHub commit object response."""

    sha: str = Field(..., description="SHA of the commit")
    url: str = Field(..., description="API URL for this commit")
    html_url: str | None = Field(default=None, description="HTML URL for the commit")
    tree: GitHubCommitTree = Field(..., description="Tree this commit points to")
    parents: list[GitHubCommitParent] = Field(default_factory=list, description="Parent commits")
    author: GitHubCommitAuthor = Field(..., description="Author information")
    committer: GitHubCommitAuthor = Field(..., description="Committer information")
    message: str = Field(..., description="Commit message")

    model_config = ConfigDict(from_attributes=True)


class GitHubRefObject(BaseModel):
    """Object that a ref points to."""

    sha: str = Field(..., description="SHA of the object")
    type: str = Field(..., description="Object type (commit, tree, blob, tag)")
    url: str = Field(..., description="API URL for the object")

    model_config = ConfigDict(from_attributes=True)


class GitHubRef(BaseModel):
    """GitHub ref (branch/tag pointer) response."""

    ref: str = Field(..., description="Full ref name (e.g., 'refs/heads/main')")
    url: str = Field(..., description="API URL for this ref")
    object: GitHubRefObject = Field(..., description="Object the ref points to")
    node_id: str | None = Field(default=None, description="GraphQL node ID")

    model_config = ConfigDict(from_attributes=True)


class GitHubCreateBlobResponse(BaseModel):
    """Response from creating a blob."""

    sha: str = Field(..., description="SHA of the created blob")
    url: str = Field(..., description="API URL for the blob")

    model_config = ConfigDict(from_attributes=True)


class GitHubCreateTreeResponse(BaseModel):
    """Response from creating a tree."""

    sha: str = Field(..., description="SHA of the created tree")
    url: str = Field(..., description="API URL for the tree")
    tree: list[GitHubTreeEntry] = Field(default_factory=list, description="Tree entries")

    model_config = ConfigDict(from_attributes=True)


class GitHubCreateCommitResponse(BaseModel):
    """Response from creating a commit."""

    sha: str = Field(..., description="SHA of the created commit")
    url: str = Field(..., description="API URL for the commit")
    tree: GitHubCommitTree = Field(..., description="Tree this commit points to")
    parents: list[GitHubCommitParent] = Field(default_factory=list, description="Parent commits")
    author: GitHubCommitAuthor = Field(..., description="Author information")
    committer: GitHubCommitAuthor = Field(..., description="Committer information")
    message: str = Field(..., description="Commit message")

    model_config = ConfigDict(from_attributes=True)


class GitHubUpdateRefResponse(BaseModel):
    """Response from updating a ref."""

    ref: str = Field(..., description="Full ref name")
    url: str = Field(..., description="API URL for this ref")
    object: GitHubRefObject = Field(..., description="Object the ref now points to")

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Commits List API Models
# =============================================================================


class GitHubUser(BaseModel):
    """GitHub user information (from REST API, not Git Data API)."""

    login: str = Field(..., description="GitHub username")
    id: int = Field(..., description="GitHub user ID")
    node_id: str | None = Field(default=None, description="GraphQL node ID")
    avatar_url: str | None = Field(default=None, description="Avatar URL")
    url: str | None = Field(default=None, description="API URL for this user")
    html_url: str | None = Field(default=None, description="HTML URL for this user")
    type: str | None = Field(default=None, description="User type (User, Bot, etc.)")

    model_config = ConfigDict(from_attributes=True)


class GitHubCommitData(BaseModel):
    """Inner commit data from the commits list endpoint."""

    message: str = Field(..., description="Commit message")
    author: GitHubCommitAuthor = Field(..., description="Author information")
    committer: GitHubCommitAuthor = Field(..., description="Committer information")
    tree: GitHubCommitTree = Field(..., description="Tree this commit points to")
    url: str | None = Field(default=None, description="API URL for this commit data")
    comment_count: int | None = Field(default=None, description="Number of comments")

    model_config = ConfigDict(from_attributes=True)


class GitHubCommitListItem(BaseModel):
    """A commit item from the commits list endpoint (GET /repos/{owner}/{repo}/commits)."""

    sha: str = Field(..., description="SHA of the commit")
    url: str = Field(..., description="API URL for this commit")
    html_url: str | None = Field(default=None, description="HTML URL for the commit")
    commit: GitHubCommitData = Field(..., description="Inner commit data")
    author: GitHubUser | None = Field(default=None, description="GitHub user who authored")
    committer: GitHubUser | None = Field(default=None, description="GitHub user who committed")
    parents: list[GitHubCommitParent] = Field(default_factory=list, description="Parent commits")

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Tree Item for Creating Trees
# =============================================================================


@dataclass
class TreeItem:
    """
    Item for creating a new tree via the GitHub API.

    Use sha=None to delete a file from the tree.
    """

    path: str
    mode: str = "100644"  # Regular file
    type: str = "blob"
    sha: str | None = None  # None means delete


# =============================================================================
# GitHub API Client
# =============================================================================


class GitHubAPIClient:
    """
    Async client for GitHub REST API git operations.

    Provides methods for:
    - Reading trees (directory listings)
    - Reading/creating blobs (file contents)
    - Creating trees and commits
    - Reading/updating refs (branch pointers)
    """

    BASE_URL = "https://api.github.com"
    API_VERSION = "2022-11-28"
    DEFAULT_TIMEOUT = 30.0

    def __init__(self, token: str, timeout: float | None = None):
        """
        Initialize GitHub API client.

        Args:
            token: GitHub personal access token or installation token
            timeout: Request timeout in seconds (default: 30)
        """
        self.token = token
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: dict | None = None,
    ) -> dict:
        """
        Make an authenticated request to the GitHub API.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            endpoint: API endpoint (e.g., '/repos/owner/repo/git/trees/sha')
            json_data: Optional JSON body for POST/PATCH requests

        Returns:
            Parsed JSON response

        Raises:
            GitHubAPIError: On API errors
            GitHubRateLimitError: When rate limit is exceeded
            GitHubNotFoundError: When resource is not found
            GitHubAuthError: When authentication fails
        """
        url = f"{self.BASE_URL}{endpoint}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._headers,
                    json=json_data,
                )

                # Handle specific error codes
                if response.status_code == 401:
                    raise GitHubAuthError(
                        "GitHub authentication failed. Check your token.",
                        status_code=401,
                    )

                if response.status_code == 403:
                    # Check if it's a rate limit error
                    remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
                    if remaining == "0":
                        reset_time = response.headers.get("X-RateLimit-Reset", "unknown")
                        raise GitHubRateLimitError(
                            f"GitHub rate limit exceeded. Resets at: {reset_time}",
                            status_code=403,
                        )
                    raise GitHubAuthError(
                        "Access forbidden. Check token permissions.",
                        status_code=403,
                    )

                if response.status_code == 404:
                    raise GitHubNotFoundError(
                        f"Resource not found: {endpoint}",
                        status_code=404,
                    )

                # Raise for other HTTP errors
                response.raise_for_status()

                return response.json()

            except httpx.HTTPStatusError as e:
                # Try to parse error response
                error_body = {}
                try:
                    error_body = e.response.json()
                except ValueError as parse_err:
                    # Non-JSON error body — fall back to str(e) as message
                    logger.debug(f"GitHub error response was not JSON: {parse_err}")

                error_message = error_body.get("message", str(e))
                raise GitHubAPIError(
                    error_message,
                    status_code=e.response.status_code,
                    response_body=error_body,
                ) from e

            except httpx.TimeoutException as e:
                raise GitHubAPIError(
                    f"Request timed out after {self.timeout}s: {endpoint}"
                ) from e

            except httpx.RequestError as e:
                raise GitHubAPIError(f"Request failed: {e}") from e

    # =========================================================================
    # Tree Operations
    # =========================================================================

    async def get_tree(
        self,
        repo: str,
        sha: str,
        recursive: bool = False,
    ) -> dict[str, GitHubTreeEntry]:
        """
        Get a tree (directory listing) from GitHub.

        Args:
            repo: Repository in 'owner/repo' format
            sha: Tree SHA or branch name
            recursive: Whether to fetch tree recursively

        Returns:
            Dictionary mapping file paths to tree entries (blobs only, not directories)

        Raises:
            GitHubAPIError: On API errors
        """
        # GitHub's GET /repos/.../git/trees/{tree_sha} accepts a SHA *or*
        # a ref name (branch/tag) here; use the ref validator since its
        # char set is a superset of hex.
        endpoint = f"/repos/{_validate_repo(repo)}/git/trees/{_validate_ref(sha)}"
        if recursive:
            endpoint += "?recursive=1"

        logger.debug(f"Fetching tree: {log_safe(repo)} @ {log_safe(sha)} (recursive={recursive})")

        data = await self._request("GET", endpoint)
        tree = GitHubTree.model_validate(data)

        if tree.truncated:
            logger.warning(f"Tree response was truncated for {log_safe(repo)}@{log_safe(sha)}")

        # Return only blobs (files), not trees (directories)
        return {
            entry.path: entry for entry in tree.tree if entry.type == "blob"
        }

    async def get_commit(self, repo: str, sha: str) -> GitHubCommit:
        """
        Get a commit object.

        Args:
            repo: Repository in 'owner/repo' format
            sha: Commit SHA

        Returns:
            Commit object with tree and parent information

        Raises:
            GitHubAPIError: On API errors
        """
        endpoint = f"/repos/{_validate_repo(repo)}/git/commits/{_validate_sha(sha)}"
        logger.debug(f"Fetching commit: {log_safe(repo)} @ {log_safe(sha)}")

        data = await self._request("GET", endpoint)
        return GitHubCommit.model_validate(data)

    # =========================================================================
    # Blob Operations
    # =========================================================================

    async def get_blob_content(self, repo: str, sha: str) -> bytes:
        """
        Get blob (file) content from GitHub.

        Args:
            repo: Repository in 'owner/repo' format
            sha: Blob SHA

        Returns:
            Decoded file content as bytes

        Raises:
            GitHubAPIError: On API errors
        """
        endpoint = f"/repos/{_validate_repo(repo)}/git/blobs/{_validate_sha(sha)}"
        logger.debug(f"Fetching blob: {log_safe(repo)} @ {log_safe(sha)}")

        data = await self._request("GET", endpoint)
        blob = GitHubBlob.model_validate(data)

        if blob.encoding == "base64":
            # GitHub returns base64 with newlines, need to handle that
            content_clean = blob.content.replace("\n", "")
            return base64.b64decode(content_clean)

        # Fallback for utf-8 encoding (rare)
        return blob.content.encode("utf-8")

    async def create_blob(self, repo: str, content: bytes) -> str:
        """
        Create a new blob (file) in the repository.

        Args:
            repo: Repository in 'owner/repo' format
            content: File content as bytes

        Returns:
            SHA of the created blob

        Raises:
            GitHubAPIError: On API errors
        """
        endpoint = f"/repos/{_validate_repo(repo)}/git/blobs"
        logger.debug(f"Creating blob in {log_safe(repo)} ({len(content)} bytes)")

        data = await self._request(
            "POST",
            endpoint,
            json_data={
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            },
        )

        response = GitHubCreateBlobResponse.model_validate(data)
        return response.sha

    # =========================================================================
    # Tree Creation
    # =========================================================================

    async def create_tree(
        self,
        repo: str,
        tree_items: list[TreeItem],
        base_tree: str | None = None,
    ) -> str:
        """
        Create a new tree in the repository.

        Args:
            repo: Repository in 'owner/repo' format
            tree_items: List of tree items (files to add/modify/delete)
            base_tree: Optional base tree SHA to build upon

        Returns:
            SHA of the created tree

        Raises:
            GitHubAPIError: On API errors
        """
        endpoint = f"/repos/{_validate_repo(repo)}/git/trees"
        logger.debug(f"Creating tree in {log_safe(repo)} with {len(tree_items)} items")

        # Build tree array for API
        tree_array = []
        for item in tree_items:
            entry = {
                "path": item.path,
                "mode": item.mode,
                "type": item.type,
            }
            if item.sha is not None:
                entry["sha"] = item.sha
            else:
                # sha=None means delete - omit sha field entirely
                # and set type to blob for deletion
                pass
            tree_array.append(entry)

        request_data: dict = {"tree": tree_array}
        if base_tree:
            request_data["base_tree"] = base_tree

        data = await self._request("POST", endpoint, json_data=request_data)

        response = GitHubCreateTreeResponse.model_validate(data)
        return response.sha

    # =========================================================================
    # Commit Operations
    # =========================================================================

    async def create_commit(
        self,
        repo: str,
        message: str,
        tree: str,
        parents: list[str],
        author: dict[str, str] | None = None,
        committer: dict[str, str] | None = None,
    ) -> str:
        """
        Create a new commit.

        Args:
            repo: Repository in 'owner/repo' format
            message: Commit message
            tree: SHA of the tree for this commit
            parents: List of parent commit SHAs
            author: Optional author info (name, email, date)
            committer: Optional committer info (name, email, date)

        Returns:
            SHA of the created commit

        Raises:
            GitHubAPIError: On API errors
        """
        endpoint = f"/repos/{_validate_repo(repo)}/git/commits"
        logger.debug(f"Creating commit in {log_safe(repo)}")

        request_data: dict = {
            "message": message,
            "tree": tree,
            "parents": parents,
        }

        if author:
            request_data["author"] = author
        if committer:
            request_data["committer"] = committer

        data = await self._request("POST", endpoint, json_data=request_data)

        response = GitHubCreateCommitResponse.model_validate(data)
        logger.info(f"Created commit {response.sha[:8]} in {log_safe(repo)}")
        return response.sha

    # =========================================================================
    # Ref Operations
    # =========================================================================

    async def get_ref(self, repo: str, ref: str) -> str:
        """
        Get a ref (branch/tag pointer).

        Args:
            repo: Repository in 'owner/repo' format
            ref: Ref name (e.g., 'heads/main' for branch, 'tags/v1.0' for tag)

        Returns:
            SHA that the ref points to

        Raises:
            GitHubAPIError: On API errors
        """
        endpoint = f"/repos/{_validate_repo(repo)}/git/ref/{_validate_ref(ref)}"
        logger.debug(f"Fetching ref: {log_safe(repo)} @ {log_safe(ref)}")

        data = await self._request("GET", endpoint)
        ref_obj = GitHubRef.model_validate(data)
        return ref_obj.object.sha

    async def update_ref(
        self,
        repo: str,
        ref: str,
        sha: str,
        force: bool = False,
    ) -> None:
        """
        Update a ref to point to a new commit.

        Args:
            repo: Repository in 'owner/repo' format
            ref: Ref name (e.g., 'heads/main')
            sha: New commit SHA for the ref to point to
            force: Whether to force update (allows non-fast-forward)

        Raises:
            GitHubAPIError: On API errors
        """
        validated_repo = _validate_repo(repo)
        validated_ref = _validate_ref(ref)
        validated_sha = _validate_sha(sha)
        endpoint = f"/repos/{validated_repo}/git/refs/{validated_ref}"
        logger.debug(f"Updating ref: {log_safe(repo)} @ {log_safe(ref)} -> {validated_sha[:8]}")

        await self._request(
            "PATCH",
            endpoint,
            json_data={
                "sha": validated_sha,
                "force": force,
            },
        )

        logger.info(f"Updated ref {log_safe(ref)} to {validated_sha[:8]} in {log_safe(repo)}")

    # =========================================================================
    # High-Level Helpers
    # =========================================================================

    async def get_branch_sha(self, repo: str, branch: str) -> str:
        """
        Get the current commit SHA for a branch.

        Convenience method that wraps get_ref with proper branch prefix.

        Args:
            repo: Repository in 'owner/repo' format
            branch: Branch name (e.g., 'main')

        Returns:
            SHA of the branch's HEAD commit

        Raises:
            GitHubAPIError: On API errors
        """
        return await self.get_ref(repo, f"heads/{branch}")

    async def update_branch(
        self,
        repo: str,
        branch: str,
        sha: str,
        force: bool = False,
    ) -> None:
        """
        Update a branch to point to a new commit.

        Convenience method that wraps update_ref with proper branch prefix.

        Args:
            repo: Repository in 'owner/repo' format
            branch: Branch name (e.g., 'main')
            sha: New commit SHA
            force: Whether to force update

        Raises:
            GitHubAPIError: On API errors
        """
        await self.update_ref(repo, f"heads/{branch}", sha, force=force)

    async def list_commits(
        self,
        repo: str,
        sha: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[GitHubCommitListItem]:
        """
        List commits for a repository branch.

        Uses the REST API endpoint GET /repos/{owner}/{repo}/commits which returns
        richer commit data than the Git Data API, including GitHub user info.

        Args:
            repo: Repository in 'owner/repo' format
            sha: Branch name or commit SHA to list commits from (default: default branch)
            per_page: Number of commits per page (default: 30, max: 100)
            page: Page number for pagination (default: 1)

        Returns:
            List of commit items with full metadata

        Raises:
            GitHubAPIError: On API errors
        """
        endpoint = f"/repos/{_validate_repo(repo)}/commits"
        params = []
        validated_per_page = _validate_pagination_value("per_page", per_page, 1, 100)
        validated_page = _validate_pagination_value("page", page, 1)

        if sha:
            # `sha` here can be a branch name or a commit SHA; the ref
            # validator covers both shapes safely.
            params.append(f"sha={_validate_ref(sha)}")
        if validated_per_page != 30:
            params.append(f"per_page={validated_per_page}")
        if validated_page != 1:
            params.append(f"page={validated_page}")

        if params:
            endpoint += "?" + "&".join(params)

        logger.debug(
            f"Listing commits: {log_safe(repo)} "
            f"(sha={log_safe(sha) if sha else None}, "
            f"per_page={validated_per_page}, page={validated_page})"
        )

        data = await self._request("GET", endpoint)

        # The response is a list of commit objects
        return [GitHubCommitListItem.model_validate(item) for item in data]

    # =========================================================================
    # Repository & Branch Operations (for config/setup)
    # =========================================================================

    async def list_repositories(self, max_repos: int = 500) -> list[dict]:
        """
        List accessible GitHub repositories for the authenticated user.

        Args:
            max_repos: Maximum number of repositories to return (default: 500)

        Returns:
            List of repository dicts with name, full_name, description, url, private

        Raises:
            GitHubAPIError: On API errors
        """
        logger.debug(f"Listing repositories (max: {max_repos})")

        repos: list[dict] = []
        page = 1
        per_page = 100  # GitHub max

        while len(repos) < max_repos:
            endpoint = f"/user/repos?per_page={per_page}&page={page}&sort=updated"
            data = await self._request("GET", endpoint)

            if not data:
                break

            for repo in data:
                repos.append(
                    {
                        "name": repo["name"],
                        "full_name": repo["full_name"],
                        "description": repo.get("description"),
                        "url": repo["html_url"],
                        "private": repo["private"],
                    }
                )

                if len(repos) >= max_repos:
                    logger.warning(
                        f"Reached repository limit of {max_repos}. "
                        "Some repositories may not be shown."
                    )
                    break

            # Check if we got a full page (more might be available)
            if len(data) < per_page:
                break

            page += 1

        logger.debug(f"Found {len(repos)} repositories")
        return repos

    async def list_branches(self, repo: str) -> list[dict]:
        """
        List branches in a repository.

        Args:
            repo: Repository in 'owner/repo' format

        Returns:
            List of branch dicts with name, protected, commit_sha

        Raises:
            GitHubAPIError: On API errors
        """
        logger.debug(f"Listing branches for {log_safe(repo)}")

        endpoint = f"/repos/{_validate_repo(repo)}/branches?per_page=100"
        data = await self._request("GET", endpoint)

        branches = []
        for branch in data:
            branches.append(
                {
                    "name": branch["name"],
                    "protected": branch.get("protected", False),
                    "commit_sha": branch["commit"]["sha"],
                }
            )

        logger.debug(f"Found {len(branches)} branches in {log_safe(repo)}")
        return branches

    async def create_repository(
        self,
        name: str,
        description: str | None = None,
        private: bool = True,
        organization: str | None = None,
    ) -> dict:
        """
        Create a new GitHub repository.

        Args:
            name: Repository name
            description: Repository description
            private: Whether repository should be private (default: True)
            organization: Optional organization to create repo under

        Returns:
            Dict with full_name, url, clone_url

        Raises:
            GitHubAPIError: On API errors
        """
        logger.debug(f"Creating repository: {log_safe(name)} (org: {log_safe(organization)})")

        request_data = {
            "name": name,
            "description": description or "",
            "private": private,
        }

        if organization:
            endpoint = f"/orgs/{_validate_org(organization)}/repos"
        else:
            endpoint = "/user/repos"

        data = await self._request("POST", endpoint, json_data=request_data)

        result = {
            "full_name": data["full_name"],
            "url": data["html_url"],
            "clone_url": data["clone_url"],
        }

        logger.info(f"Created repository: {result['full_name']}")
        return result
