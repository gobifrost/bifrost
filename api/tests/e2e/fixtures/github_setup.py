"""
GitHub E2E test fixtures.

Uses branch-per-run strategy:
1. Create unique branch for test run
2. Run all tests against that branch
3. Clean up branch after tests

This ensures test isolation and prevents state conflicts between runs.

Environment variables required:
- GITHUB_TEST_PAT: GitHub Personal Access Token with repo access
- GITHUB_TEST_REPO: Repository to test against (default: jackmusick/e2e-test-workspace)
"""

import logging
import os
import time
from collections.abc import Generator
from typing import Any

import pytest

logger = logging.getLogger(__name__)


def _get_github_client():
    """
    Lazily import and create GitHub client.

    This avoids import errors if PyGithub is not installed
    (tests will skip gracefully).
    """
    try:
        from github import Github, Auth

        return Github, Auth
    except ImportError:
        return None, None


@pytest.fixture(scope="session")
def github_test_config() -> dict[str, Any] | None:
    """
    Get GitHub test configuration from environment.

    Returns None if GITHUB_TEST_PAT is not set, PyGithub is not installed,
    or the token is invalid/expired.
    Tests should use require_github_config fixture to skip when None.

    Returns:
        dict with pat, repo, and base_branch, or None if not configured
    """
    pat = os.environ.get("GITHUB_TEST_PAT")
    repo = os.environ.get("GITHUB_TEST_REPO", "jackmusick/e2e-test-workspace")

    if not pat:
        logger.info(
            "GitHub E2E tests skipped: GITHUB_TEST_PAT environment variable not set"
        )
        return None

    Github, Auth = _get_github_client()
    if Github is None or Auth is None:
        logger.info(
            "GitHub E2E tests skipped: PyGithub package not installed"
        )
        return None

    # Validate the token actually works before running tests
    try:
        g = Github(auth=Auth.Token(pat))
        # Make a simple API call to validate credentials
        g.get_user().login
        logger.info("GitHub token validated successfully")
    except Exception as e:
        logger.info(
            f"GitHub E2E tests skipped: Token validation failed ({e})"
        )
        return None

    return {
        "pat": pat,
        "repo": repo,
        "base_branch": "main",
    }


@pytest.fixture(scope="function")
def require_github_config(github_test_config: dict[str, Any] | None) -> dict[str, Any]:
    """
    Skip test if GitHub is not configured.

    Use this fixture in tests that require GitHub credentials.
    It converts the None return from github_test_config into a proper skip.

    Returns:
        The github_test_config dict (guaranteed non-None)
    """
    if github_test_config is None:
        pytest.skip(
            "GitHub E2E tests require GITHUB_TEST_PAT environment variable. "
            "Set it to a GitHub PAT with repo access to run these tests."
        )
    assert github_test_config is not None
    return github_test_config


@pytest.fixture(scope="session")
def github_test_branch(
    github_test_config: dict[str, Any] | None,
) -> Generator[dict[str, Any] | None, None, None]:
    """
    Create a unique branch for this test run.

    Branch naming: e2e-test-{timestamp}

    This fixture:
    1. Creates a new branch from main
    2. Yields config dict with branch name and repo object
    3. Cleans up (deletes) the branch after all tests complete

    Args:
        github_test_config: Configuration from github_test_config fixture

    Yields:
        dict with pat, repo, branch, base_branch, and repo_obj, or None if not configured
    """
    if github_test_config is None:
        yield None
        return

    Github, Auth = _get_github_client()
    if Github is None or Auth is None:
        yield None
        return

    try:
        g = Github(auth=Auth.Token(github_test_config["pat"]))
        repo = g.get_repo(github_test_config["repo"])
    except Exception as e:
        logger.warning(f"Failed to connect to GitHub (invalid credentials?): {e}")
        yield None
        return

    # Create unique branch name with timestamp
    branch_name = f"e2e-test-{int(time.time())}"

    try:
        # Get main branch SHA
        main_ref = repo.get_git_ref(f"heads/{github_test_config['base_branch']}")
        main_sha = main_ref.object.sha

        # Create new branch from main
        repo.create_git_ref(f"refs/heads/{branch_name}", main_sha)
        logger.info(f"Created test branch: {branch_name}")

    except Exception as e:
        logger.warning(f"Failed to create test branch: {e}")
        yield None
        return

    yield {
        **github_test_config,
        "branch": branch_name,
        "repo_obj": repo,
        "github_client": g,
    }

    # Cleanup: delete the test branch
    try:
        ref = repo.get_git_ref(f"heads/{branch_name}")
        ref.delete()
        logger.info(f"Cleaned up test branch: {branch_name}")
    except Exception as e:
        logger.warning(f"Failed to cleanup test branch {branch_name}: {e}")
        # Don't fail - branch may already be deleted or cleanup isn't critical


def _wait_for_platform_job(
    e2e_client,
    headers: dict,
    job_id: str,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Wait for the reviewed first-connect PlatformJob to reach a terminal state."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = e2e_client.get(
            f"/api/platform-jobs/{job_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.5)
    raise AssertionError(f"Git connection job {job_id} did not finish")


@pytest.fixture(scope="function")
def github_configured(e2e_client, platform_admin, github_test_branch):
    """
    Configure GitHub integration for a single test.

    This fixture:
    1. Validates and saves the GitHub token
    2. Reviews the detached workspace against the repository
    3. Reconciles the reviewed connection through a PlatformJob
    4. Yields the config for test use
    5. Disconnects GitHub after the test

    Args:
        e2e_client: HTTP client fixture
        platform_admin: Authenticated platform admin fixture
        github_test_branch: Branch configuration from github_test_branch

    Yields:
        dict with GitHub config including branch name and repo object
    """
    if github_test_branch is None:
        pytest.skip("GitHub not configured (GITHUB_TEST_PAT not set)")

    config = github_test_branch

    # Step 1: Validate and save token
    response = e2e_client.post(
        "/api/github/validate",
        json={"token": config["pat"]},
        headers=platform_admin.headers,
    )
    assert response.status_code == 200, f"Token validation failed: {response.text}"

    # Step 2: Review and reconcile the first connection.
    repo_url = f"https://github.com/{config['repo']}.git"
    response = e2e_client.post(
        "/api/github/connect/preview",
        json={
            "repository_url": repo_url,
            "branch": config["branch"],
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 200, f"Repository preview failed: {response.text}"
    preview = response.json()
    decisions = {
        item["path"]: "local"
        for item in preview["items"]
        if item["classification"] == "conflict"
    }

    response = e2e_client.post(
        "/api/github/connect",
        json={
            "preview_token": preview["token"],
            "strategy": "reconcile",
            "decisions": decisions,
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 202, f"Git connection enqueue failed: {response.text}"
    job = _wait_for_platform_job(
        e2e_client,
        platform_admin.headers,
        response.json()["job_id"],
    )
    assert job["status"] == "succeeded", job
    logger.info(f"GitHub connection completed: {repo_url} @ {config['branch']}")

    yield config

    # Cleanup: Disconnect GitHub integration
    try:
        e2e_client.post(
            "/api/github/disconnect",
            headers=platform_admin.headers,
        )
        logger.info("Disconnected GitHub integration")
    except Exception as e:
        logger.warning(f"Failed to disconnect GitHub: {e}")


@pytest.fixture(scope="function")
def github_token_only(e2e_client, platform_admin, github_test_config):
    """
    Configure only the GitHub token (not the full repository).

    Use this fixture for tests that need to validate tokens or list
    repositories before configuring a specific repo.

    Args:
        e2e_client: HTTP client fixture
        platform_admin: Authenticated platform admin fixture
        github_test_config: Base configuration from github_test_config

    Yields:
        dict with GitHub config (token only, no repo configured)
    """
    if github_test_config is None:
        pytest.skip("GitHub not configured (GITHUB_TEST_PAT not set)")

    config = github_test_config

    # Validate and save token only
    response = e2e_client.post(
        "/api/github/validate",
        json={"token": config["pat"]},
        headers=platform_admin.headers,
    )
    assert response.status_code == 200, f"Token validation failed: {response.text}"

    yield config

    # Cleanup: Disconnect to clear token
    try:
        e2e_client.post(
            "/api/github/disconnect",
            headers=platform_admin.headers,
        )
    except Exception:
        pass  # Ignore cleanup failures


@pytest.fixture(scope="function")
def create_remote_file(github_test_branch):
    """
    Factory fixture to create files directly on GitHub.

    This is useful for testing pull operations - create a file on GitHub,
    then verify it can be pulled to the local workspace.

    Usage:
        def test_pull(create_remote_file):
            file_info = create_remote_file("test.txt", "content", "commit message")
            # Now pull and verify...

    Skips test if GitHub is not configured.

    Returns:
        Factory function that creates files on GitHub
    """
    if github_test_branch is None:
        pytest.skip("GitHub not configured (GITHUB_TEST_PAT not set)")

    config = github_test_branch
    repo = config["repo_obj"]
    branch = config["branch"]
    created_files: list[str] = []

    def _create_file(
        path: str,
        content: str,
        message: str = "E2E test file",
    ) -> dict[str, Any]:
        """
        Create a file on GitHub.

        Args:
            path: File path in repository
            content: File content
            message: Commit message

        Returns:
            dict with path, sha, and commit info
        """
        result = repo.create_file(
            path=path,
            message=message,
            content=content,
            branch=branch,
        )
        created_files.append(path)
        logger.info(f"Created remote file: {path}")
        return {
            "path": path,
            "sha": result["content"].sha,
            "commit_sha": result["commit"].sha,
        }

    yield _create_file

    # Cleanup: delete created files
    for file_path in created_files:
        try:
            contents = repo.get_contents(file_path, ref=branch)
            if not isinstance(contents, list):
                repo.delete_file(
                    path=file_path,
                    message=f"E2E cleanup: {file_path}",
                    sha=contents.sha,
                    branch=branch,
                )
                logger.info(f"Cleaned up remote file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup remote file {file_path}: {e}")


@pytest.fixture(scope="function")
def update_remote_file(github_test_branch):
    """
    Factory fixture to update existing files on GitHub.

    This is useful for testing conflict scenarios - update a file on GitHub
    while it's also modified locally.

    Usage:
        def test_conflict(update_remote_file):
            update_remote_file("existing.txt", "new content", "Update for conflict")
            # Now try to push local changes and verify conflict...

    Skips test if GitHub is not configured.

    Returns:
        Factory function that updates files on GitHub
    """
    if github_test_branch is None:
        pytest.skip("GitHub not configured (GITHUB_TEST_PAT not set)")

    config = github_test_branch
    repo = config["repo_obj"]
    branch = config["branch"]

    def _update_file(
        path: str,
        new_content: str,
        message: str = "E2E test update",
    ) -> dict[str, Any]:
        """
        Update an existing file on GitHub.

        Args:
            path: File path in repository
            new_content: New file content
            message: Commit message

        Returns:
            dict with path, sha, and commit info
        """
        # Get current file to get its SHA
        contents = repo.get_contents(path, ref=branch)
        if isinstance(contents, list):
            raise ValueError(f"Path {path} is a directory, not a file")

        result = repo.update_file(
            path=path,
            message=message,
            content=new_content,
            sha=contents.sha,
            branch=branch,
        )
        logger.info(f"Updated remote file: {path}")
        return {
            "path": path,
            "sha": result["content"].sha,
            "commit_sha": result["commit"].sha,
        }

    return _update_file


@pytest.fixture(scope="function")
def get_github_file_content(github_test_branch):
    """
    Factory fixture to fetch file content directly from GitHub.

    This is useful for verifying that pushed files have the correct content
    (portable refs, _export metadata, etc.). Virtual entity files don't exist
    in file_index, so we can't use the files API to verify them.

    Usage:
        def test_portable_refs(get_github_file_content, ...):
            content = get_github_file_content("apps/uuid.app.json")
            assert content["_export"]["workflow_refs"] is not None

    Skips test if GitHub is not configured.

    Returns:
        Factory function that fetches and parses YAML/JSON files from GitHub
    """
    if github_test_branch is None:
        pytest.skip("GitHub not configured (GITHUB_TEST_PAT not set)")

    config = github_test_branch
    pat = config["pat"]
    repo = config["repo"]
    branch = config["branch"]

    def _get_content(path: str) -> dict[str, Any] | None:
        """
        Fetch file content from GitHub and return parsed JSON.

        Args:
            path: File path in the repository (e.g., "apps/uuid.app.json")

        Returns:
            Parsed JSON content as a dict, or None if file not found

        Raises:
            httpx.HTTPStatusError: For non-404 HTTP errors
            json.JSONDecodeError: If content is not valid JSON
        """
        import base64
        import json

        import httpx
        import yaml

        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"ref": branch}

        logger.debug(f"Fetching GitHub file: {path} from {repo}@{branch}")

        response = httpx.get(url, headers=headers, params=params)

        if response.status_code == 404:
            logger.debug(f"GitHub file not found: {path}")
            return None

        response.raise_for_status()

        data = response.json()
        content_b64 = data.get("content", "")
        # GitHub returns base64 with newlines, so we need to handle that
        content_bytes = base64.b64decode(content_b64)
        content_str = content_bytes.decode("utf-8")

        # Parse as YAML (handles both YAML and JSON since JSON is valid YAML)
        if path.endswith(".yaml") or path.endswith(".yml"):
            parsed = yaml.safe_load(content_str)
        else:
            parsed = json.loads(content_str)

        logger.debug(f"Successfully fetched GitHub file: {path}")
        return parsed

    return _get_content
