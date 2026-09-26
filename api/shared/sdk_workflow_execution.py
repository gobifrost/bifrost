"""Shared business service for SDK-consumed workflow execute and scheduled cancel.

Single implementation for both entry points:

- the HTTP handlers (``api/src/routers/workflows.py::execute_workflow``,
  ``api/src/routers/workflows.py::cancel_scheduled_execution``) serving
  SDK callers (``api/bifrost/workflows.py``), reached directly or by
  workflow children over the worker-local engine socket.

Both paths share the trusted-principal input (a server-built principal plus
request-context fields — never anything built from user-supplied request
fields), workflow lookup and role checks, Solution inbound scope, org
override / ``run_as`` rules, scheduled-row insertion, every execution form
(inline code, data providers with cache-hit and transient/sync variants,
normal queue dispatch), creator/audit fields, status/result visibility, and
WebSocket publication ordering. Scheduled cancel keeps its status-guarded
UPDATE race and 404/403/409 precedence, and DB/queue transaction order is
identical to the pre-extraction handlers.

Only the two fixed SDK mutations live here: ``workflows.execute()`` and
``workflows.cancel()``. Workflow reads (list/get), execution reads,
usage-stats, validation, registration, and orphan/role management keep
their router-level logic.

Parent-side only: imports SQLAlchemy repositories and execution services.
A workflow child never imports this module (it stays DB-free and reaches
it over the engine socket).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.org_filter import resolve_target_org
from src.core.principal import UserPrincipal

if TYPE_CHECKING:
    from src.models import WorkflowExecutionRequest, WorkflowExecutionResponse

logger = logging.getLogger(__name__)


class SdkWorkflowExecutionError(Exception):
    """SDK workflow execute/cancel failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. ``detail`` may be a string or a dict,
    matching the historical handler responses exactly.
    """

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status_code = status_code
        self.detail = detail


def is_uuid_workflow_ref(identifier: str) -> bool:
    """True when the execute ref is a UUID (repository path with org/role checks)."""
    try:
        UUID(identifier)
    except ValueError:
        return False
    return True


def should_publish_request_execution_update(status: Any) -> bool:
    """The request owns initial state; the worker owns terminal fan-out."""
    from src.models.enums import ExecutionStatus

    return status in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING)


async def insert_scheduled_execution(
    *,
    db: AsyncSession,
    workflow_id: UUID,
    workflow_name: str,
    parameters: dict,
    scheduled_at: datetime,
    organization_id: UUID | None,
    executed_by: UUID,
    executed_by_name: str,
    form_id: UUID | None,
    api_key_id: UUID | None,
    is_platform_admin: bool,
) -> UUID:
    """Insert a SCHEDULED execution row.

    Skips Redis/RabbitMQ — the deferred_execution_promoter job will publish
    the row when scheduled_at matures. Shared with the form execute path.
    """
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution

    exec_id = uuid4()
    db.add(
        Execution(
            id=exec_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status=ExecutionStatus.SCHEDULED,
            parameters=parameters,
            scheduled_at=scheduled_at,
            organization_id=organization_id,
            executed_by=executed_by,
            executed_by_name=executed_by_name,
            form_id=form_id,
            api_key_id=api_key_id,
            execution_context={"is_platform_admin": is_platform_admin},
        )
    )
    await db.commit()
    return exec_id


def _server_caller_context(
    principal: UserPrincipal,
    context_solution_id: str | None,
    context_app_id: str | None,
    context_caller_solution_id: str | None,
) -> SimpleNamespace:
    """Trusted request-context shim for Solution scope derivation.

    Carries only server-validated values (auth-resolved ``?solution=`` /
    app header / engine caller claims). The service never fills these from
    user-supplied request fields.
    """
    return SimpleNamespace(
        user=principal,
        solution_id=context_solution_id,
        app_id=context_app_id,
        caller_solution_id=context_caller_solution_id,
    )


async def execute_sdk_workflow(
    session: AsyncSession,
    principal: UserPrincipal,
    request: WorkflowExecutionRequest,
    *,
    caller_org_id: UUID | None,
    context_solution_id: str | None = None,
    context_app_id: str | None = None,
    context_caller_solution_id: str | None = None,
) -> WorkflowExecutionResponse:
    """Execute a workflow, data provider, or inline script.

    Preserves the historical ``POST /api/workflows/execute`` behavior
    exactly: org-scoped lookup, Solution own-install scope with inbound
    denial (404 without shared fallback), UUID vs portable-ref role checks,
    admin-only inline code and ``org_id`` / ``run_as`` overrides, ``run_as``
    identity resolution, execution-org priority (explicit override >
    workflow org > caller org), ``delay_seconds`` normalization, scheduled
    insert, inline-code / data-provider (cache-hit, transient/sync) /
    normal dispatch forms, terminal-transient marking, and request-side
    non-terminal WebSocket fan-out ordering.

    ``principal`` and the ``context_*`` / ``caller_org_id`` values must come
    from the server request context, never from request fields.

    Raises:
        SdkWorkflowExecutionError: mapped HTTP-style failure (400/403/404/
            409/500) with the historical detail shape.
        ValueError: malformed scope/UUID inputs from the pre-dispatch phase
            (the global handler maps these to 422, as before extraction).
    """
    from src.repositories import AccessDeniedError, WorkflowRepository
    from src.services.solution_scope import (
        SolutionInboundDenied,
        derive_execution_solution_scope,
        solution_allows_global,
    )

    # Resolve org scope for workflow lookup — superusers/provider-org members
    # may pass org_id to search that org; regular users always use their own.
    # ValueError propagates for the global 422 handler (pre-dispatch, as
    # before extraction).
    lookup_org_id = resolve_target_org(
        user=principal,
        scope=request.org_id,
        default_org_id=caller_org_id,
    )

    workflow_repo = WorkflowRepository(
        session=session,
        org_id=lookup_org_id,
        user_id=principal.user_id,
        is_superuser=principal.is_superuser,
        # Embed principals carry is_external=True (OPEN-D: external-equivalent
        # for the config/knowledge/table data gates), but workflow execution
        # is the HMAC-pre-authorized app function-call channel — deliberately
        # allowlisted by EmbedScopeMiddleware and execution-scoped by jti.
        # Keep the pre-OPEN-D resolution semantics for embed sessions here.
        is_external=principal.is_external and not principal.embed,
    )

    # A Solution caller's path::fn ref carries no install id (it can't know the
    # per-install uuid5). Derive the install scope from the caller so a path ref
    # resolves to THIS install's own workflow, not a sibling install's that
    # shares the path (Codex #8 P1) nor the bare _repo/ one. solution_id (a
    # form/agent) > form_id > app_id. A bad/foreign ref yields no scope.
    # An explicitly denied/sealed target raises SolutionInboundDenied — 404
    # WITHOUT shared fallback (a denied install must never execute a loose
    # same-path workflow).
    server_ctx = _server_caller_context(
        principal, context_solution_id, context_app_id, context_caller_solution_id
    )
    try:
        solution_scope = await derive_execution_solution_scope(
            session,
            server_ctx,
            solution_id=request.solution_id,
            form_id=request.form_id,
            app_id=request.app_id,
            target_org_id=lookup_org_id,
            caller_solution_id=request.caller_solution_id,
        )
    except SolutionInboundDenied:
        raise SdkWorkflowExecutionError(
            404,
            {"message": f"Workflow '{request.workflow_id}' not found"},
        ) from None
    allow_shared_workflow = (
        solution_scope is None
        or await solution_allows_global(session, solution_scope)
    )

    # Look up workflow metadata for type checking (needed for data provider handling)
    workflow = None
    if request.workflow_id:
        workflow = await workflow_repo.resolve(
            request.workflow_id,
            solution_scope=solution_scope,
            allow_shared_fallback=allow_shared_workflow,
        )
        if not workflow:
            # A resolution miss must identify its scope inputs: a dropped or
            # wrong install scope reads as derived_solution_scope=null here
            # instead of a mystery 404 (no user/token data — every field is
            # caller-supplied or derived from it).
            raise SdkWorkflowExecutionError(
                404,
                {
                    "message": f"Workflow '{request.workflow_id}' not found",
                    "workflow_ref": request.workflow_id,
                    "context_solution_id": context_solution_id,
                    "request_solution_id": request.solution_id,
                    "request_form_id": request.form_id,
                    "request_app_id": request.app_id,
                    "derived_solution_scope": (
                        str(solution_scope) if solution_scope else None
                    ),
                },
            )

    # Authorization check
    if request.code:
        # Inline code execution requires platform admin
        if not principal.is_superuser:
            raise SdkWorkflowExecutionError(
                403,
                "Inline code execution requires platform admin access",
            )
    elif request.workflow_id:
        # UUID resolution already goes through repository.get(), including its
        # org and role access checks. Portable name/path refs use specialized
        # resolution and still need the explicit access assertion below.
        assert workflow is not None  # guaranteed by resolve() + 404 above
        if not is_uuid_workflow_ref(request.workflow_id):
            try:
                await workflow_repo.can_access(id=workflow.id)
            except AccessDeniedError:
                raise SdkWorkflowExecutionError(
                    403,
                    "Access denied to execute this workflow",
                ) from None
    else:
        raise SdkWorkflowExecutionError(
            400,
            "Either workflow_id or code must be provided",
        )

    # Validate admin-only overrides (org_id, run_as)
    if (request.org_id or request.run_as) and not principal.is_superuser:
        raise SdkWorkflowExecutionError(
            403,
            "org_id and run_as overrides require platform admin",
        )

    # Resolve run_as user if provided
    exec_user_id = str(principal.user_id)
    exec_user_name = principal.name or principal.email or "Unknown"
    exec_user_email = principal.email or ""
    exec_is_admin = principal.is_superuser

    if request.run_as:
        from src.models.orm.users import User

        run_as_result = await session.execute(
            select(User).where(User.id == UUID(request.run_as))
        )
        run_as_user = run_as_result.scalar_one_or_none()
        if not run_as_user:
            raise SdkWorkflowExecutionError(
                404,
                f"run_as user '{request.run_as}' not found",
            )
        exec_user_id = str(run_as_user.id)
        exec_user_name = run_as_user.name or run_as_user.email or "Unknown"
        exec_user_email = run_as_user.email or ""
        exec_is_admin = run_as_user.is_superuser
        logger.info(f"Impersonating user: {exec_user_id} ({exec_user_email})")

    # Determine execution org_id
    # Priority order:
    # 0. Explicit org_id override (admin only, checked above)
    # 1. Org-scoped workflow: use workflow's organization_id (enforces workflow isolation)
    # 2. Global workflow / inline code: use caller's org context (caller_org_id).
    #    Platform admins and provider-org members targeting a non-default
    #    org must pass request.org_id explicitly per call.
    if request.org_id:
        execution_org_id = UUID(request.org_id)
        logger.info(f"Using explicit org_id override: {execution_org_id}")
    elif workflow and workflow.organization_id:
        # Org-scoped workflow - execution MUST use workflow's org for data isolation
        execution_org_id = workflow.organization_id
        logger.info(f"Using workflow's organization: {execution_org_id}")
    else:
        execution_org_id = caller_org_id

    # Scheduled execution: normalize delay_seconds -> scheduled_at and insert row.
    # The deferred_execution_promoter job will publish this row when it matures.
    scheduled_at: datetime | None = request.scheduled_at
    if request.delay_seconds is not None:
        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=request.delay_seconds)

    if scheduled_at is not None:
        from src.models import WorkflowExecutionResponse
        from src.models.enums import ExecutionStatus

        # Schedule-with-code is rejected by the contract validator; workflow must exist.
        assert workflow is not None
        exec_id = await insert_scheduled_execution(
            db=session,
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            parameters=request.input_data,
            scheduled_at=scheduled_at,
            organization_id=execution_org_id,
            executed_by=UUID(exec_user_id),
            executed_by_name=exec_user_name,
            form_id=UUID(request.form_id) if request.form_id else None,
            api_key_id=None,  # API-key-triggered scheduling not supported in v1
            is_platform_admin=exec_is_admin,
        )
        return WorkflowExecutionResponse(
            execution_id=str(exec_id),
            workflow_id=str(workflow.id),
            workflow_name=workflow.name,
            status=ExecutionStatus.SCHEDULED,
            scheduled_at=scheduled_at,
        )

    # Build shared context for execution.
    #
    # Only org_id is load-bearing here: at the enqueue boundary the context is
    # reduced to scalars (org_id, user_id, is_platform_admin, ...) and stored in
    # Redis — this Organization object is NOT serialized to the worker. The
    # worker rehydrates the org (including is_provider) from org_id via
    # OrganizationRepository.get_with_cache in the workflow_execution consumer.
    # So leaving name/is_provider unset here is intentional, not a gap.
    from src.models.enums import ExecutionStatus
    from src.sdk.context import ExecutionContext as SharedContext, Organization
    from src.services.execution.service import (
        WorkflowLoadError,
        WorkflowNotFoundError,
        get_workflow_for_execution,
        run_code,
        run_workflow,
    )

    org = None
    if execution_org_id:
        org = Organization(id=str(execution_org_id), name="", is_active=True)

    logger.info(
        f"Building execution context: org_id={execution_org_id}, user={exec_user_id}, is_superuser={exec_is_admin}, scope={'GLOBAL' if not execution_org_id else str(execution_org_id)}"
    )

    shared_ctx = SharedContext(
        user_id=exec_user_id,
        name=exec_user_name,
        email=exec_user_email,
        scope=str(execution_org_id) if execution_org_id else "GLOBAL",
        organization=org,
        is_platform_admin=exec_is_admin,
        is_function_key=False,
        execution_id=str(uuid4()),
    )

    try:
        if request.code:
            # Execute inline code
            result = await run_code(
                context=shared_ctx,
                code=request.code,
                script_name=request.script_name or "inline_script",
                input_data=request.input_data,
                transient=request.transient,
            )
        elif workflow and workflow.type == "data_provider":
            # Only short-circuit on the sync/transient hot path. A non-transient
            # request (e.g. manual "Execute" from the workflows page) expects a
            # tracked execution row to navigate to — returning a synthetic
            # execution_id would 404 the history detail page.
            if request.transient and workflow.cache_ttl_seconds > 0:
                from src.core.cache import get_cached_data_provider

                cached_result = await get_cached_data_provider(
                    str(execution_org_id) if execution_org_id else None,
                    workflow.name,
                    request.input_data,
                )
                if cached_result:
                    from src.models import WorkflowExecutionResponse

                    return WorkflowExecutionResponse(
                        execution_id=shared_ctx.execution_id,
                        workflow_id=str(workflow.id),
                        workflow_name=workflow.name,
                        status=ExecutionStatus.SUCCESS,
                        result=cached_result.get("data"),
                        duration_ms=0,
                        is_transient=True,
                    )

            # Reuse one hardened dispatch snapshot for the queue boundary. It
            # includes the active-Solution gate and global-repo policy, so the
            # worker does not repeat this query after RabbitMQ delivery.
            dispatch_metadata = await get_workflow_for_execution(
                str(workflow.id),
                db=session,
            )

            # Data providers always run sync (small payloads, no UI poll flow),
            # but honor the caller's transient flag: dropdown-options pass
            # transient=True for the fast path, the manual Execute page passes
            # transient=False and expects a tracked execution row.
            result = await run_workflow(
                context=shared_ctx,
                workflow_id=str(workflow.id),
                input_data=request.input_data,
                transient=request.transient,
                sync=True,
                dispatch_metadata=dispatch_metadata,
            )
            from src.models import WorkflowExecutionResponse

            return WorkflowExecutionResponse(
                execution_id=result.execution_id,
                workflow_id=str(workflow.id),
                workflow_name=workflow.name,
                status=result.status,
                result=result.result,
                is_transient=request.transient,
            )
        elif workflow:
            # Execute workflow by ID
            dispatch_metadata = await get_workflow_for_execution(
                str(workflow.id),
                db=session,
            )
            result = await run_workflow(
                context=shared_ctx,
                workflow_id=str(workflow.id),
                input_data=request.input_data,
                form_id=request.form_id,
                transient=request.transient,
                sync=request.sync or False,
                dispatch_metadata=dispatch_metadata,
            )
        else:
            # This shouldn't happen due to earlier validation
            raise SdkWorkflowExecutionError(
                400,
                "Either workflow_id or code must be provided",
            )

        # If the result already has a terminal status (sync mode), mark as transient
        # so the frontend uses the inline result instead of waiting on WebSocket
        if result.status and result.status not in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
            result.is_transient = True

        # Publish only the immediate non-terminal state here. The worker pushes
        # a sync result before publishing its terminal execution/history events,
        # so repeating terminal fan-out in this request adds latency and emits
        # duplicate WebSocket events.
        if (
            not request.transient
            and result.execution_id
            and should_publish_request_execution_update(result.status)
        ):
            from src.core.pubsub import publish_execution_update, publish_history_update

            await publish_execution_update(
                execution_id=result.execution_id,
                status=result.status.value,
                data={
                    "result": result.result,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                },
            )
            await publish_history_update(
                execution_id=result.execution_id,
                status=result.status.value,
                executed_by=exec_user_id,
                executed_by_name=exec_user_name or exec_user_email or "Unknown",
                workflow_name=result.workflow_name or request.script_name or "inline_script",
                org_id=execution_org_id,
                started_at=result.started_at,
                completed_at=result.completed_at,
                duration_ms=result.duration_ms,
            )

        return result

    except WorkflowNotFoundError as e:
        raise SdkWorkflowExecutionError(404, str(e)) from None
    except WorkflowLoadError as e:
        raise SdkWorkflowExecutionError(500, str(e)) from None
    except ValueError as e:
        raise SdkWorkflowExecutionError(400, str(e)) from None
    except Exception as e:
        logger.error(f"Error executing workflow: {str(e)}", exc_info=True)
        raise SdkWorkflowExecutionError(
            500, f"Failed to execute workflow: {type(e).__name__}: {str(e)}"
        ) from None


async def cancel_scheduled_sdk_execution(
    session: AsyncSession,
    principal: UserPrincipal,
    execution_id: UUID,
    *,
    caller_org_id: UUID | None,
) -> dict:
    """Cancel a SCHEDULED execution via a status-guarded UPDATE.

    Preserves the historical
    ``POST /api/workflows/executions/{execution_id}/cancel`` behavior
    exactly, including the 404/403/409 precedence:

    - 404 when the row is missing,
    - 403 when a non-admin reaches across orgs or cancels another
      submitter's row,
    - 409 with the current status when the row is no longer SCHEDULED
      (the promoter or a concurrent cancel won the race).

    Raises:
        SdkWorkflowExecutionError: 404/403/409 with the historical detail.
    """
    from sqlalchemy.engine import CursorResult

    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution

    row = await session.get(Execution, execution_id)
    if row is None:
        raise SdkWorkflowExecutionError(404, "Execution not found")

    # Org-scoped access: row's org must match caller's org, unless admin.
    if (
        not principal.is_superuser
        and row.organization_id is not None
        and row.organization_id != caller_org_id
    ):
        raise SdkWorkflowExecutionError(403, "Access denied")

    # Non-admin can only cancel their own scheduled rows.
    if not principal.is_superuser and row.executed_by != principal.user_id:
        raise SdkWorkflowExecutionError(
            403, "Only the submitter or an admin may cancel"
        )

    # Status-guarded UPDATE (wins or loses atomically vs. the promoter).
    result = cast(
        CursorResult,
        await session.execute(
            update(Execution)
            .where(Execution.id == execution_id)
            .where(Execution.status == ExecutionStatus.SCHEDULED)
            .values(
                status=ExecutionStatus.CANCELLED,
                completed_at=datetime.now(timezone.utc),
            )
        ),
    )
    await session.commit()

    if result.rowcount == 0:
        # Another actor (promoter or concurrent cancel) changed status first.
        await session.refresh(row)
        raise SdkWorkflowExecutionError(
            409, f"Execution is not Scheduled (current status: {row.status.value})"
        )

    return {
        "execution_id": str(execution_id),
        "status": ExecutionStatus.CANCELLED.value,
    }


__all__ = [
    "SdkWorkflowExecutionError",
    "cancel_scheduled_sdk_execution",
    "execute_sdk_workflow",
    "insert_scheduled_execution",
    "is_uuid_workflow_ref",
    "should_publish_request_execution_update",
]
