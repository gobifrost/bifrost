"""
GitHub Integration Router

Git/GitHub integration for workspace sync.
Provides endpoints for connecting to repos, syncing, and configuration management.
"""

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status

from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
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
    GitHubConfigResponse,
    GitHubRepoInfo,
    GitHubReposResponse,
    GitOpRequest,
    SyncRequest,
    SyncResult,
    GitRefreshStatusResponse,
    RepoStatusResponse,
    ResolveRequest,
    ValidateTokenRequest,
)
from src.jobs.platform.git_operation import (
    GIT_OPERATION_DEFINITION,
    GitOperationPayload,
    WORKSPACE_MUTATION_RESOURCE_LOCK_KEY,
    _authenticated_clone_url,
)
from src.models.contracts.github import (
    GitConnectPreview,
    GitConnectPreviewRequest,
    GitConnectRequest,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted, PlatformJobStatus
from src.models.orm.platform_jobs import PlatformJob
from src.services.github_api import GitHubAPIClient, GitHubAPIError
from src.services.github_config import (
    delete_github_config,
    get_github_config,
    save_github_config,
)
from src.services.github_sync import (
    GitConnectDecisionError,
    GitConnectPreviewError,
    GitHubSyncService,
    resolve_connect_items,
)
from src.services.platform_jobs import (
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
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


def _normalize_connect_repository_url(repository_url: str) -> str:
    """Accept GitHub owner/repo shorthand without permitting arbitrary remotes."""
    candidate = repository_url.strip().removesuffix(".git")
    if not candidate.startswith("http"):
        candidate = f"https://github.com/{candidate}"
    prefix = "https://github.com/"
    repository = candidate.removeprefix(prefix).strip("/")
    if (
        not candidate.startswith(prefix)
        or len(repository.split("/")) != 2
        or not all(repository.split("/"))
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="repository_url must be a GitHub HTTPS repository URL",
        )
    return f"{prefix}{repository}"


async def _enqueue_git_operation(
    db: DbSession,
    *,
    operation: Literal[
        "fetch", "status", "commit", "sync", "resolve", "discard", "abort_merge", "diff", "connect"
    ],
    organization_id,
    user: CurrentSuperuser,
    job_id: uuid.UUID | None,
    options: dict | None = None,
    response: Response | None = None,
    ensure_notification: bool = False,
) -> PlatformJobAccepted:
    """Enqueue one Git operation through the shared durable job transport."""
    job, reused = await enqueue_platform_job(
        db,
        GIT_OPERATION_DEFINITION,
        GitOperationPayload(
            operation=operation,
            organization_id=organization_id,
            options=options or {},
        ),
        dedupe_key=str(job_id) if job_id else None,
        resource_lock_key=WORKSPACE_MUTATION_RESOURCE_LOCK_KEY,
        priority=500,
        organization_id=organization_id,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.email,
        resource_type="workspace",
        resource_id="git",
        title=operation.replace("_", " ").title(),
        action_url="/git",
        job_id=job_id,
    )
    if ensure_notification and job.notification_id is None:
        await ensure_platform_job_notification(db, job)
    await db.commit()
    await publish_platform_job_update(job)
    if response is not None:
        response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        status=PlatformJobStatus(job.status),
        reused=reused,
        notification_id=job.notification_id,
    )


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
    "/connect/preview",
    response_model=GitConnectPreview,
    summary="Preview first workspace Git connection",
)
async def preview_git_connect(
    body: GitConnectPreviewRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> GitConnectPreview:
    """Compare the detached workspace with a remote branch without changing either."""
    config = await get_github_config(db, ctx.org_id)
    if config is None or not config.token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub token not found. Please validate your token first.",
        )
    if config.repo_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="GitHub is already connected; disconnect it before connecting another repository.",
        )
    repository_url = _normalize_connect_repository_url(body.repository_url)
    service = GitHubSyncService(
        db,
        repo_url=_authenticated_clone_url(config, repository_url),
        branch=body.branch,
    )
    try:
        return await service.preview_connect(
            repository_url,
            body.branch,
            requested_by_user_id=str(user.user_id),
            organization_id=str(ctx.org_id) if ctx.org_id else None,
        )
    except GitConnectPreviewError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.post(
    "/connect",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue reviewed first workspace Git connection",
)
async def enqueue_git_connect(
    body: GitConnectRequest,
    response: Response,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> PlatformJobAccepted:
    """Validate a requester-bound preview, then run it through ``workspace.git``."""
    config = await get_github_config(db, ctx.org_id)
    if config is None or not config.token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub token not found. Please validate your token first.",
        )
    if config.repo_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="GitHub is already connected; disconnect it before connecting another repository.",
        )
    try:
        preview = await GitHubSyncService.load_connect_preview(
            body.preview_token,
            requested_by_user_id=str(user.user_id),
            organization_id=str(ctx.org_id) if ctx.org_id else None,
        )
        resolve_connect_items(
            preview.items, strategy=body.strategy, decisions=body.decisions
        )
        if body.strategy == "start_from_remote" and any(
            item.classification in {"local_only", "conflict"} for item in preview.items
        ) and not body.confirm_destructive:
            raise GitConnectDecisionError(
                "start_from_remote would discard local content; set confirm_destructive"
            )
    except GitConnectPreviewError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GitConnectDecisionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return await _enqueue_git_operation(
        db,
        operation="connect",
        organization_id=ctx.org_id,
        user=user,
        job_id=uuid.uuid4(),
        options={"request": body.model_dump(mode="json")},
        response=response,
        ensure_notification=True,
    )


@router.post(
    "/fetch",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue git fetch",
    description="Queue a git fetch operation. Results via WebSocket.",
)
async def git_fetch(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: GitOpRequest | None = None,
) -> PlatformJobAccepted:
    """Queue a git fetch operation."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    return await _enqueue_git_operation(
        db,
        operation="fetch",
        organization_id=ctx.org_id,
        user=user,
        job_id=request.job_id if request else None,
    )


@router.post(
    "/commit",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue git commit",
    description="Queue a git commit operation (local only, no push).",
)
async def git_commit(
    request: CommitRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> PlatformJobAccepted:
    """Queue a git commit."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    return await _enqueue_git_operation(
        db,
        operation="commit",
        organization_id=ctx.org_id,
        user=user,
        job_id=request.job_id,
        options={"message": request.message},
    )


@router.post(
    "/sync",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue sync (pull + push)",
    description="Queue a combined sync: pull remote changes, push local commits, import entities. Results via WebSocket.",
)
async def git_sync(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: SyncRequest | None = None,
) -> PlatformJobAccepted:
    """Queue a sync (pull + push + entity import)."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    job_id = request.job_id if request and request.job_id else uuid.uuid4()
    confirm_deletes = request.confirm_deletes if request else False
    retry_plan = None
    if request and request.retry_job_id:
        previous_job = await db.get(PlatformJob, request.retry_job_id)
        if (
            previous_job is None
            or previous_job.organization_id != ctx.org_id
            or previous_job.requested_by_user_id != str(user.user_id)
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Retry plan not found")
        if previous_job.job_type != "workspace.git" or previous_job.status != "failed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Retry plan is not from a failed workspace sync",
            )
        try:
            previous_result = SyncResult.model_validate(
                previous_job.result
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Retry plan is unavailable",
            ) from exc
        if not previous_result.retryable or previous_result.retry_plan is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspace sync is not retryable",
            )
        retry_plan = previous_result.retry_plan.model_dump(mode="json")
    return await _enqueue_git_operation(
        db,
        operation="sync",
        organization_id=ctx.org_id,
        user=user,
        job_id=job_id,
        options={
            "confirm_deletes": confirm_deletes,
            "retry_plan": retry_plan,
        },
    )


@router.post(
    "/abort-merge",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Abort merge",
    description="Abort an in-progress merge, returning to pre-pull state.",
)
async def git_abort_merge(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: GitOpRequest | None = None,
) -> PlatformJobAccepted:
    """Queue a merge abort."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    return await _enqueue_git_operation(
        db,
        operation="abort_merge",
        organization_id=ctx.org_id,
        user=user,
        job_id=request.job_id if request else None,
    )


@router.post(
    "/changes",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue working tree status",
    description="Queue a working tree status check.",
)
async def git_changes(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    request: GitOpRequest | None = None,
) -> PlatformJobAccepted:
    """Queue a working tree status check."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    return await _enqueue_git_operation(
        db,
        operation="status",
        organization_id=ctx.org_id,
        user=user,
        job_id=request.job_id if request else None,
    )


@router.post(
    "/resolve",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue conflict resolution",
    description="Queue conflict resolution after a failed pull.",
)
async def git_resolve(
    request: ResolveRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> PlatformJobAccepted:
    """Queue conflict resolution."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    return await _enqueue_git_operation(
        db,
        operation="resolve",
        organization_id=ctx.org_id,
        user=user,
        job_id=request.job_id,
        options={"resolutions": request.resolutions},
    )


@router.post(
    "/diff",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue file diff",
    description="Queue a file diff operation.",
)
async def git_diff(
    request: DiffRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> PlatformJobAccepted:
    """Queue a file diff."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    return await _enqueue_git_operation(
        db,
        operation="diff",
        organization_id=ctx.org_id,
        user=user,
        job_id=request.job_id,
        options={"path": request.path},
    )


@router.post(
    "/discard",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Discard working tree changes",
    description="Discard uncommitted changes for specific files (git checkout -- <path>).",
)
async def git_discard(
    request: DiscardRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> PlatformJobAccepted:
    """Queue a discard operation."""
    config = await get_github_config(db, ctx.org_id)
    if not config or not config.token or not config.repo_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not configured")

    return await _enqueue_git_operation(
        db,
        operation="discard",
        organization_id=ctx.org_id,
        user=user,
        job_id=request.job_id,
        options={"paths": request.paths},
    )
