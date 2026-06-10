"""
GitHub Integration Router

Git/GitHub integration for workspace sync.
Provides endpoints for connecting to repos, syncing, and configuration management.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, status

from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.core.pubsub import publish_git_operation
from src.models import (
    CommitHistoryResponse,
    CommitInfo,
    CommitRequest,
    CreateRepoRequest,
    CreateRepoResponse,
    DiffRequest,
    DiscardRequest,
    GitHubBranchesResponse,
    GitHubBranchInfo,
    GitHubConfigRequest,
    GitHubConfigResponse,
    GitHubRepoInfo,
    GitHubReposResponse,
    GitHubSetupResponse,
    GitJobResponse,
    GitOpRequest,
    SyncRequest,
    GitRefreshStatusResponse,
    RepoStatusResponse,
    ResolveRequest,
    ValidateTokenRequest,
)
from src.services.github_api import GitHubAPIClient, GitHubAPIError
from src.services.github_config import (
    delete_github_config,
    get_github_config,
    save_github_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github", tags=["GitHub"])


# =============================================================================
# Helper Functions
# =============================================================================


def _extract_repo_from_url(repo_url: str) -> str:
    """Extract owner/repo from GitHub URL."""
    if repo_url.startswith("https://github.com/"):
        return repo_url.replace("https://github.com/", "").rstrip(".git")
    return repo_url


# =============================================================================
# GitHub Configuration Endpoints
# =============================================================================


@router.get(
    "/config",
    response_model=GitHubConfigResponse,
    summary="Get GitHub configuration",
    description="Retrieve current GitHub integration configuration",
)
async def get_config_endpoint(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitHubConfigResponse:
    """Get current GitHub configuration."""
    try:
        config = await get_github_config(db, ctx.org_id)

        if not config:
            return GitHubConfigResponse(
                configured=False,
                token_saved=False,
                repo_url=None,
                branch=None,
                backup_path=None,
            )

        return GitHubConfigResponse(
            configured=bool(config.repo_url),
            token_saved=bool(config.token),
            repo_url=config.repo_url,
            branch=config.branch,
            backup_path=None,
        )

    except Exception as e:
        logger.error(f"Failed to get GitHub config: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get GitHub configuration",
        )


@router.get(
    "/status",
    response_model=GitRefreshStatusResponse,
    summary="Get GitHub sync status",
    description="Get current GitHub repository connection and sync status",
)
async def get_github_status(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitRefreshStatusResponse:
    """
    Get current GitHub status for the Source Control panel.

    Returns basic status information about GitHub configuration.
    For detailed sync preview (files to pull/push), use GET /api/github/sync.
    """
    try:
        config = await get_github_config(db, ctx.org_id)

        if not config or not config.token:
            # Not configured
            return GitRefreshStatusResponse(
                success=True,
                initialized=False,
                configured=False,
                current_branch=None,
                changed_files=[],
                conflicts=[],
                merging=False,
                commits_ahead=0,
                commits_behind=0,
                commit_history=[],
                last_synced=None,
                error=None,
            )

        if not config.repo_url:
            # Token saved but repo not configured
            return GitRefreshStatusResponse(
                success=True,
                initialized=False,
                configured=False,
                current_branch=None,
                changed_files=[],
                conflicts=[],
                merging=False,
                commits_ahead=0,
                commits_behind=0,
                commit_history=[],
                last_synced=None,
                error=None,
            )

        # Fully configured
        return GitRefreshStatusResponse(
            success=True,
            initialized=True,
            configured=True,
            current_branch=config.branch,
            changed_files=[],
            conflicts=[],
            merging=False,
            commits_ahead=0,
            commits_behind=0,
            commit_history=[],
            last_synced=config.last_synced_at,
            error=None,
        )

    except Exception as e:
        logger.error(f"Failed to get GitHub status: {e}", exc_info=True)
        return GitRefreshStatusResponse(
            success=False,
            initialized=False,
            configured=False,
            current_branch=None,
            changed_files=[],
            conflicts=[],
            merging=False,
            commits_ahead=0,
            commits_behind=0,
            commit_history=[],
            last_synced=None,
            error=str(e),
        )


@router.get(
    "/repo-status",
    response_model=RepoStatusResponse,
    summary="Fast repo status for CLI push pre-check",
    description="Check if platform has uncommitted changes and if git is configured",
)
async def get_repo_status(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> RepoStatusResponse:
    """Fast repo status check used by CLI push to gate on dirty state."""
    from src.core.repo_dirty import get_repo_dirty_since

    config = await get_github_config(db, ctx.org_id)
    dirty_since = await get_repo_dirty_since()
    return RepoStatusResponse(
        git_configured=config is not None and bool(config.repo_url),
        dirty=dirty_since is not None,
        dirty_since=dirty_since,
    )


@router.post(
    "/validate",
    response_model=GitHubReposResponse,
    summary="Validate GitHub token",
    description="Validate GitHub token and save to database, returns accessible repositories",
)
async def validate_github_token(
    request: ValidateTokenRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitHubReposResponse:
    """Validate GitHub token, save to database, and list repositories."""
    try:
        if not request.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub token required",
            )

        logger.info("Validating GitHub token")

        # Test the token by listing repositories
        client = GitHubAPIClient(request.token)
        repo_list = await client.list_repositories()

        # Convert to GitHubRepoInfo models
        repositories = [
            GitHubRepoInfo(
                name=r["name"],
                full_name=r["full_name"],
                description=r["description"],
                url=r["url"],
                private=r["private"],
            )
            for r in repo_list
        ]

        # Save token to database (repo_url=None indicates not configured yet)
        await save_github_config(
            db=db,
            org_id=ctx.org_id,
            token=request.token,
            repo_url=None,
            branch="main",
            updated_by=user.email,
        )

        logger.info("GitHub token validated and saved successfully")

        return GitHubReposResponse(
            repositories=repositories,
            detected_repo=None,
        )

    except GitHubAPIError as e:
        logger.error(f"GitHub API error validating token: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid GitHub token: {e.message}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to validate GitHub token: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to validate GitHub token: {str(e)}",
        )


@router.post(
    "/configure",
    response_model=GitHubSetupResponse,
    summary="Configure GitHub integration",
    description="Save GitHub repository configuration. Syncing happens via /sync endpoints.",
)
async def configure_github(
    request: GitHubConfigRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitHubSetupResponse:
    """
    Configure GitHub integration.

    Saves the GitHub repository configuration (repo URL, branch) to the database.
    Use the /sync endpoints to pull/push changes.
    """
    try:
        # Normalize repo_url - accept both full URL and owner/repo format
        repo_url = request.repo_url.strip()
        if not repo_url.startswith("http"):
            repo_url = f"https://github.com/{repo_url}"

        logger.info(f"Configuring GitHub for repo: {log_safe(repo_url)}")

        # Get existing config to retrieve token
        existing_config = await get_github_config(db, ctx.org_id)

        if not existing_config or not existing_config.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub token not found. Please validate your token first.",
            )

        # Save the updated configuration
        await save_github_config(
            db=db,
            org_id=ctx.org_id,
            token=existing_config.token,
            repo_url=repo_url,
            branch=request.branch or "main",
            updated_by=user.email,
        )

        logger.info(f"GitHub configuration saved for repo: {log_safe(repo_url)}")

        return GitHubSetupResponse(
            job_id=None,
            notification_id=None,
            status="configured",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to configure GitHub: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to configure GitHub: {str(e)}",
        )


@router.get(
    "/repositories",
    response_model=GitHubReposResponse,
    summary="List GitHub repositories",
    description="List accessible repositories using the saved GitHub token",
)
async def list_github_repos(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitHubReposResponse:
    """List user's GitHub repositories using saved token."""
    try:
        config = await get_github_config(db, ctx.org_id)

        if not config or not config.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub token not found. Please validate your token first.",
            )

        client = GitHubAPIClient(config.token)
        repo_list = await client.list_repositories()

        repositories = [
            GitHubRepoInfo(
                name=r["name"],
                full_name=r["full_name"],
                description=r["description"],
                url=r["url"],
                private=r["private"],
            )
            for r in repo_list
        ]

        return GitHubReposResponse(repositories=repositories, detected_repo=None)

    except GitHubAPIError as e:
        logger.error(f"GitHub API error listing repositories: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub API error: {e.message}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list repositories: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list repositories",
        )


@router.get(
    "/branches",
    response_model=GitHubBranchesResponse,
    summary="List repository branches",
    description="List branches in a GitHub repository using saved token",
)
async def list_github_branches(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    repo: str = Query(..., description="Repository full name (owner/repo)"),
) -> GitHubBranchesResponse:
    """List branches in a repository using saved token."""
    try:
        if not repo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Repository name required",
            )

        config = await get_github_config(db, ctx.org_id)

        if not config or not config.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub token not found. Please validate your token first.",
            )

        client = GitHubAPIClient(config.token)
        branch_list = await client.list_branches(repo)

        branches = [
            GitHubBranchInfo(
                name=b["name"],
                protected=b["protected"],
                commit_sha=b["commit_sha"],
            )
            for b in branch_list
        ]

        return GitHubBranchesResponse(branches=branches)

    except GitHubAPIError as e:
        logger.error(f"GitHub API error listing branches: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub API error: {e.message}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list branches: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list branches",
        )


@router.post(
    "/create-repository",
    response_model=CreateRepoResponse,
    summary="Create GitHub repository",
    description="Create a new GitHub repository using saved token",
)
async def create_github_repository(
    request: CreateRepoRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> CreateRepoResponse:
    """Create new GitHub repository."""
    try:
        config = await get_github_config(db, ctx.org_id)

        if not config or not config.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub token not found. Please validate your token first.",
            )

        client = GitHubAPIClient(config.token)
        result = await client.create_repository(
            name=request.name,
            description=request.description,
            private=request.private,
            organization=request.organization,
        )

        return CreateRepoResponse(**result)

    except GitHubAPIError as e:
        logger.error(f"GitHub API error creating repository: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create repository: {e.message}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create repository: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create repository",
        )


@router.post(
    "/disconnect",
    summary="Disconnect GitHub integration",
    description="Remove GitHub integration configuration",
)
async def disconnect_github(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> dict:
    """Disconnect GitHub integration."""
    try:
        await delete_github_config(db, ctx.org_id)

        logger.info("GitHub integration disconnected")

        return {"success": True, "message": "GitHub integration disconnected"}

    except Exception as e:
        logger.error(f"Failed to disconnect GitHub: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disconnect GitHub",
        )


# =============================================================================
# Commit History Endpoint
# =============================================================================


@router.get(
    "/commits",
    response_model=CommitHistoryResponse,
    summary="Get commit history",
    description="Get commit history with pagination",
)
async def get_commits(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    limit: int = Query(20, description="Number of commits to return"),
    offset: int = Query(0, description="Offset for pagination"),
) -> CommitHistoryResponse:
    """
    Get commit history with pagination support.

    Uses GitHub API directly to fetch commits from the configured repository.
    """
    try:
        config = await get_github_config(db, ctx.org_id)

        if not config or not config.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub token not found. Please validate your token first.",
            )

        if not config.repo_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub repository not configured.",
            )

        repo = _extract_repo_from_url(config.repo_url)
        client = GitHubAPIClient(config.token)

        # Calculate pagination - GitHub uses page-based, we expose offset-based
        page = (offset // limit) + 1 if limit > 0 else 1
        per_page = min(limit, 100)  # GitHub max is 100

        github_commits = await client.list_commits(
            repo=repo,
            sha=config.branch,
            per_page=per_page,
            page=page,
        )

        # Map GitHub API response to our CommitInfo model
        commits = [
            CommitInfo(
                sha=c.sha,
                message=c.commit.message.split("\n")[0],  # First line only
                author=c.commit.author.name,
                timestamp=c.commit.author.date,
                is_pushed=True,
            )
            for c in github_commits
        ]

        has_more = len(github_commits) == per_page

        return CommitHistoryResponse(
            commits=commits,
            total_commits=offset + len(commits) + (1 if has_more else 0),
            has_more=has_more,
        )

    except GitHubAPIError as e:
        logger.error(f"GitHub API error getting commits: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub API error: {e.message}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting commits: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get commit history",
        )


# =============================================================================
# Desktop-Style Git Operations
# =============================================================================


@router.post(
    "/fetch",
    response_model=GitJobResponse,
    summary="Queue git fetch",
    description="Queue a git fetch operation. Results via WebSocket.",
)
async def git_fetch(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: GitOpRequest | None = None,
) -> GitJobResponse:
    """Queue a git fetch operation."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = (request.job_id if request and request.job_id else None) or str(uuid.uuid4())
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_fetch",
    )
    return GitJobResponse(job_id=job_id)


@router.post(
    "/commit",
    response_model=GitJobResponse,
    summary="Queue git commit",
    description="Queue a git commit operation (local only, no push).",
)
async def git_commit(
    request: CommitRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitJobResponse:
    """Queue a git commit."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = request.job_id or str(uuid.uuid4())
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_commit",
        message=request.message,
    )
    return GitJobResponse(job_id=job_id)


@router.post(
    "/sync",
    response_model=GitJobResponse,
    summary="Queue sync (pull + push)",
    description="Queue a combined sync: pull remote changes, push local commits, import entities. Results via WebSocket.",
)
async def git_sync(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: SyncRequest | None = None,
) -> GitJobResponse:
    """Queue a sync (pull + push + entity import)."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = (request.job_id if request and request.job_id else None) or str(uuid.uuid4())
    confirm_deletes = request.confirm_deletes if request else False
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_sync",
        confirm_deletes=confirm_deletes,
    )
    return GitJobResponse(job_id=job_id)


@router.post(
    "/abort-merge",
    response_model=GitJobResponse,
    summary="Abort merge",
    description="Abort an in-progress merge, returning to pre-pull state.",
)
async def git_abort_merge(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: GitOpRequest | None = None,
) -> GitJobResponse:
    """Queue a merge abort."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = (request.job_id if request and request.job_id else None) or str(uuid.uuid4())
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_abort_merge",
    )
    return GitJobResponse(job_id=job_id)


@router.post(
    "/changes",
    response_model=GitJobResponse,
    summary="Queue working tree status",
    description="Queue a working tree status check.",
)
async def git_changes(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: GitOpRequest | None = None,
) -> GitJobResponse:
    """Queue a working tree status check."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = (request.job_id if request and request.job_id else None) or str(uuid.uuid4())
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_status",
    )
    return GitJobResponse(job_id=job_id)


@router.post(
    "/resolve",
    response_model=GitJobResponse,
    summary="Queue conflict resolution",
    description="Queue conflict resolution after a failed pull.",
)
async def git_resolve(
    request: ResolveRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitJobResponse:
    """Queue conflict resolution."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = request.job_id or str(uuid.uuid4())
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_resolve",
        resolutions=request.resolutions,
    )
    return GitJobResponse(job_id=job_id)


@router.post(
    "/diff",
    response_model=GitJobResponse,
    summary="Queue file diff",
    description="Queue a file diff operation.",
)
async def git_diff(
    request: DiffRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitJobResponse:
    """Queue a file diff."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = request.job_id or str(uuid.uuid4())
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_diff",
        path=request.path,
    )
    return GitJobResponse(job_id=job_id)


@router.post(
    "/discard",
    response_model=GitJobResponse,
    summary="Discard working tree changes",
    description="Discard uncommitted changes for specific files (git checkout -- <path>).",
)
async def git_discard(
    request: DiscardRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitJobResponse:
    """Queue a discard operation."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = request.job_id or str(uuid.uuid4())
    await publish_git_operation(
        job_id=job_id,
        org_id=str(ctx.org_id) if ctx.org_id else "",
        user_id=str(user.user_id),
        user_email=user.email,
        op_type="git_discard",
        paths=request.paths,
    )
    return GitJobResponse(job_id=job_id)



