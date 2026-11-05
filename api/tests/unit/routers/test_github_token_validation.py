"""GitHub validation failures must retain their upstream status and reason."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.models.contracts.github import ValidateTokenRequest
from src.routers import github
from src.services.github_api import GitHubAuthError, GitHubRateLimitError


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_error", [
    GitHubRateLimitError("GitHub rate limit exceeded. Resets at: 1793126584", status_code=403),
    GitHubAuthError("Invalid GitHub token", status_code=401),
    GitHubAuthError("Access forbidden. Check token permissions.", status_code=403),
])
async def test_validate_preserves_github_failure_without_saving_token(monkeypatch, upstream_error):
    monkeypatch.setattr(github.GitHubAPIClient, "list_repositories", AsyncMock(side_effect=upstream_error))
    save = AsyncMock()
    monkeypatch.setattr(github, "save_github_config", save)
    token = "synthetic-token-never-saved"
    with pytest.raises(HTTPException) as error:
        await github.validate_github_token(
            request=ValidateTokenRequest(token=token), ctx=SimpleNamespace(),
            user=SimpleNamespace(), db=SimpleNamespace(),
        )
    # GitHub auth errors must not masquerade as an expired Bifrost login.
    assert error.value.status_code == 400
    assert error.value.detail == f"GitHub token validation failed (HTTP {upstream_error.status_code}): {upstream_error.message}"
    assert token not in error.value.detail
    save.assert_not_awaited()
