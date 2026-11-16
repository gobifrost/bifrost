"""Durable, direct execution for workspace Git operations."""

from __future__ import annotations

from typing import Any, Literal
from contextlib import asynccontextmanager
from uuid import UUID

from pydantic import BaseModel, Field

from src.config import get_settings
from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobCancelled,
    PlatformJobFailure,
    define_platform_job,
    PlatformJobRequiresAction,
)
from src.models.contracts.github import WorkspaceSyncPlan
from src.services.github_config import get_github_config, save_github_config, lock_github_config
from shared.github_git_app import activate, credential_provider, pending_config, validate_current_connection, cleanup_setup_credentials
from src.services.github_sync import (
    GitConnectDecisionError,
    GitConnectPreviewError,
    GitConnectPreviewStale,
    GitHubSyncService,
    redact_token,
)


WORKSPACE_MUTATION_RESOURCE_LOCK_KEY = "workspace"


class GitOperationPayload(BaseModel):
    operation: Literal[
        "fetch", "status", "commit", "sync", "resolve", "discard", "abort_merge", "diff", "connect"
    ]
    organization_id: UUID | None = None
    options: dict[str, Any] = Field(default_factory=dict)


def _github_remote_url(config: Any, repository_url: str | None = None) -> str:
    """Build the credential-free GitHub remote; the token is passed to git separately."""
    repo = repository_url or config.repo_url
    if not repo:
        raise ValueError("repository URL is required")
    if repo.startswith("https://github.com/"):
        repo = repo.removeprefix("https://github.com/").removesuffix(".git")
    return f"https://github.com/{repo}.git"


async def _report(
    context: PlatformJobContext,
    phase: str,
    current: int = 0,
    total: int = 0,
) -> None:
    await context.report(phase, current=current, total=total or None)


async def dispatch_git_operation(
    service: GitHubSyncService,
    payload: GitOperationPayload,
    context: PlatformJobContext,
) -> dict[str, Any]:
    """Execute one typed operation using the shared PlatformJob transport."""
    options = payload.options
    if payload.operation == "fetch":
        result = await service.desktop_fetch(
            progress_fn=lambda phase, current=0, total=0: _report(
                context, phase, current, total
            )
        )
        if result.success:
            status = await service.desktop_status()
            return {
                **result.model_dump(mode="json"),
                "changed_files": [item.model_dump() for item in status.changed_files],
                "conflicts": [item.model_dump() for item in status.conflicts],
            }
        return result.model_dump(mode="json")
    if payload.operation == "status":
        return {"success": True, **(await service.desktop_status()).model_dump(mode="json")}
    if payload.operation == "commit":
        return (await service.desktop_commit(options.get("message", "Commit from Bifrost"))).model_dump(
            mode="json"
        )
    if payload.operation == "sync":
        retry_plan_payload = options.get("retry_plan")
        if retry_plan_payload is None and context.checkpoint:
            retry_plan_payload = context.checkpoint.get("retry_plan")
        retry_plan = (
            WorkspaceSyncPlan.model_validate(retry_plan_payload)
            if retry_plan_payload is not None
            else None
        )
        return (
            await service.desktop_sync(
                confirm_deletes=bool(options.get("confirm_deletes", False)),
                retry_plan=retry_plan,
                progress_fn=lambda phase, current=0, total=0: _report(
                    context, phase, current, total
                ),
            )
        ).model_dump(mode="json")
    if payload.operation == "resolve":
        return (await service.desktop_resolve(options.get("resolutions", {}))).model_dump(mode="json")
    if payload.operation == "abort_merge":
        return (await service.desktop_abort_merge()).model_dump(mode="json")
    if payload.operation == "diff":
        return {"success": True, **(await service.desktop_diff(options["path"])).model_dump(mode="json")}
    if payload.operation == "discard":
        return (await service.desktop_discard(options.get("paths", []))).model_dump(mode="json")
    if payload.operation == "connect":
        from src.models.contracts.github import GitConnectRequest

        request = GitConnectRequest.model_validate(options.get("request"))
        return (
            await service.desktop_connect(
                request,
                requested_by_user_id=context.requested_by_user_id,
                organization_id=str(context.organization_id) if context.organization_id else None,
                progress_fn=lambda phase, current=0, total=0: _report(
                    context, phase, current, total
                ),
            )
        ).model_dump(mode="json")
    raise PlatformJobFailure("invalid_git_operation", "Unknown Git operation.")


@asynccontextmanager
async def workspace_publication_authorization(context: PlatformJobContext):
    """Fence the short retained-state publication, not immutable staging I/O."""
    async with get_db_context() as db:
        if not await context.authorize_effect(db):
            raise PlatformJobCancelled
        yield


async def _authorize_remote(context: PlatformJobContext, config) -> None:
    async with get_db_context() as db:
        if not await context.authorize_effect(db):
            raise PlatformJobCancelled
        await validate_current_connection(db, config)


async def _cleanup_connection_receipt(context, receipt) -> None:
    from src.services.git_repo_manager import GitRepoManager
    async with workspace_publication_authorization(context):
        if receipt.get("checkpoint_id"):
            manager = GitRepoManager(get_settings())
            await manager.delete_workspace_checkpoint(receipt["checkpoint_id"])
        if receipt.get("setup_id"):
            await cleanup_setup_credentials(UUID(receipt["setup_id"]))


async def _activate_connection(context, config, connection, result) -> None:
    """Commit connection activation and its same-job receipt in one transaction."""
    async with get_db_context() as db:
        if not await context.authorize_effect(db):
            raise PlatformJobCancelled
        await lock_github_config(db, config.scope)
        if context.organization_id != config.scope:
            # A global PAT may be inherited when first connecting an org.
            # Lock its target too before checking for a newer org connection.
            await lock_github_config(db, context.organization_id)
        await validate_current_connection(db, config)
        if config.setup_id:
            await activate(db, config, connection["branch"], context.requested_by_email, commit=False)
        else:
            current = await get_github_config(db, context.organization_id)
            if current is None or current.generation != config.generation or current.repo_url:
                raise PlatformJobFailure("github_connection_changed", "The GitHub connection changed during setup.")
            if config.token is None:
                raise PlatformJobFailure("github_not_configured", "GitHub PAT credentials are missing.")
            await save_github_config(db=db, org_id=context.organization_id, token=config.token,
                repo_url=connection["repository_url"], branch=connection["branch"],
                updated_by=context.requested_by_email, commit=False)
        from src.models.orm.platform_jobs import PlatformJob
        job = await db.get(PlatformJob, context.job_id)
        previous = (job.result or {}) if job is not None else {}
        receipt = {
            "git_connection_complete": True, "job_id": str(context.job_id),
            "result": result, "setup_id": str(config.setup_id) if config.setup_id else None,
            "checkpoint_id": previous.get("checkpoint_id"),
        }
        await context.checkpoint_in_transaction(db, receipt, phase="Git connection activated")
        try:
            await db.commit()
        except Exception as exc:
            raise PlatformJobFailure("git_activation_commit_unconfirmed",
                "Git connection activation could not be confirmed; its durable receipt is retained.",
                retryable=True, preserve_checkpoint=True) from exc
    await _cleanup_connection_receipt(context, receipt)


async def _run_git_operation(
    context: PlatformJobContext,
    payload: GitOperationPayload,
) -> dict:
    await _report(context, f"Running {payload.operation.replace('_', ' ')}", total=100)
    if payload.organization_id not in (None, context.organization_id):
        raise PlatformJobFailure(
            "invalid_git_operation",
            "Git operation organization does not match its platform job.",
        )
    organization_id = context.organization_id
    if context.checkpoint and context.checkpoint.get("git_connection_complete"):
        if payload.operation != "connect" or context.checkpoint.get("job_id") != str(context.job_id):
            raise PlatformJobFailure("git_checkpoint_mismatch", "Git completion receipt belongs to another operation.")
        await _cleanup_connection_receipt(context, context.checkpoint)
        result = context.checkpoint["result"]
        if result.get("requires_action"):
            raise PlatformJobRequiresAction("Delete confirmation required", result)
        return result
    if payload.operation == "sync" and context.checkpoint and context.checkpoint.get("git_publication_complete"):
        from src.models.contracts.github import SyncResult
        from src.services.git_repo_manager import GitRepoManager
        if context.checkpoint.get("job_id") != str(context.job_id):
            raise PlatformJobFailure("git_checkpoint_mismatch", "Git completion receipt belongs to another job.")
        result = SyncResult.model_validate(context.checkpoint["result"]).model_dump(mode="json")
        manager = GitRepoManager(get_settings(), publication_authorization=lambda: workspace_publication_authorization(context))
        try:
            async with manager.authorized_publication():
                await manager.delete_workspace_checkpoint(context.checkpoint["checkpoint_id"])
        except PlatformJobCancelled:
            raise
        except Exception as exc:
            raise PlatformJobFailure("git_checkpoint_cleanup_failed", "Git publication completed but its private checkpoint cleanup is unfinished.",
                retryable=True, preserve_checkpoint=True) from exc
        await _report(context, "Git operation complete", current=100, total=100)
        return result
    async with get_db_context() as db:
        config = await get_github_config(db, organization_id)
        setup_id = payload.options.get("request", {}).get("app_setup_id") if payload.operation == "connect" else None
        if setup_id:
            # Browser setup must be live when accepted; an admitted durable job
            # may refresh its credentials throughout the normal job lifetime.
            config = await pending_config(db, UUID(setup_id), context.requested_by_user_id, organization_id, executing=True)
        elif config and config.auth_kind == "app" and "connection_generation" not in payload.options:
            raise PlatformJobFailure("github_connection_changed", "Requeue this Git operation after the App migration.")
        elif config and "connection_generation" in payload.options and (
            config.generation != payload.options["connection_generation"] or config.repo_url != payload.options.get("connection_repository")
        ):
            raise PlatformJobFailure("github_connection_changed", "GitHub connection changed after this operation was queued.")
        if config is None or not config.has_credentials:
            raise PlatformJobFailure("github_not_configured", "GitHub is not configured.")
        connect_receipt = context.checkpoint if payload.operation == "connect" and context.checkpoint and (context.checkpoint.get("git_publication_complete") or context.checkpoint.get("git_connect_ready")) else None
        import_receipt = context.checkpoint if payload.operation == "connect" and context.checkpoint and context.checkpoint.get("retry_plan") else None
        if connect_receipt:
            if connect_receipt.get("job_id") != str(context.job_id) or connect_receipt.get("operation") != "connect":
                raise PlatformJobFailure("git_checkpoint_mismatch", "Git completion receipt belongs to another operation.")
            connection = connect_receipt["connection"]
            result = connect_receipt["result"]
        elif import_receipt:
            connection = import_receipt["connection"]
            service_url = _github_remote_url(config, connection["repository_url"])
            service_branch = connection["branch"]
        elif payload.operation == "connect":
            try:
                request_data = payload.options["request"]
                preview = await GitHubSyncService.load_connect_preview(
                    request_data["preview_token"],
                    requested_by_user_id=context.requested_by_user_id,
                    organization_id=str(context.organization_id) if context.organization_id else None,
                )
            except (KeyError, TypeError, GitConnectPreviewError) as exc:
                raise PlatformJobFailure("git_connect_preview_invalid", str(exc)) from exc
            if preview.app_setup_id != (UUID(setup_id) if setup_id else None):
                raise PlatformJobFailure("git_connect_preview_invalid", "GitHub credentials do not match this connection preview.")
            if setup_id and config.repo_url != preview.repository_url:
                raise PlatformJobFailure("git_connect_preview_invalid", "The verified repository changed after preview.")
            if config.repo_url and not setup_id:
                raise PlatformJobFailure(
                    "github_already_configured",
                    "GitHub is already connected; disconnect it before first-connect reconciliation.",
                )
            service_url = _github_remote_url(config, preview.repository_url)
            service_branch = preview.branch
            connection = {"repository_url": preview.repository_url, "branch": preview.branch}
        else:
            if not config.repo_url:
                raise PlatformJobFailure("github_not_configured", "GitHub is not configured.")
            service_url = _github_remote_url(config)
            service_branch = config.branch
    if connect_receipt:
        await _activate_connection(context, config, connection, result)
        if result.get("requires_action"):
            raise PlatformJobRequiresAction("Delete confirmation required", result)
        return result

    async def token_provider():
        # No credential-bearing session or provider token reaches a worker.
        # The refresh session closes before Git's subprocess begins.
        await _authorize_remote(context, config)
        async with get_db_context() as credential_db:
            return await credential_provider(credential_db, config)()

    async with get_db_context() as db:
        service = GitHubSyncService(
            db=db,
            repo_url=service_url,
            branch=service_branch,
            settings=get_settings(),
            token=config.token,
            token_provider=token_provider,
            app_auth=config.auth_kind == "app",
            workspace_authorization=lambda: workspace_publication_authorization(context),
            job_context=context,
            job_operation=payload.operation,
            remote_authorization=lambda: _authorize_remote(context, config),
        )
        try:
            result = redact_token(
                await dispatch_git_operation(service, payload, context), config.token
            )
        except GitConnectPreviewStale as exc:
            raise PlatformJobFailure(
                "git_connect_plan_stale", redact_token(str(exc), config.token)
            ) from exc
        except GitConnectDecisionError as exc:
            raise PlatformJobFailure(
                "git_connect_decision_invalid", redact_token(str(exc), config.token)
            ) from exc

    if payload.operation == "connect" and (result.get("success") or result.get("requires_action")):
        await _activate_connection(context, config, connection, result)

    if result.get("requires_action"):
        raise PlatformJobRequiresAction("Delete confirmation required", result)
    if not result.get("success", False):
        raise PlatformJobFailure(
            "git_operation_failed",
            result.get("error") or f"{payload.operation.replace('_', ' ').title()} failed.",
            retryable=bool(result.get("retryable")),
            result=result,
            # Connect's canonical import receipt also binds its target. The
            # public SyncResult must not erase that private recovery binding.
            preserve_checkpoint=payload.operation == "connect" and result.get("retry_plan") is not None,
        )
    if payload.operation == "sync":
        from src.core.repo_dirty import clear_repo_dirty

        await clear_repo_dirty()
    await _report(context, "Git operation complete", current=100, total=100)
    return result


async def run_git_operation(context: PlatformJobContext, payload: GitOperationPayload) -> dict:
    try:
        return await _run_git_operation(context, payload)
    except PlatformJobCancelled as exc:
        # Keep any import/completion receipt from the locked canonical row,
        # including cancellation after that transaction has already committed.
        exc.preserve_checkpoint = True
        raise
    except PlatformJobRequiresAction:
        raise
    except PlatformJobFailure as exc:
        if exc.result is None:
            exc.preserve_checkpoint = True
        raise
    except Exception as exc:
        raise PlatformJobFailure("git_operation_interrupted",
            "Git operation was interrupted; its durable recovery receipt is retained.",
            retryable=True, preserve_checkpoint=True) from exc


GIT_OPERATION_DEFINITION = define_platform_job(
    "workspace.git", GitOperationPayload, run_git_operation,
    # v4 fences older credential-bearing executor handlers. Git effects now
    # run in the API; old queued schemas retain their exact admission checks.
    historical_payload_models={1: GitOperationPayload, 2: GitOperationPayload, 3: GitOperationPayload},
)
