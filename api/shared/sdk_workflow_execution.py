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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.run_lineage import RunLineage, lineage_columns, principal_lineage
from src.core.principal import UserPrincipal
from src.services.durable_impersonation import current_durable_impersonation

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
    lineage: RunLineage | None,
    run_as: dict[str, Any] | None,
) -> UUID:
    """Insert a SCHEDULED execution row.

    Stages no dispatch — the deferred_execution_promoter job dispatches the
    row when scheduled_at matures. Shared with the form execute path.
    ``run_as`` (from ``impersonation.scheduled_run_as``) is kept when the row
    acts as another user, so the promoter decides it again when it fires.
    """
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution

    exec_id = uuid4()
    execution_context: dict[str, Any] = {"is_platform_admin": is_platform_admin}
    if run_as is not None:
        execution_context["run_as"] = run_as
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
            execution_context=execution_context,
            **lineage_columns(lineage.bound(exec_id) if lineage else None),
            **current_durable_impersonation().columns(),
        )
    )
    await db.commit()
    return exec_id


def _server_caller_context(
    principal: UserPrincipal,
    context_solution_id: str | None,
    context_app_id: str | None,
    context_caller_solution_id: str | None,
    context_dev_solution_id: str | None = None,
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
        dev_solution_id=context_dev_solution_id,
    )


async def _verified_app_embed_capability(
    session: AsyncSession,
    principal: UserPrincipal,
    request: WorkflowExecutionRequest,
) -> Any | None:
    """Load the app resource a signed embed token is bound to.

    App embeds are resource capabilities, rather than user-role sessions.  The
    current database row is authoritative: claims only identify the app and
    its original scope.  Request parent identifiers may describe a normal SDK
    caller, but cannot attest an embed parent.
    """
    if not principal.embed:
        return None
    if principal.embed_kind != "app" or principal.app_id is None:
        raise SdkWorkflowExecutionError(403, "Invalid app embed session")
    try:
        app_id = UUID(principal.app_id)
    except ValueError:
        raise SdkWorkflowExecutionError(403, "Invalid app embed session") from None

    from src.models.orm.applications import Application
    from src.services.solution_scope import get_active_solution

    app = await session.get(Application, app_id)
    if app is None:
        raise SdkWorkflowExecutionError(404, "Application not found")
    if app.organization_id != principal.organization_id:
        raise SdkWorkflowExecutionError(403, "App embed scope is invalid")

    if request.code is not None or request.run_as is not None:
        raise SdkWorkflowExecutionError(403, "App embeds may only execute workflows")

    # A signed app capability is its sole parent attestation.  A body or
    # transport parent can only repeat the signed app's own identity; it can
    # never select a form, another app, or another install.
    if request.form_id is not None:
        raise SdkWorkflowExecutionError(404, "Workflow not found")
    if request.app_id is not None and request.app_id != str(app.id):
        raise SdkWorkflowExecutionError(404, "Workflow not found")
    if (
        request.solution_id is not None
        and request.solution_id != str(app.solution_id)
    ):
        raise SdkWorkflowExecutionError(404, "Workflow not found")
    if request.caller_solution_id is not None:
        raise SdkWorkflowExecutionError(404, "Workflow not found")
    if request.org_id is not None:
        try:
            requested_org_id = UUID(request.org_id)
        except ValueError:
            raise SdkWorkflowExecutionError(404, "Workflow not found") from None
        if requested_org_id != app.organization_id:
            raise SdkWorkflowExecutionError(404, "Workflow not found")

    if app.solution_id is not None:
        solution = await get_active_solution(session, app.solution_id)
        if solution is None or solution.organization_id != app.organization_id:
            raise SdkWorkflowExecutionError(404, "Application not found")
    return app


async def execute_sdk_workflow(
    session: AsyncSession,
    principal: UserPrincipal,
    request: WorkflowExecutionRequest,
    *,
    caller_org_id: UUID | None,
    context_solution_id: str | None = None,
    context_app_id: str | None = None,
    context_caller_solution_id: str | None = None,
    context_dev_solution_id: str | None = None,
) -> WorkflowExecutionResponse:
    """Execute a workflow, data provider, or inline script.

    Preserves the historical ``POST /api/workflows/execute`` behavior
    exactly: org-scoped lookup, Solution own-install scope with inbound
    denial (404 without shared fallback), UUID vs portable-ref role checks,
    admin-only inline code and ``org_id`` override, ``run_as`` decided by
    ``impersonation.acting_target`` (it changes the acting user only), execution-org
    priority (explicit override > workflow org > caller org),
    ``delay_seconds`` normalization, scheduled
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

    app_embed = await _verified_app_embed_capability(session, principal, request)
    if app_embed is not None:
        signed_solution_id = (
            str(app_embed.solution_id) if app_embed.solution_id is not None else None
        )
        if (
            context_app_id not in (None, str(app_embed.id))
            or context_solution_id not in (None, signed_solution_id)
            or context_caller_solution_id is not None
            or context_dev_solution_id is not None
        ):
            # These transport fields are request context for ordinary SDK
            # callers.  An embed cannot use one to replace its signed parent.
            raise SdkWorkflowExecutionError(404, "Workflow not found")
    caller = None
    if app_embed is not None:
        # The signed app's persisted scope is exact, including Global.  Do
        # not inherit the request context or a synthetic embed identity.
        lookup_org_id = app_embed.organization_id
        workflow_repo = WorkflowRepository(
            session=session,
            org_id=lookup_org_id,
            user_id=None,
            is_superuser=False,
            is_external=False,
        )
    else:
        from shared.scope_resolver import ScopeNotAllowed, UNSET, resolve_effective_scope
        from src.services.authorization.enforce import load_caller

        caller = await load_caller(session, principal)
        requested_scope = UUID(request.org_id) if request.org_id else UNSET
        try:
            lookup_org_id = resolve_effective_scope(
                caller=caller, requested_scope=requested_scope
            )
        except ScopeNotAllowed as exc:
            raise SdkWorkflowExecutionError(403, str(exc)) from None

        # REST entry evaluates the original run user's workflow sharing. Children
        # already passed their parent resource gate, so retain their established
        # no-second-ACL behavior while named permissions and reach remain enforced.
        workflow_repo = WorkflowRepository(
            session=session,
            org_id=lookup_org_id,
            is_external=principal.is_external and not principal.embed,
            caller=caller,
        )

    # A Solution caller's path::fn ref carries no install id (it can't know the
    # per-install uuid5). Derive the install scope from the caller so a path ref
    # resolves to THIS install's own workflow, not a sibling install's that
    # shares the path (Codex #8 P1) nor the bare _repo/ one. solution_id (a
    # form/agent) > form_id > app_id. A bad/foreign ref yields no scope.
    # An explicitly denied/sealed target raises SolutionInboundDenied — 404
    # WITHOUT shared fallback (a denied install must never execute a loose
    # same-path workflow).
    if app_embed is not None:
        # Use only the current signed app row as the parent capability.  It is
        # not a request attestation and therefore does not take an inbound
        # cross-install path.
        solution_scope = app_embed.solution_id
    else:
        server_ctx = _server_caller_context(
            principal,
            context_solution_id,
            context_app_id,
            context_caller_solution_id,
            context_dev_solution_id,
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

    # REST workflow entry retains its resource-level access check. Inline
    # source has no workflow object; its named permissions are enforced below.
    if not request.code and request.workflow_id:
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
    elif not request.code:
        raise SdkWorkflowExecutionError(
            400,
            "Either workflow_id or code must be provided",
        )

    # The acting user: the caller, or the run_as user when the caller may act as them.
    exec_user_id = str(principal.user_id)
    exec_user_name = principal.name or principal.email or "Unknown"
    exec_user_email = principal.email or ""
    exec_is_admin = principal.is_superuser
    scheduled_run_as: dict[str, Any] | None = None

    if request.run_as:
        from src.services.authorization import impersonation

        try:
            target = await impersonation.acting_target(session, principal, UUID(request.run_as))
        except impersonation.RunAsError as exc:
            raise SdkWorkflowExecutionError(exc.status_code, exc.detail) from None
        if target is not None:
            scheduled_run_as = impersonation.scheduled_run_as(principal, target)
            if scheduled_run_as is not None:
                logger.info(f"Impersonating user: {target.user_id} ({target.email})")
            exec_user_id = str(target.user_id)
            exec_user_name = target.name or target.email or "Unknown"
            exec_user_email = target.email
            exec_is_admin = target.is_superuser

    # Who the run is for: the authenticated caller, never the run_as user.
    lineage = await principal_lineage(session, principal)
    embed_shared_ctx = None
    if app_embed is not None:
        if lineage is None:
            raise SdkWorkflowExecutionError(500, "App embed execution has no identity")
        from shared.unattended_execution import create_unattended_context

        # Embed tokens identify a capability, never an actor.  Effects and
        # scheduled audit fields therefore use the signed app scope's default
        # identity, selected by the single lineage computation above.
        embed_shared_ctx = await create_unattended_context(
            session,
            lineage=lineage,
            execution_id=str(uuid4()),
            org_id=str(lookup_org_id) if lookup_org_id else None,
            is_function_key=False,
        )
        embed_shared_ctx.solution_id = (
            str(app_embed.solution_id) if app_embed.solution_id is not None else None
        )
        embed_shared_ctx.embed = principal.verified_context or {}
        exec_user_id = embed_shared_ctx.user_id
        exec_user_name = embed_shared_ctx.name
        exec_user_email = embed_shared_ctx.email
        exec_is_admin = embed_shared_ctx.is_platform_admin

    # Determine execution org_id
    # Priority order:
    # 0. Explicit org_id override, already constrained to caller reach.
    # 1. Org-scoped workflow: use workflow's organization_id (enforces workflow isolation)
    # 2. Global workflow / inline code: use caller's org context (caller_org_id).
    #    Platform admins and provider-org members targeting a non-default
    #    org must pass request.org_id explicitly per call.
    if request.org_id:
        execution_org_id = lookup_org_id
        logger.info(f"Using explicit org_id override: {execution_org_id}")
    elif workflow and workflow.organization_id:
        # Org-scoped workflow - execution MUST use workflow's org for data isolation
        execution_org_id = workflow.organization_id
        logger.info(f"Using workflow's organization: {execution_org_id}")
    else:
        execution_org_id = lookup_org_id
    if app_embed is None:
        from src.services.authorization.enforce import (
            PermissionDenied,
            require_named_permission,
            user_reach,
        )

        assert caller is not None
        if not user_reach(caller).covers(execution_org_id):
            raise SdkWorkflowExecutionError(
                403, "Execution organization is outside the caller's organization reach"
            )
        try:
            require_named_permission(caller, "workflows.execute", execution_org_id)
            if request.code:
                require_named_permission(caller, "repository.readwrite", execution_org_id)
        except PermissionDenied as exc:
            raise SdkWorkflowExecutionError(exc.status_code, exc.detail) from None
    access_checks.note("child_run", execution_org_id)
    # The execution organization is already the workflow's, or the caller's
    # for a Global workflow or inline code.
    access_checks.note_launch(
        "workflows.execute",
        execution_org_id,
        lookup_org_id,
        subject="inline_code" if workflow is None else f"workflow:{workflow.id}",
    )

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
            lineage=lineage,
            run_as=scheduled_run_as,
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

    if embed_shared_ctx is not None:
        shared_ctx = embed_shared_ctx
    else:
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
            # All request-side authorization and context reads are complete.
            # Release the main-pool connection before dispatching
            # long-running execution work; acceptance uses the dispatch pool.
            await session.commit()
            # Execute inline code
            result = await run_code(
                context=shared_ctx,
                code=request.code,
                script_name=request.script_name or "inline_script",
                input_data=request.input_data,
                transient=request.transient,
                lineage=lineage,
            )
        elif workflow and workflow.type == "data_provider":
            # Only short-circuit on the sync/transient hot path. A non-transient
            # request (e.g. manual "Execute" from the workflows page) expects a
            # tracked execution row to navigate to — returning a synthetic
            # execution_id would 404 the history detail page.
            if request.transient and lineage is not None:
                from shared.sdk_provider_cache import ProviderCacheScopeError, build_provider_cache_scope
                from src.core.cache.data_provider_cache import get_cached_result

                try:
                    cache_scope = await build_provider_cache_scope(
                        session,
                        workflow=workflow,
                        organization_id=execution_org_id,
                        run_user_id=lineage.run_user_id,
                        acting_user_id=UUID(exec_user_id),
                    )
                except ProviderCacheScopeError:
                    # Cache reuse is optional, but only a fully provable
                    # current lineage may read it.
                    cache_scope = None
                if cache_scope is not None and cache_scope.ttl_seconds > 0:
                    cached_result = await get_cached_result(
                        None if execution_org_id is None else str(execution_org_id),
                        str(cache_scope.workflow_id),
                        cache_scope.authority_fingerprint,
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
            # executor does not repeat this query when it claims the run.
            dispatch_metadata = await get_workflow_for_execution(
                str(workflow.id),
                db=session,
            )

            # Data providers always run sync (small payloads, no UI poll flow),
            # but honor the caller's transient flag: dropdown-options pass
            # transient=True for the fast path, the manual Execute page passes
            # transient=False and expects a tracked execution row.
            # Dispatch metadata has completed the request-side read phase.
            # The sync wait below must not retain this main-pool connection.
            await session.commit()
            result = await run_workflow(
                context=shared_ctx,
                workflow_id=str(workflow.id),
                input_data=request.input_data,
                transient=request.transient,
                sync=True,
                dispatch_metadata=dispatch_metadata,
                lineage=lineage,
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
            # Dispatch metadata has completed the request-side read phase.
            # The sync wait below must not retain this main-pool connection.
            await session.commit()
            result = await run_workflow(
                context=shared_ctx,
                workflow_id=str(workflow.id),
                input_data=request.input_data,
                form_id=request.form_id,
                transient=request.transient,
                sync=request.sync or False,
                dispatch_metadata=dispatch_metadata,
                lineage=lineage,
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
    caller_org_id: UUID | None = None,
) -> dict:
    """Cancel a SCHEDULED execution via a status-guarded UPDATE.

    Preserves the historical
    ``POST /api/workflows/executions/{execution_id}/cancel`` behavior
    exactly, including the 404/403/409 precedence:

    - 404 when the row is missing,
    - 403 when the original run user cannot reach the row or neither owns it
      nor holds scoped ``executions.readwrite.all``,
    - 409 with the current status when the row is no longer SCHEDULED
      (the promoter or a concurrent cancel won the race).

    Raises:
        SdkWorkflowExecutionError: 404/403/409 with the historical detail.
    """
    del caller_org_id

    from sqlalchemy.engine import CursorResult

    from src.services.authorization.enforce import load_caller
    from src.services.authorization.execution_access import execution_cancel_allowed
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution

    row = await session.get(Execution, execution_id)
    if row is None:
        raise SdkWorkflowExecutionError(404, "Execution not found")

    if not execution_cancel_allowed(await load_caller(session, principal), row):
        raise SdkWorkflowExecutionError(403, "Access denied")

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
