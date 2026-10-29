"""Shared business service for SDK-consumed workflow/execution reads.

Single implementation for both entry points:

- the HTTP handlers (``api/src/routers/workflows.py::list_workflows``,
  ``api/src/routers/executions.py::list_executions`` /
  ``get_execution``) serving SDK callers (``api/bifrost/workflows.py``,
  ``api/bifrost/executions.py``), and
- the same handlers reached by workflow children over the worker-local
  engine socket.

Both paths share scope resolution input (an explicit trusted principal),
workflow filters, used-by counts,
execution history keyset and legacy cursor behavior, the pending
execution fallback, status mapping, and response shape — so HTTP and
worker-local results are identical by construction.

Only the three fixed SDK reads live here: ``workflows.list()``,
``executions.list()``, and ``executions.get()`` (which also serves
``workflows.get()``). Execution writes (execute, cancel, cleanup),
workflow mutations, usage-stats, logs listing, result/variables
endpoints, and validation keep their router-level logic.

Parent-side only: imports SQLAlchemy repositories. A workflow child never
imports this module (it stays DB-free and reaches it over the engine
socket).
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from src.core.log_safety import log_safe
from src.core.principal import UserPrincipal
from src.services.authorization.enforce import (
    allows_permission,
    load_caller,
    user_reach,
)
from src.services.authorization.execution_access import (
    current_execution_read_access,
    execution_list_filter,
)

if TYPE_CHECKING:
    from src.models.contracts.workflow_permissions import WorkflowPermissionMode
    from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest

logger = logging.getLogger(__name__)


class SdkExecutionReadError(Exception):
    """SDK workflow/execution read failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Missing entities are 404, denied
    access is 403, and malformed scope or status is 422.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# =============================================================================
# History pagination cursor
# =============================================================================
#
# History pagination is keyset-based: the continuation token names the last
# row the client saw — (timeline_at, id) — and the next page is "rows strictly
# older than that". It is shared so HTTP and local execution reads mint and
# consume identical continuation tokens.


def encode_history_cursor(timeline_at: datetime | None, row_id: UUID) -> str:
    """Encode the last-served row's position as an opaque token."""
    payload = {
        "v": 1,
        "s": timeline_at.isoformat() if timeline_at else None,
        "i": str(row_id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_history_cursor(token: str) -> tuple[datetime | None, UUID] | None:
    """Decode a keyset cursor; None for legacy offsets or malformed tokens."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        raw_started = payload.get("s")
        started_at = datetime.fromisoformat(raw_started) if raw_started else None
        return started_at, UUID(payload["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        # Not a keyset cursor (legacy numeric offset, or garbage) — the caller
        # falls back to legacy offset parsing / first page.
        return None


# =============================================================================
# Workflow list helpers (moved from api/src/routers/workflows.py)
# =============================================================================


def convert_workflow_orm_to_schema(
    workflow,
    default_permission_mode: WorkflowPermissionMode,
    used_by_count: int = 0,
    role_ids: list[UUID] | None = None,
    permission_request: SolutionWorkflowPermissionRequest | None = None,
):
    """Convert ORM model to Pydantic schema for API response.

    ``default_permission_mode`` is the platform default a workflow with no
    mode of its own takes (``get_default_workflow_permission_mode``).
    """
    from typing import Literal

    from src.models import WorkflowMetadata, WorkflowParameter
    from src.models.contracts.workflows import ExecutableType
    from src.services.workflow_permissions import resolve_workflow_permission_mode
    from src.services.workflow_validation import _extract_relative_path

    parameters = []
    for param in workflow.parameters_schema or []:
        if isinstance(param, dict):
            parameters.append(WorkflowParameter(**param))

    raw_mode = workflow.execution_mode or "sync"
    execution_mode: Literal["sync", "async"] = "async" if raw_mode == "async" else "sync"

    workflow_type = ExecutableType(workflow.type or "workflow")

    return WorkflowMetadata(
        id=str(workflow.id),
        name=workflow.name,
        function_name=workflow.function_name,
        display_name=workflow.display_name,
        description=workflow.description if workflow.description else None,
        category=workflow.category or "General",
        tags=workflow.tags or [],
        type=workflow_type,
        organization_id=str(workflow.organization_id) if workflow.organization_id else None,
        is_solution_managed=workflow.solution_id is not None,
        solution_id=workflow.solution_id,
        run_identity_id=workflow.run_identity_id,
        permission_mode=resolve_workflow_permission_mode(workflow, default_permission_mode, permission_request),
        access_level=workflow.access_level or "role_based",
        role_ids=[str(role_id) for role_id in (role_ids or [])],
        parameters=parameters,
        execution_mode=execution_mode,
        timeout_seconds=workflow.timeout_seconds if workflow.timeout_seconds is not None else 1800,
        memory_request_mib=workflow.memory_request_mib,
        learned_memory_bytes=workflow.learned_memory_bytes,
        retry_policy=None,
        endpoint_enabled=workflow.endpoint_enabled or False,
        allowed_methods=workflow.allowed_methods or ["POST"],
        disable_global_key=workflow.disable_global_key or False,
        public_endpoint=workflow.public_endpoint or False,
        is_tool=workflow.type == "tool",
        tool_description=workflow.tool_description,
        # NOT `or 300` — 0 means "never cache" and `or` would clobber it.
        cache_ttl_seconds=(
            workflow.cache_ttl_seconds if workflow.cache_ttl_seconds is not None else 300
        ),
        time_saved=workflow.time_saved or 0,
        value=float(workflow.value or 0.0),
        used_by_count=used_by_count,
        source_file_path=workflow.path,
        relative_file_path=_extract_relative_path(workflow.path),
        created_at=workflow.created_at,
    )


async def get_workflow_role_ids(
    session: AsyncSession, workflow_ids: list[UUID]
) -> dict[UUID, list[UUID]]:
    """Return assigned role IDs keyed by workflow ID for a workflow batch."""
    from src.models.orm.workflow_roles import WorkflowRole

    if not workflow_ids:
        return {}

    result = await session.execute(
        select(WorkflowRole.workflow_id, WorkflowRole.role_id)
        .where(WorkflowRole.workflow_id.in_(workflow_ids))
        .order_by(WorkflowRole.workflow_id, WorkflowRole.role_id)
    )
    role_ids_by_workflow: dict[UUID, list[UUID]] = {}
    for workflow_id, role_id in result.all():
        role_ids_by_workflow.setdefault(workflow_id, []).append(role_id)
    return role_ids_by_workflow


async def get_form_workflow_ids(session: AsyncSession, form_id: UUID) -> set[UUID]:
    """Get all workflow IDs referenced by a form."""
    from sqlalchemy.orm import selectinload

    from src.models.orm.forms import Form

    result = await session.execute(
        select(Form)
        .options(selectinload(Form.fields))
        .where(Form.id == form_id)
    )
    form = result.scalar_one_or_none()

    if not form:
        return set()

    workflow_ids: set[UUID] = set()

    if form.workflow_id:
        try:
            workflow_ids.add(UUID(form.workflow_id))
        except ValueError as e:
            logger.debug(f"form.workflow_id not a UUID, skipping: {e}")

    if form.launch_workflow_id:
        try:
            workflow_ids.add(UUID(form.launch_workflow_id))
        except ValueError as e:
            logger.debug(f"form.launch_workflow_id not a UUID, skipping: {e}")

    for field in form.fields:
        if field.data_provider_id:
            workflow_ids.add(field.data_provider_id)

    return workflow_ids


async def get_app_workflow_ids(session: AsyncSession, app_id: UUID) -> set[UUID]:
    """Get all workflow IDs referenced by an app."""
    from src.models.orm.applications import Application
    from src.models.orm.file_index import FileIndex
    from src.models.orm.workflows import Workflow as WfORM
    from src.services.app_dependencies import parse_dependencies

    app_result = await session.execute(
        select(Application).where(Application.id == app_id)
    )
    app = app_result.scalar_one_or_none()
    if not app:
        return set()

    # Independently deployed V2 Apps have no server-side source tree. Their
    # workflow references are resolved dynamically by the live SDK at runtime.
    if app.repo_path is None:
        return set()

    prefix = app.repo_prefix
    fi_result = await session.execute(
        select(FileIndex.content).where(
            FileIndex.path.startswith(prefix),
        )
    )

    all_refs: set[str] = set()
    for (content,) in fi_result.all():
        if content:
            all_refs.update(parse_dependencies(content))

    if not all_refs:
        return set()

    wf_result = await session.execute(
        select(WfORM.id, WfORM.name).where(WfORM.is_active.is_(True))
    )
    matched: set[UUID] = set()
    for wf_id, wf_name in wf_result.all():
        if str(wf_id) in all_refs or wf_name in all_refs:
            matched.add(wf_id)

    return matched


async def compute_used_by_counts(
    session: AsyncSession, workflow_ids: list[UUID]
) -> dict[UUID, int]:
    """Batch-compute how many entities reference each workflow."""
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    from src.models.orm.agents import AgentTool
    from src.models.orm.forms import Form, FormField

    refs_form_wf = (
        select(Form.workflow_id.cast(PG_UUID(as_uuid=True)).label("wf_id"))
        .where(
            Form.is_active == True,  # noqa: E712
            Form.workflow_id.isnot(None),
            func.length(Form.workflow_id) == 36,
        )
    )
    refs_form_launch = (
        select(Form.launch_workflow_id.cast(PG_UUID(as_uuid=True)).label("wf_id"))
        .where(
            Form.is_active == True,  # noqa: E712
            Form.launch_workflow_id.isnot(None),
            func.length(Form.launch_workflow_id) == 36,
        )
    )
    refs_form_dp = (
        select(FormField.data_provider_id.label("wf_id"))
        .where(FormField.data_provider_id.isnot(None))
    )
    refs_agent = (
        select(AgentTool.workflow_id.label("wf_id"))
    )

    all_refs = union_all(
        refs_form_wf, refs_form_launch, refs_form_dp, refs_agent
    ).subquery("all_refs")

    count_query = (
        select(
            all_refs.c.wf_id,
            func.count().label("cnt"),
        )
        .where(all_refs.c.wf_id.in_(workflow_ids))
        .group_by(all_refs.c.wf_id)
    )

    result = await session.execute(count_query)
    return {row.wf_id: row.cnt for row in result.all()}


# =============================================================================
# workflows.list()
# =============================================================================


async def list_sdk_workflows(
    session: AsyncSession,
    principal: UserPrincipal,
    *,
    type: str | None = None,
    is_tool: bool | None = None,
    scope: str | None = None,
    filter_by_form: UUID | None = None,
    filter_by_app: UUID | None = None,
    filter_by_agent: UUID | None = None,
):
    """List all workflows visible to the principal.

    Resolves the selected scope from the verified caller's original-user reach,
    then filters rows by ``workflows.read`` at the actual row organization (or
    selected organization for a shared Global fallback) before dependency
    counts and serialization. Type /
    legacy ``is_tool`` filters, entity filters (form wins over app wins
    over agent), batch used-by counts and role IDs, and per-row
    conversion failures skipped with an error log.

    Raises:
        SdkExecutionReadError: 403 for a scope outside original-user reach;
            422 for malformed scope input.
    """
    from src.models import Workflow as WorkflowORM
    from src.models.orm.agents import AgentTool
    from src.services.workflow_permissions import get_default_workflow_permission_mode, workflow_permission_requests

    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from shared.scope_resolver import UNSET, shared_read_organization

    caller = await load_caller(session, principal)
    try:
        selected_org = await resolve_sdk_scope(scope, caller=caller)
    except ScopeResolutionError as e:
        raise SdkExecutionReadError(e.status_code, e.detail) from None

    query = select(WorkflowORM).where(WorkflowORM.is_active.is_(True))
    reach_filter = user_reach(caller).where(WorkflowORM.organization_id)
    if reach_filter is not None:
        query = query.where(reach_filter)

    scope_is_unset = scope is None or scope == ""
    if scope == "global":
        query = query.where(WorkflowORM.organization_id.is_(None))
    elif not scope_is_unset:
        query = query.where(WorkflowORM.organization_id == selected_org)

    if type is not None:
        query = query.where(WorkflowORM.type == type)
    elif is_tool is not None:
        if is_tool:
            query = query.where(WorkflowORM.type == "tool")
        else:
            query = query.where(WorkflowORM.type != "tool")

    if filter_by_form:
        workflow_ids = await get_form_workflow_ids(session, filter_by_form)
        if workflow_ids:
            query = query.where(WorkflowORM.id.in_(workflow_ids))
        else:
            return []
    elif filter_by_app:
        workflow_ids = await get_app_workflow_ids(session, filter_by_app)
        if workflow_ids:
            query = query.where(WorkflowORM.id.in_(workflow_ids))
        else:
            return []
    elif filter_by_agent:
        workflow_ids_subquery = select(AgentTool.workflow_id).where(
            AgentTool.agent_id == filter_by_agent,
        )
        query = query.where(WorkflowORM.id.in_(workflow_ids_subquery))

    result = await session.execute(query)
    selected_permission_scope = UNSET if scope_is_unset else selected_org
    workflows = [
        workflow
        for workflow in result.scalars().all()
        if user_reach(caller).covers(workflow.organization_id)
        and allows_permission(
            caller,
            "workflows.read",
            shared_read_organization(
                caller,
                workflow.organization_id,
                selected_organization_id=selected_permission_scope,
            ),
        )
    ]

    workflow_ids = [w.id for w in workflows]
    used_by_counts: dict[UUID, int] = {}
    role_ids_by_workflow: dict[UUID, list[UUID]] = {}
    if workflow_ids:
        used_by_counts = await compute_used_by_counts(session, workflow_ids)
        role_ids_by_workflow = await get_workflow_role_ids(session, workflow_ids)

    default_permission_mode = await get_default_workflow_permission_mode(session)
    permission_requests = await workflow_permission_requests(session, workflows)
    workflow_list = []
    for w in workflows:
        try:
            workflow_list.append(
                convert_workflow_orm_to_schema(
                    w,
                    default_permission_mode,
                    used_by_count=used_by_counts.get(w.id, 0),
                    role_ids=role_ids_by_workflow.get(w.id, []),
                    permission_request=permission_requests.get(w.id),
                )
            )
        except Exception as e:
            logger.error(f"Failed to convert workflow '{w.name}': {e}")

    logger.info(f"Returning {len(workflow_list)} workflows (scope={log_safe(scope) or 'default'})")
    return workflow_list


# =============================================================================
# executions.list() helpers
# =============================================================================


def _execution_org_name(execution) -> str | None:
    """Display name for the execution's effective scope."""
    if execution.organization_id:
        if hasattr(execution, "organization") and execution.organization is not None:
            return execution.organization.name
        return None
    return "Global"


def to_execution_summary(execution):
    """Convert SQLAlchemy model to the payload-free list model."""
    from src.models.contracts.executions import ExecutionSummary
    from src.models.enums import ExecutionStatus

    return ExecutionSummary(
        execution_id=str(execution.id),
        workflow_name=execution.workflow_name,
        workflow_id=str(execution.workflow_id) if execution.workflow_id else None,
        org_id=str(execution.organization_id) if execution.organization_id else None,
        org_name=_execution_org_name(execution),
        form_id=str(execution.form_id) if execution.form_id else None,
        executed_by=str(execution.executed_by),
        executed_by_name=execution.executed_by_name or str(execution.executed_by),
        executed_by_email=execution.executed_by_user.email if hasattr(execution, "executed_by_user") and execution.executed_by_user else None,
        status=ExecutionStatus(execution.status),
        result_type=execution.result_type,
        error_message=execution.error_message,
        duration_ms=execution.duration_ms,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        scheduled_at=execution.scheduled_at,
        created_at=execution.created_at,
    )


# =============================================================================
# executions.list()
# =============================================================================


async def list_sdk_executions(
    session: AsyncSession,
    principal: UserPrincipal,
    *,
    scope: str | None = None,
    workflow_name: str | None = None,
    workflow_id: UUID | None = None,
    status_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 25,
    offset: int = 0,
    cursor: tuple[datetime | None, UUID] | None = None,
) -> tuple[list, str | None]:
    """List executions with filtering and keyset pagination.

    Explicit scope is validated and always restricts the query. Visibility is
    applied before ordering, paging, and the ``limit + 1`` sample: own rows
    require original-user reach, while other rows require scoped
    ``executions.read.all`` within that same reach.
    ``workflow_id`` winning over ``workflow_name``,
    comma-separated status match-any, silently-ignored malformed
    start/end dates, the
    ``started_at/scheduled_at/completed_at/created_at`` timeline anchor,
    keyset cursor with legacy numeric-offset fallback, and keyset
    continuation tokens.

    Callers pass transport-neutral filter values: the HTTP handler owns
    camelCase/snake_case alias resolution, unknown-param rejection, UUID
    and ISO-8601 validation, and boolean parsing, and forwards the
    validated values here.

    Raises:
        SdkExecutionReadError: 422 for a malformed scope or execution status.
    """
    from src.models import Execution as ExecutionModel
    from src.models.enums import ExecutionStatus

    statuses: list[ExecutionStatus] | None = None
    if status_filter:
        try:
            statuses = [
                ExecutionStatus(value.strip())
                for value in status_filter.split(",")
                if value.strip()
            ]
        except ValueError:
            raise SdkExecutionReadError(
                422, "status contains an invalid execution status"
            ) from None

    scope_filter = None
    if scope:
        if scope == "global":
            scope_filter = ExecutionModel.organization_id.is_(None)
        else:
            try:
                scope_filter = ExecutionModel.organization_id == UUID(scope)
            except ValueError:
                raise SdkExecutionReadError(422, f"Invalid scope value: {scope}") from None

    caller = await load_caller(session, principal)

    query = select(ExecutionModel).options(
        selectinload(ExecutionModel.organization),
        selectinload(ExecutionModel.executed_by_user),
        defer(ExecutionModel.parameters, raiseload=True),
        defer(ExecutionModel.result, raiseload=True),
        defer(ExecutionModel.variables, raiseload=True),
        defer(ExecutionModel.execution_context, raiseload=True),
    )

    visibility_filter = execution_list_filter(caller, ExecutionModel)
    if visibility_filter is not None:
        query = query.where(visibility_filter)
    if scope_filter is not None:
        query = query.where(scope_filter)

    if workflow_id:
        query = query.where(ExecutionModel.workflow_id == workflow_id)
    elif workflow_name:
        query = query.where(ExecutionModel.workflow_name == workflow_name)

    if statuses is not None:
        query = query.where(ExecutionModel.status.in_(statuses))

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            if start_dt.tzinfo is not None:
                start_dt = start_dt.replace(tzinfo=None)
            query = query.where(ExecutionModel.started_at >= start_dt)
        except ValueError as e:
            logger.debug(f"invalid start_date {log_safe(start_date)!r}, ignoring filter: {log_safe(e)}")

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is not None:
                end_dt = end_dt.replace(tzinfo=None)
            query = query.where(ExecutionModel.started_at <= end_dt)
        except ValueError as e:
            logger.debug(f"invalid end_date {log_safe(end_date)!r}, ignoring filter: {log_safe(e)}")

    timeline_at = func.coalesce(
        ExecutionModel.started_at,
        ExecutionModel.scheduled_at,
        ExecutionModel.completed_at,
        ExecutionModel.created_at,
    )
    query = query.order_by(
        desc(timeline_at),
        desc(ExecutionModel.id),
    )

    if cursor is not None:
        cursor_timeline, cursor_id = cursor
        if cursor_timeline is None:
            query = query.where(
                and_(timeline_at.is_(None), ExecutionModel.id < cursor_id)
            )
        else:
            query = query.where(
                or_(
                    timeline_at < cursor_timeline,
                    and_(
                        timeline_at == cursor_timeline,
                        ExecutionModel.id < cursor_id,
                    ),
                )
            )
    elif offset:
        query = query.offset(offset)

    query = query.limit(limit + 1)

    result = await session.execute(query)
    executions = list(result.scalars().all())

    has_more = len(executions) > limit
    if has_more:
        executions = executions[:limit]

    next_token = None
    if has_more and executions:
        last = executions[-1]
        last_timeline = (
            last.started_at
            or last.scheduled_at
            or last.completed_at
            or last.created_at
        )
        next_token = encode_history_cursor(last_timeline, last.id)

    return [to_execution_summary(e) for e in executions], next_token


# =============================================================================
# executions.get() (also serves workflows.get())
# =============================================================================


async def get_sdk_execution(
    session: AsyncSession,
    principal: UserPrincipal,
    execution_id: UUID,
):
    """Get execution details by ID with authorization.

    Preserves the historical ``GET /api/executions/{id}`` behavior
    exactly: 404 when the row is missing, 403 when the original user cannot view the row,
    dual-read logs (Redis Stream when in-progress, Postgres when
    complete, DEBUG/TRACEBACK filtered for non-admins), AI usage rows
    plus totals, permission-gated variables/context/resource fields, and the
    Global org display name.

    Raises:
        SdkExecutionReadError: 404 when missing; 403 when denied.
    """
    from bifrost._logging import read_logs_from_stream
    from src.models import Execution as ExecutionModel
    from src.models import ExecutionLog as ExecutionLogORM
    from src.models import ExecutionLogPublic, WorkflowExecution
    from src.models.contracts.executions import (
        AIUsagePublicSimple,
        AIUsageTotalsSimple,
    )
    from src.models.enums import ExecutionStatus
    from src.models.orm.ai_usage import AIUsage

    caller = await load_caller(session, principal)
    result = await session.execute(
        select(ExecutionModel)
        .options(selectinload(ExecutionModel.organization), selectinload(ExecutionModel.executed_by_user))
        .where(ExecutionModel.id == execution_id)
    )
    execution = result.scalar_one_or_none()

    if not execution:
        raise SdkExecutionReadError(404, f"Execution {execution_id} not found")

    access = await current_execution_read_access(caller, execution)
    if not access.visible:
        raise SdkExecutionReadError(
            403, "You do not have permission to view this execution"
        )

    is_in_progress = execution.status in (
        ExecutionStatus.PENDING, ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING,
    )
    logs: list = []

    if is_in_progress:
        try:
            stream_logs = await read_logs_from_stream(str(execution_id), count=10000)
            hidden_levels = set() if access.diagnostics else {"DEBUG", "TRACEBACK"}
            for seq, slog in enumerate(stream_logs):
                level = (slog.level or "INFO").upper()
                if level in hidden_levels:
                    continue
                logs.append(ExecutionLogPublic(
                    id=seq,
                    timestamp=slog.timestamp or "",
                    level=level.lower(),
                    message=slog.message or "",
                    data=slog.metadata if isinstance(slog.metadata, dict) else None,
                    sequence=seq,
                ))
        except Exception:
            logger.warning(f"Failed to read logs from Redis for execution {log_safe(execution_id)}, falling back to DB")
            is_in_progress = False

    if not is_in_progress:
        logs_query = (
            select(ExecutionLogORM)
            .where(ExecutionLogORM.execution_id == execution_id)
            .order_by(ExecutionLogORM.sequence)
        )
        if not access.diagnostics:
            logs_query = logs_query.where(
                ExecutionLogORM.level.notin_(["DEBUG", "TRACEBACK"])
            )
        logs_result = await session.execute(logs_query)
        log_entries = logs_result.scalars().all()

        logs = [
            ExecutionLogPublic(
                id=log.id,
                timestamp=log.timestamp.isoformat() if log.timestamp else "",
                level=log.level or "info",
                message=log.message or "",
                data=log.log_metadata,
                sequence=log.sequence,
            )
            for log in log_entries
        ]

    ai_usage_query = (
        select(AIUsage)
        .where(AIUsage.execution_id == execution_id)
        .order_by(AIUsage.sequence)
    )
    ai_usage_result = await session.execute(ai_usage_query)
    ai_usage_entries = ai_usage_result.scalars().all()

    ai_usage_list = [
        AIUsagePublicSimple(
            provider=entry.provider,
            model=entry.model,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            cache_read_tokens=entry.cache_read_tokens,
            cache_write_tokens=entry.cache_write_tokens,
            provider_cost=(str(entry.provider_cost) if entry.provider_cost is not None else None),
            cost=str(entry.cost) if entry.cost else None,
            duration_ms=entry.duration_ms,
            timestamp=entry.timestamp.isoformat() if entry.timestamp else "",
            sequence=entry.sequence,
        )
        for entry in ai_usage_entries
    ]

    ai_totals = None
    if ai_usage_entries:
        totals_query = select(
            func.coalesce(func.sum(AIUsage.input_tokens), 0).label("total_input"),
            func.coalesce(func.sum(AIUsage.output_tokens), 0).label("total_output"),
            func.coalesce(func.sum(AIUsage.cache_read_tokens), 0).label("total_cache_read"),
            func.coalesce(func.sum(AIUsage.cache_write_tokens), 0).label("total_cache_write"),
            func.coalesce(func.sum(AIUsage.provider_cost), Decimal("0")).label("total_provider_cost"),
            func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("total_cost"),
            func.coalesce(func.sum(AIUsage.duration_ms), 0).label("total_duration"),
            func.count(AIUsage.id).label("call_count"),
        ).where(AIUsage.execution_id == execution_id)

        totals_result = await session.execute(totals_query)
        totals_row = totals_result.one()

        ai_totals = AIUsageTotalsSimple(
            total_input_tokens=int(totals_row.total_input or 0),
            total_output_tokens=int(totals_row.total_output or 0),
            total_cache_read_tokens=int(totals_row.total_cache_read or 0),
            total_cache_write_tokens=int(totals_row.total_cache_write or 0),
            total_provider_cost=str(totals_row.total_provider_cost or Decimal("0")),
            total_cost=str(totals_row.total_cost or Decimal("0")),
            total_duration_ms=int(totals_row.total_duration or 0),
            call_count=int(totals_row.call_count or 0),
        )

    if execution.organization_id:
        org_name = execution.organization.name if execution.organization else None
    else:
        org_name = "Global"

    return WorkflowExecution(
        execution_id=str(execution.id),
        workflow_name=execution.workflow_name,
        workflow_id=str(execution.workflow_id) if execution.workflow_id else None,
        org_id=str(execution.organization_id) if execution.organization_id else None,
        org_name=org_name,
        form_id=str(execution.form_id) if execution.form_id else None,
        executed_by=str(execution.executed_by),
        executed_by_name=execution.executed_by_name or str(execution.executed_by),
        executed_by_email=execution.executed_by_user.email if execution.executed_by_user else None,
        status=ExecutionStatus(execution.status),
        input_data=execution.parameters or {},
        result=execution.result,
        result_type=execution.result_type,
        error_message=execution.error_message,
        duration_ms=execution.duration_ms,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        scheduled_at=execution.scheduled_at,
        logs=[log.model_dump() for log in logs],
        variables=execution.variables if access.diagnostics else None,
        execution_context=execution.execution_context if access.diagnostics else None,
        peak_memory_bytes=execution.peak_memory_bytes if access.diagnostics else None,
        process_rss_bytes=execution.process_rss_bytes if access.diagnostics else None,
        cpu_total_seconds=execution.cpu_total_seconds if access.diagnostics else None,
        ai_usage=ai_usage_list if ai_usage_list else None,
        ai_totals=ai_totals,
    )
