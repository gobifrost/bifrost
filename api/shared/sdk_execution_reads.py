"""Shared business service for SDK-consumed workflow/execution reads.

Single implementation for both entry points:

- the HTTP handlers (``api/src/routers/workflows.py::list_workflows``,
  ``api/src/routers/executions.py::list_executions`` /
  ``get_execution``) serving SDK callers (``api/bifrost/workflows.py``,
  ``api/bifrost/executions.py``), and
- the same handlers reached by workflow children over the worker-local
  engine socket.

Both paths share scope resolution input (an explicit trusted principal),
the superuser/org-user query split, workflow filters, used-by counts,
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
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType, resolve_org_filter
from src.core.principal import UserPrincipal

logger = logging.getLogger(__name__)


class SdkExecutionReadError(Exception):
    """SDK workflow/execution read failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Missing entities are 404, denied
    access is 403, malformed scope is 400 (workflows list) or 422
    (executions list) — matching the historical handler responses
    exactly.
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
    used_by_count: int = 0,
    role_ids: list[UUID] | None = None,
):
    """Convert ORM model to Pydantic schema for API response."""
    from typing import Literal

    from src.models import WorkflowMetadata, WorkflowParameter
    from src.models.contracts.workflows import ExecutableType
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
        access_level=workflow.access_level or "role_based",
        role_ids=[str(role_id) for role_id in (role_ids or [])],
        parameters=parameters,
        execution_mode=execution_mode,
        timeout_seconds=workflow.timeout_seconds if workflow.timeout_seconds is not None else 1800,
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

    Preserves the historical ``GET /api/workflows`` behavior exactly:
    platform-admin only, org scope via ``resolve_org_filter``, type /
    legacy ``is_tool`` filters, entity filters (form wins over app wins
    over agent), batch used-by counts and role IDs, and per-row
    conversion failures skipped with an error log.

    Raises:
        SdkExecutionReadError: 403 for non-superusers; 400 for a
            malformed scope value.
    """
    from src.models import Workflow as WorkflowORM
    from src.models.orm.agents import AgentTool

    if not principal.is_superuser:
        raise SdkExecutionReadError(403, "Superuser privileges required")

    try:
        filter_type, filter_org = resolve_org_filter(principal, scope)
    except ValueError as e:
        raise SdkExecutionReadError(400, str(e)) from None

    query = select(WorkflowORM).where(WorkflowORM.is_active.is_(True))

    if filter_type == OrgFilterType.ALL:
        pass
    elif filter_type == OrgFilterType.GLOBAL_ONLY:
        query = query.where(WorkflowORM.organization_id.is_(None))
    elif filter_type == OrgFilterType.ORG_ONLY:
        query = query.where(WorkflowORM.organization_id == filter_org)
    elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL:
        query = query.where(
            or_(
                WorkflowORM.organization_id == filter_org,
                WorkflowORM.organization_id.is_(None),
            )
        )

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
    workflows = result.scalars().all()

    workflow_ids = [w.id for w in workflows]
    used_by_counts: dict[UUID, int] = {}
    role_ids_by_workflow: dict[UUID, list[UUID]] = {}
    if workflow_ids:
        used_by_counts = await compute_used_by_counts(session, workflow_ids)
        role_ids_by_workflow = await get_workflow_role_ids(session, workflow_ids)

    workflow_list = []
    for w in workflows:
        try:
            workflow_list.append(
                convert_workflow_orm_to_schema(
                    w,
                    used_by_count=used_by_counts.get(w.id, 0),
                    role_ids=role_ids_by_workflow.get(w.id, []),
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
        session_id=str(execution.session_id) if execution.session_id else None,
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
    exclude_local: bool = True,
    limit: int = 25,
    offset: int = 0,
    cursor: tuple[datetime | None, UUID] | None = None,
) -> tuple[list, str | None]:
    """List executions with filtering and keyset pagination.

    Preserves the historical ``GET /api/executions`` behavior exactly:
    org scope via ``resolve_org_filter`` (org users pinned to their org,
    superusers unfiltered unless scoped), non-superusers restricted to
    their own rows, ``workflow_id`` winning over ``workflow_name``,
    comma-separated status match-any, silently-ignored malformed
    start/end dates, ``exclude_local`` default-true, the
    ``started_at/scheduled_at/completed_at/created_at`` timeline anchor,
    keyset cursor with legacy numeric-offset fallback, and keyset
    continuation tokens.

    Callers pass transport-neutral filter values: the HTTP handler owns
    camelCase/snake_case alias resolution, unknown-param rejection, UUID
    and ISO-8601 validation, and boolean parsing, and forwards the
    validated values here.

    Raises:
        SdkExecutionReadError: 422 for a malformed scope value.
    """
    from src.models import Execution as ExecutionModel

    try:
        filter_type, filter_org = resolve_org_filter(principal, scope)
    except ValueError as e:
        raise SdkExecutionReadError(422, str(e)) from None

    if filter_type in (OrgFilterType.ORG_ONLY, OrgFilterType.ORG_PLUS_GLOBAL):
        org_filter = filter_org
    else:
        org_filter = None

    query = select(ExecutionModel).options(
        selectinload(ExecutionModel.organization),
        selectinload(ExecutionModel.executed_by_user),
        defer(ExecutionModel.parameters, raiseload=True),
        defer(ExecutionModel.result, raiseload=True),
        defer(ExecutionModel.variables, raiseload=True),
        defer(ExecutionModel.execution_context, raiseload=True),
    )

    if org_filter:
        query = query.where(ExecutionModel.organization_id == org_filter)

    if not principal.is_superuser:
        query = query.where(ExecutionModel.executed_by == principal.user_id)

    if workflow_id:
        query = query.where(ExecutionModel.workflow_id == workflow_id)
    elif workflow_name:
        query = query.where(ExecutionModel.workflow_name == workflow_name)

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
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

    if exclude_local:
        query = query.where(ExecutionModel.is_local_execution == False)  # noqa: E712

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
    exactly: 404 when the row is missing (including a Redis-pending
    fallback miss), 403 when a non-superuser views another user's row,
    dual-read logs (Redis Stream when in-progress, Postgres when
    complete, DEBUG/TRACEBACK filtered for non-admins), AI usage rows
    plus totals, admin-only variables/context/resource fields, and the
    Global org display name.

    Raises:
        SdkExecutionReadError: 404 when missing; 403 when denied.
    """
    from bifrost._logging import read_logs_from_stream
    from shared.pending_execution import get_pending_execution_fallback
    from src.models import Execution as ExecutionModel
    from src.models import ExecutionLog as ExecutionLogORM
    from src.models import ExecutionLogPublic, WorkflowExecution
    from src.models.contracts.executions import (
        AIUsagePublicSimple,
        AIUsageTotalsSimple,
    )
    from src.models.enums import ExecutionStatus
    from src.models.orm.ai_usage import AIUsage

    result = await session.execute(
        select(ExecutionModel)
        .options(selectinload(ExecutionModel.organization), selectinload(ExecutionModel.executed_by_user))
        .where(ExecutionModel.id == execution_id)
    )
    execution = result.scalar_one_or_none()

    if not execution:
        pending, pending_error = await get_pending_execution_fallback(
            execution_id,
            principal,
            session,
        )
        if pending_error == "Forbidden":
            raise SdkExecutionReadError(
                403, "You do not have permission to view this execution"
            )
        if pending is None:
            raise SdkExecutionReadError(
                404, f"Execution {execution_id} not found"
            )
        return pending

    if not principal.is_superuser and execution.executed_by != principal.user_id:
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
            hidden_levels = set() if principal.is_superuser else {"DEBUG", "TRACEBACK"}
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
        if not principal.is_superuser:
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
        variables=execution.variables if principal.is_superuser else None,
        execution_context=execution.execution_context if principal.is_superuser else None,
        peak_memory_bytes=execution.peak_memory_bytes if principal.is_superuser else None,
        process_rss_bytes=execution.process_rss_bytes if principal.is_superuser else None,
        cpu_total_seconds=execution.cpu_total_seconds if principal.is_superuser else None,
        ai_usage=ai_usage_list if ai_usage_list else None,
        ai_totals=ai_totals,
    )
