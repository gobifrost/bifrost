"""
Agent Runs Router

CRUD + execute endpoints for autonomous agent runs.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, func, literal_column, or_, select, update
from sqlalchemy.orm import selectinload

from shared.run_lineage import principal_lineage
from src.core.auth import CurrentActiveUser
from src.core.database import get_session_factory
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.agent_run_flag_conversations import (
    FlagConversationResponse,
    SendFlagMessageRequest,
)
from src.models.contracts.agent_runs import (
    AgentRunCreateRequest,
    AgentRunListResponse,
    AgentRunRerunResponse,
    AgentRunResponse,
    AgentRunUsageSummary,
    BackfillEligibleResponse,
    BackfillSummariesRequest,
    BackfillSummariesResponse,
    DryRunRequest,
    DryRunResponse,
    MetadataKeysResponse,
    MetadataValuesResponse,
    SummaryBackfillJobListResponse,
    SummaryBackfillJobResponse,
    VerdictRequest,
    VerdictResponse,
)
from shared.sdk_agent_runs import (
    SdkAgentRunError,
    resolve_agent_run_actor,
    resolve_executable_agent,
    resolve_rerun_actor,
)
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_run_verdict_history import AgentRunVerdictHistory
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.ai_usage import AIUsage
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.core.redis_client import get_redis_client
from src.routers.agent_run_sdk import detail_router, enqueue_router
from src.services.agent_run_usage_summary import summarize_run_usage
from src.services.agent_write_policy import caller_user_id
from src.services.authorization.enforce import (
    allows_permission,
    load_caller,
    operation_reach,
    org_target,
    require_operation,
    require_permission,
    user_reach,
)
from src.services.execution.agent_run_access import agent_run_visibility_conditions
from src.services.execution.agent_run_service import (
    enqueue_agent_run,
    wait_for_agent_run_result,
)
from src.services.execution.dry_run import evaluate_against_prompt
from src.services.execution.summary_dispatch import hand_off_summary, request_summary
from src.services.execution.tuning_service import (
    append_user_message_and_reply,
    get_or_create_conversation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-runs", tags=["Agent Runs"])


async def _get_executable_agent(
    db: DbSession,
    agent_name: str,
    user: CurrentActiveUser,
) -> Agent:
    """Resolve an agent and enforce solution execution availability.

    Thin wrapper over the shared service so ``/execute`` keeps its exact
    historical behavior (404 unknown name, 409 inactive Solution) while
    the shared service enforces the access check.
    """
    try:
        return await resolve_executable_agent(db, agent_name=agent_name, principal=user)
    except SdkAgentRunError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


async def _require_own_private_agent_run(
    db: DbSession, caller, run: AgentRun
) -> None:
    """Enforce the tuning-action rule on a run already passed visibility.

    Tuning actions are narrower than plain run visibility: the owner may act
    on a run of their own PRIVATE agent.  Another caller needs the exact
    ``agentruns.readwrite.all`` power at the run's organization.  Runtime
    credentials are evaluated as their original run user.
    """
    if (
        user_reach(caller).covers(run.org_id)
        and allows_permission(caller, "agentruns.readwrite.all", run.org_id)
    ):
        return

    agent = (
        await db.execute(select(Agent).where(Agent.id == run.agent_id))
    ).scalar_one_or_none()
    if (
        agent is None
        or agent.access_level != AgentAccessLevel.PRIVATE
        or agent.owner_user_id != caller_user_id(caller)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run.id} not found",
        )


def _may_cancel_run(caller, run: AgentRun) -> bool:
    """Own cancellation is personal; cancelling another user's run is named."""
    if run.caller_user_id == str(caller_user_id(caller)):
        return True
    return user_reach(caller).covers(run.org_id) and allows_permission(
        caller, "agentruns.readwrite.all", run.org_id
    )


def _run_to_response(
    run: AgentRun,
    usage_summary: AgentRunUsageSummary | None = None,
) -> AgentRunResponse:
    """Convert AgentRun ORM to AgentRunResponse."""
    return AgentRunResponse(
        id=run.id,
        agent_id=run.agent_id,
        agent_name=run.agent.name if run.agent else None,
        trigger_type=run.trigger_type,
        trigger_source=run.trigger_source,
        conversation_id=run.conversation_id,
        event_delivery_id=run.event_delivery_id,
        input=run.input,
        output=run.output,
        status=run.status,
        error=run.error,
        org_id=run.org_id,
        caller_user_id=run.caller_user_id,
        caller_email=run.caller_email,
        caller_name=run.caller_name,
        run_as_user_id=run.run_as_user_id,
        run_as_user_name=run.run_as_user.name if run.run_as_user else None,
        run_as_user_email=run.run_as_user.email if run.run_as_user else None,
        iterations_used=run.iterations_used,
        tokens_used=run.tokens_used,
        budget_max_iterations=run.budget_max_iterations,
        budget_max_tokens=run.budget_max_tokens,
        duration_ms=run.duration_ms,
        llm_model=run.llm_model,
        asked=run.asked,
        did=run.did,
        answered=run.answered,
        metadata=run.run_metadata or {},
        confidence=run.confidence,
        confidence_reason=run.confidence_reason,
        summary_status=run.summary_status,
        summary_error=run.summary_error,
        verdict=run.verdict,
        verdict_note=run.verdict_note,
        verdict_set_at=run.verdict_set_at,
        verdict_set_by=run.verdict_set_by,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        parent_run_id=run.parent_run_id,
        usage_summary=usage_summary,
    )


@router.get("")
async def list_agent_runs(
    db: DbSession,
    user: CurrentActiveUser,
    agent_id: UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    trigger_type: str | None = None,
    org_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    q: str | None = Query(
        None,
        description="Full-text search across asked/did/error/caller/metadata",
    ),
    verdict: str | None = Query(
        None,
        description="Filter by verdict: 'up', 'down', or 'unreviewed'",
    ),
    metadata_filter: str | None = Query(
        None,
        description=(
            "JSON array of conditions on run metadata, e.g. "
            '[{"key":"billing_status","op":"eq","value":"Billable"},'
            '{"key":"service_category","op":"contains","value":"security"}]. '
            "Supported ops: 'eq' (exact match) and 'contains' "
            "(case-insensitive substring). All conditions are AND-ed."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    cursor: str | None = Query(
        None,
        description="Opaque page position returned as next_cursor; offset remains supported.",
    ),
) -> AgentRunListResponse:
    """List agent runs with optional filters."""
    caller = await load_caller(db, user)
    page_offset = offset
    if cursor is not None:
        try:
            page_offset = int(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="cursor is invalid",
            ) from exc
        if page_offset < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="cursor is invalid",
            )
    # Global history stays root-only so one orchestration is counted once.
    # An individual agent's history includes delegated runs because those are
    # real executions of that agent and should not disappear from its Runs tab.
    query = select(AgentRun).join(AgentRun.agent)
    if agent_id is None:
        query = query.where(
            AgentRun.parent_run_id.is_(None),
            AgentRun.trigger_type != "delegation",
        )

    query = query.where(*agent_run_visibility_conditions(caller))

    # Apply optional filters
    if agent_id is not None:
        query = query.where(AgentRun.agent_id == agent_id)
    if status_filter is not None:
        query = query.where(AgentRun.status == status_filter)
    if trigger_type is not None:
        query = query.where(AgentRun.trigger_type == trigger_type)
    if org_id is not None:
        query = query.where(AgentRun.org_id == org_id)
    if start_date is not None:
        query = query.where(AgentRun.created_at >= start_date)
    if end_date is not None:
        query = query.where(AgentRun.created_at <= end_date)

    # Full-text search via the materialized ``search_tsv`` generated column
    # (see migration 20260421e_run_tsvector_search). The column is added via
    # raw DDL and isn't on the ORM model, hence ``literal_column``.
    if q:
        query = query.where(
            literal_column("search_tsv").op("@@")(
                func.plainto_tsquery("english", q)
            )
        )

    if verdict is not None:
        if verdict == "unreviewed":
            query = query.where(AgentRun.verdict.is_(None)).where(
                AgentRun.status == "completed"
            )
        elif verdict in ("up", "down"):
            query = query.where(AgentRun.verdict == verdict)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid verdict filter: {verdict}",
            )

    if metadata_filter:
        try:
            md = json.loads(metadata_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="metadata_filter must be valid JSON",
            )
        if not isinstance(md, list):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="metadata_filter must be a JSON array of {key,op,value} objects",
            )
        for cond in md:
            if (
                not isinstance(cond, dict)
                or "key" not in cond
                or "value" not in cond
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="each metadata_filter entry needs 'key' and 'value'",
                )
            k = str(cond["key"])
            v = str(cond["value"])
            op = str(cond.get("op", "contains")).lower()
            # AgentRun.run_metadata is the Python attribute; the DB column is
            # ``metadata``. JSONB key access returns text via .astext.
            col = AgentRun.run_metadata[k].astext
            if op == "eq":
                query = query.where(col == v)
            elif op == "contains":
                # Case-insensitive substring. % escaping isn't needed for the
                # use case (metadata values are short tags from the agent);
                # ilike's wildcards are fine here.
                query = query.where(col.ilike(f"%{v}%"))
            else:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Unsupported metadata_filter op: {op}",
                )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Fetch paginated results
    query = (
        query.options(selectinload(AgentRun.run_as_user))
        .order_by(desc(AgentRun.created_at))
        .limit(limit + 1)
        .offset(page_offset)
    )
    result = await db.execute(query)
    runs = list(result.scalars().all())
    has_more = len(runs) > limit
    if has_more:
        runs = runs[:limit]

    next_cursor = str(page_offset + len(runs)) if has_more else None

    usage_summaries = await summarize_run_usage(db, [run.id for run in runs])
    return AgentRunListResponse(
        items=[_run_to_response(run, usage_summaries.get(run.id)) for run in runs],
        total=total,
        next_cursor=next_cursor,
    )


# -----------------------------------------------------------------------------
# Metadata aggregation (powers the captured-data filter on the runs list)
# -----------------------------------------------------------------------------


def _enforce_agent_scope(agent_id: UUID, user) -> None:  # type: ignore[no-untyped-def]
    """Caller must be able to see the agent to aggregate its metadata.

    We keep the enforcement simple: any authenticated user can ask about
    agents in their org; superusers can ask about any agent. The list
    endpoint itself enforces org scoping on the result set, and these
    aggregation endpoints only reveal keys/values that already appear in
    a run the caller could have seen — so there's no separate info leak
    vector beyond what ``GET /api/agent-runs?agent_id=...`` would expose.
    """
    _ = user  # kept in the signature for future per-agent ACL checks


@router.get(
    "/metadata-keys",
    response_model=MetadataKeysResponse,
)
async def get_metadata_keys(
    db: DbSession,
    user: CurrentActiveUser,
    agent_id: UUID = Query(..., description="Required. Scope keys to this agent."),
) -> MetadataKeysResponse:
    """Distinct top-level keys observed in metadata for this agent's runs.

    The captured-data filter uses this to populate its key combobox so
    users don't have to guess which fields the summarizer actually
    extracts on this agent.
    """
    caller = await load_caller(db, user)
    _enforce_agent_scope(agent_id, user)
    conditions = [
        AgentRun.agent_id == agent_id,
        *agent_run_visibility_conditions(caller),
    ]

    # jsonb_object_keys explodes the top-level keys of each row's metadata;
    # DISTINCT + ORDER BY gives the UI a stable, deduped list.
    key_col = func.jsonb_object_keys(AgentRun.run_metadata).label("k")
    stmt = (
        select(key_col)
        .where(*conditions)
        .distinct()
        .order_by(key_col)
    )
    result = await db.execute(stmt)
    return MetadataKeysResponse(keys=[row[0] for row in result.all()])


@router.get(
    "/metadata-values",
    response_model=MetadataValuesResponse,
)
async def get_metadata_values(
    db: DbSession,
    user: CurrentActiveUser,
    agent_id: UUID = Query(..., description="Required. Scope values to this agent."),
    key: str = Query(..., min_length=1, description="Metadata key to aggregate."),
    limit: int = Query(500, ge=1, le=2000),
) -> MetadataValuesResponse:
    """Distinct values observed for ``key`` in metadata for this agent's runs.

    Used by the filter UI when the user picks the 'eq' operator — lets
    them pick from a known-value list instead of free-typing.
    """
    caller = await load_caller(db, user)
    _enforce_agent_scope(agent_id, user)
    conditions = [
        AgentRun.agent_id == agent_id,
        *agent_run_visibility_conditions(caller),
    ]

    value_col = AgentRun.run_metadata[key].astext
    stmt = (
        select(value_col)
        .where(*conditions, value_col.isnot(None))
        .distinct()
        .order_by(value_col)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return MetadataValuesResponse(values=[row[0] for row in result.all() if row[0]])


@router.get(
    "/backfill-jobs",
    response_model=SummaryBackfillJobListResponse,
)
async def list_backfill_jobs(
    db: DbSession,
    user: CurrentActiveUser,
    active: bool = Query(
        default=False,
        description="If true, only return running jobs.",
    ),
) -> SummaryBackfillJobListResponse:
    """Admin-only: list summary backfill jobs (optionally filtered to active).

    Registered before ``/{run_id}`` so the literal path isn't swallowed by
    the run-detail handler.
    """
    caller = await load_caller(db, user)
    reach = operation_reach(caller, "GET /api/agent-runs/backfill-jobs")
    q = select(SummaryBackfillJob).order_by(desc(SummaryBackfillJob.created_at))
    if active:
        q = q.where(SummaryBackfillJob.status == "running")
    jobs = (await db.execute(q)).scalars().all()
    jobs = [job for job in jobs if await _backfill_job_in_reach(db, caller, job, reach)]
    return SummaryBackfillJobListResponse(
        items=[SummaryBackfillJobResponse.model_validate(j) for j in jobs]
    )


@router.get(
    "/backfill-eligible",
    response_model=BackfillEligibleResponse,
)
async def get_backfill_eligible(
    db: DbSession,
    user: CurrentActiveUser,
    agent_id: UUID | None = None,
    prompt_version_below: str | None = None,
    include_completed: bool = False,
) -> BackfillEligibleResponse:
    """Lightweight preview the UI uses to decide whether to show the Backfill
    button at all. Returns 0/0.00 if nothing is eligible — caller can hide
    the affordance instead of surfacing a dead-end "Nothing to backfill"
    modal.

    Three orthogonal flags shape the count:
      - default (no flags)                  → pending + failed summaries
      - ``prompt_version_below="vN"``       → above + completed runs on an
                                               older prompt version (or NULL)
      - ``include_completed=true``          → above + ALL completed summaries
                                               regardless of version
    """
    caller = await load_caller(db, user)
    reach = operation_reach(caller, "GET /api/agent-runs/backfill-eligible")
    statuses = ["pending", "failed"]
    if prompt_version_below is not None or include_completed:
        statuses.append("completed")
    conditions = [
        AgentRun.status == "completed",
        AgentRun.summary_status.in_(statuses),
        *_agent_run_reach_conditions(reach),
    ]
    if agent_id is not None:
        conditions.append(AgentRun.agent_id == agent_id)
    if prompt_version_below is not None and not include_completed:
        # Only constrain by version when the caller specifically asked for
        # the version-below scope; ``include_completed=true`` overrides it
        # because that scope is "everything completed, no version filter".
        conditions.append(
            or_(
                AgentRun.summary_prompt_version.is_(None),
                AgentRun.summary_prompt_version < prompt_version_below,
            )
        )

    count = (
        await db.execute(
            select(func.count()).select_from(AgentRun).where(*conditions)
        )
    ).scalar() or 0

    per_run_cost, basis = await _estimate_per_run_cost(db, reach=reach)
    estimated_total = (per_run_cost * Decimal(count)).quantize(Decimal("0.0001"))

    return BackfillEligibleResponse(
        eligible=count,
        estimated_cost_usd=estimated_total,
        cost_basis=basis,  # type: ignore[arg-type]
    )


# These routes already carry the public prefix and tags. Append them at their
# original registration positions so route precedence and OpenAPI stay exact.
router.routes.extend(detail_router.routes)


@router.post("/{run_id}/rerun")
async def rerun_agent_run(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentRunRerunResponse:
    """Rerun an agent run with the same input (async, non-blocking)."""
    caller = await load_caller(db, user)
    query = select(AgentRun).where(AgentRun.id == run_id)

    query = query.where(*agent_run_visibility_conditions(caller))

    result = await db.execute(query)
    original = result.scalar_one_or_none()

    if not original:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    await _require_own_private_agent_run(db, caller, original)

    try:
        actor = await resolve_rerun_actor(db, user, original)
    except SdkAgentRunError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    require_permission(caller, "agents.execute", actor.org_id)

    new_run_id = await enqueue_agent_run(
        agent_id=str(original.agent_id),
        trigger_type="rerun",
        input_data=original.input,
        trigger_source=str(run_id),
        output_schema=original.output_schema,
        org_id=str(actor.org_id) if actor.org_id else None,
        caller_user_id=str(user.user_id),
        caller_email=user.email,
        caller_name=getattr(user, "name", None),
        caller_is_superuser=user.is_superuser,
        caller_is_external=user.is_external,
        caller_is_provider_org=user.is_provider_org,
        sync=False,
        lineage=await principal_lineage(db, user),
        run_as_user_id=actor.run_as_user_id,
    )

    return AgentRunRerunResponse(run_id=UUID(new_run_id))


TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "paused",
    "budget_exceeded",
    "timeout",
}


@router.post("/{run_id}/cancel")
async def cancel_agent_run(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Cancel a queued or running agent run."""
    caller = await load_caller(db, user)
    query = select(AgentRun).where(AgentRun.id == run_id)

    query = query.where(*agent_run_visibility_conditions(caller))

    result = await db.execute(query)
    agent_run = result.scalar_one_or_none()

    if not agent_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    if not _may_cancel_run(caller, agent_run):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent run {run_id} not found")

    if agent_run.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel agent run with status '{agent_run.status}'",
        )

    if agent_run.status == "cancelling":
        # Already cancelling — idempotent
        return {"run_id": str(run_id), "status": "cancelling"}

    redis_client = get_redis_client()

    if agent_run.status == "queued":
        # Claim cancellation only while the run is still queued. If the worker
        # wins this transition, refresh and continue through running cancel.
        cancelled_at = datetime.now(timezone.utc)
        cancel_result = await db.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.status == "queued")
            .values(status="cancelled", completed_at=cancelled_at)
            .returning(AgentRun.id)
        )
        if cancel_result.scalar_one_or_none() is None:
            await db.refresh(agent_run)
        else:
            await db.commit()
            return {"run_id": str(run_id), "status": "cancelled"}

    if agent_run.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel agent run with status '{agent_run.status}'",
        )

    # Running — set to cancelling and signal via Redis
    agent_run.status = "cancelling"
    agent_run.cancel_requested_at = datetime.now(timezone.utc)
    await db.commit()

    await redis_client.set_agent_run_cancel_flag(str(run_id))
    logger.info(f"Set cancel flag for agent run {log_safe(run_id)}")

    try:
        from src.core.pubsub import publish_agent_run_update
        await publish_agent_run_update(agent_run, agent_run.agent.name if agent_run.agent else "Unknown")
    except Exception as e:
        # Cancel flag already set; pubsub notify is best-effort UI hint
        logger.debug(f"failed to publish agent_run cancel update for {log_safe(run_id)}: {log_safe(e)}")

    return {"run_id": str(run_id), "status": "cancelling"}


@router.post("/{run_id}/verdict", response_model=VerdictResponse)
async def set_verdict(
    run_id: UUID,
    request: VerdictRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> VerdictResponse:
    """Set a verdict on a completed run. Records an audit row."""
    caller = await load_caller(db, user)
    query = select(AgentRun).where(AgentRun.id == run_id)

    query = query.where(*agent_run_visibility_conditions(caller))

    result = await db.execute(query)
    run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    await _require_own_private_agent_run(db, caller, run)
    if run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Verdict can only be set on completed runs (current status: {run.status})",
        )

    now = datetime.now(timezone.utc)
    previous = run.verdict
    run.verdict = request.verdict
    run.verdict_note = request.note
    run.verdict_set_at = now
    run.verdict_set_by = user.user_id

    db.add(
        AgentRunVerdictHistory(
            run_id=run.id,
            previous_verdict=previous,
            new_verdict=request.verdict,
            changed_by=user.user_id,
            changed_at=now,
            note=request.note,
        )
    )
    await db.commit()

    return VerdictResponse(
        run_id=run.id,
        verdict=run.verdict,
        verdict_note=run.verdict_note,
        verdict_set_at=run.verdict_set_at,
        verdict_set_by=run.verdict_set_by,
    )


@router.delete("/{run_id}/verdict", response_model=VerdictResponse)
async def clear_verdict(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> VerdictResponse:
    """Clear the verdict on a run. Records an audit row."""
    caller = await load_caller(db, user)
    query = select(AgentRun).where(AgentRun.id == run_id)

    query = query.where(*agent_run_visibility_conditions(caller))

    result = await db.execute(query)
    run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    await _require_own_private_agent_run(db, caller, run)

    now = datetime.now(timezone.utc)
    previous = run.verdict
    run.verdict = None
    run.verdict_note = None
    run.verdict_set_at = now
    run.verdict_set_by = user.user_id

    db.add(
        AgentRunVerdictHistory(
            run_id=run.id,
            previous_verdict=previous,
            new_verdict=None,
            changed_by=user.user_id,
            changed_at=now,
            note=None,
        )
    )
    await db.commit()

    return VerdictResponse(
        run_id=run.id,
        verdict=None,
        verdict_note=None,
        verdict_set_at=now,
        verdict_set_by=user.user_id,
    )


@router.get(
    "/{run_id}/flag-conversation",
    response_model=FlagConversationResponse,
)
async def get_flag_conversation(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> FlagConversationResponse:
    """Return the tuning conversation attached to a flagged run.

    Creates an empty conversation row if none exists yet so the UI can
    stream messages into a stable ``id``.
    """
    caller = await load_caller(db, user)
    query = select(AgentRun).where(AgentRun.id == run_id)
    query = query.where(*agent_run_visibility_conditions(caller))
    run = (await db.execute(query)).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    await _require_own_private_agent_run(db, caller, run)

    conv = await get_or_create_conversation(run_id, db)
    # Persist the created-empty conversation so subsequent GETs see the same id.
    await db.commit()
    return FlagConversationResponse(
        id=conv.id,
        run_id=conv.run_id,
        messages=conv.messages,  # type: ignore[arg-type]
        created_at=conv.created_at,
        last_updated_at=conv.last_updated_at,
    )


@router.post(
    "/{run_id}/flag-conversation/message",
    response_model=FlagConversationResponse,
)
async def send_flag_message(
    run_id: UUID,
    request: SendFlagMessageRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> FlagConversationResponse:
    """Append a user turn and synchronously get the tuning-model reply."""
    caller = await load_caller(db, user)
    query = select(AgentRun).where(AgentRun.id == run_id)
    query = query.where(*agent_run_visibility_conditions(caller))
    run = (await db.execute(query)).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    await _require_own_private_agent_run(db, caller, run)
    if run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Flag conversations are only available on completed runs "
                f"(current status: {run.status})"
            ),
        )

    conv = await append_user_message_and_reply(run_id, request.content, db)
    return FlagConversationResponse(
        id=conv.id,
        run_id=conv.run_id,
        messages=conv.messages,  # type: ignore[arg-type]
        created_at=conv.created_at,
        last_updated_at=conv.last_updated_at,
    )


@router.post("/{run_id}/regenerate-summary")
async def regenerate_summary(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Reset summary state and re-enqueue a summarization job. Admin-only."""
    run = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.id == run_id)
            .with_for_update(of=AgentRun)
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    caller = await load_caller(db, user)
    require_operation(caller, "POST /api/agent-runs/{run_id}/regenerate-summary", org_target(run.org_id))

    summary = await request_summary(db, run)
    await db.commit()

    await hand_off_summary(summary)

    return {"status": "enqueued", "run_id": str(run_id)}


@router.post("/{run_id}/dry-run", response_model=DryRunResponse)
async def dry_run_agent_run(
    run_id: UUID,
    request: DryRunRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> DryRunResponse:
    """Evaluate a proposed system prompt against a past run's transcript.

    Single LLM call — does not re-execute tools. Returns a structured
    verdict indicating whether the new prompt would produce the same
    decision. Records an ``AIUsage`` row on the original run for cost
    tracking (``sequence=8000``).
    """
    caller = await load_caller(db, user)
    query = select(AgentRun).where(AgentRun.id == run_id)
    query = query.where(*agent_run_visibility_conditions(caller))

    run = (await db.execute(query)).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    await _require_own_private_agent_run(db, caller, run)
    require_permission(caller, "agents.execute", run.org_id)
    if run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Dry-run is only available on completed runs "
                f"(current status: {run.status})"
            ),
        )

    session_factory = get_session_factory()
    result = await evaluate_against_prompt(
        run_id=run_id,
        proposed_prompt=request.proposed_prompt,
        session_factory=session_factory,
    )

    return DryRunResponse(
        run_id=run_id,
        would_still_decide_same=result.would_still_decide_same,
        reasoning=result.reasoning,
        alternative_action=result.alternative_action,
        confidence=result.confidence,
    )


router.routes.extend(enqueue_router.routes)


@router.post("/execute")
async def execute_agent_run(
    request: AgentRunCreateRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Execute an agent synchronously via the SDK."""
    agent = await _get_executable_agent(db, request.agent_name, user)

    # Paused agents short-circuit gracefully — HTTP 200 with structured body.
    # Downstream consumers (webhook senders, SDK) discriminate on status="paused".
    if not agent.is_active:
        return {
            "status": "paused",
            "accepted": False,
            "message": f"Agent '{agent.name}' is paused. Request not processed.",
            "agent_id": str(agent.id),
        }

    try:
        actor = await resolve_agent_run_actor(db, user, agent, request.run_as)
    except SdkAgentRunError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    require_permission(await load_caller(db, user), "agents.execute", actor.org_id)

    # Enqueue the agent run for sync execution
    run_id = await enqueue_agent_run(
        agent_id=str(agent.id),
        trigger_type="api",
        input_data=request.input,
        output_schema=request.output_schema,
        org_id=str(actor.org_id) if actor.org_id else None,
        caller_user_id=str(user.user_id),
        caller_email=user.email,
        caller_name=getattr(user, "name", None),
        caller_is_superuser=user.is_superuser,
        caller_is_external=user.is_external,
        caller_is_provider_org=user.is_provider_org,
        sync=True,
        lineage=await principal_lineage(db, user),
        run_as_user_id=actor.run_as_user_id,
    )

    # Wait for the result
    result_data = await wait_for_agent_run_result(run_id, timeout=request.timeout)

    if result_data is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Agent run timed out",
        )

    if actor.run_as_user_id is not None:
        return {**result_data, "run_as_user_id": str(actor.run_as_user_id)}
    return result_data


# -----------------------------------------------------------------------------
# Bulk summary backfill (admin-only)
# -----------------------------------------------------------------------------

_BACKFILL_FALLBACK_PER_RUN_COST = Decimal("0.002")


def _agent_run_reach_conditions(reach) -> tuple:
    """SQL predicates for a named operation's original-user organization reach."""
    predicate = reach.where(AgentRun.org_id)
    return () if predicate is None else (predicate,)


async def _backfill_job_in_reach(db, caller, job: SummaryBackfillJob, reach) -> bool:  # type: ignore[no-untyped-def]
    """A backfill job inherits the actual organization of its selected Agent."""
    if job.agent_id is None:
        return reach.everything or job.requested_by == caller_user_id(caller)
    organization_id = await db.scalar(select(Agent.organization_id).where(Agent.id == job.agent_id))
    return organization_id is not None and reach.covers(organization_id) and user_reach(caller).covers(organization_id)


async def _estimate_per_run_cost(db, *, reach) -> tuple[Decimal, str]:  # type: ignore[no-untyped-def]
    """Average cost-per-summarizer-call over the last 100 completed summaries.

    Returns ``(per_run_cost, basis)`` where basis is 'history' or 'fallback'.
    """
    recent_runs_subq = (
        select(AgentRun.id)
        .where(AgentRun.summary_status == "completed", *_agent_run_reach_conditions(reach))
        .order_by(desc(AgentRun.summary_generated_at))
        .limit(100)
        .subquery()
    )
    result = await db.execute(
        select(func.coalesce(func.sum(AIUsage.cost), Decimal("0"))).where(
            AIUsage.agent_run_id.in_(select(recent_runs_subq.c.id))
        )
    )
    total_cost = result.scalar() or Decimal("0")
    # Count how many of those runs actually had a usage row we'd be dividing by.
    count_result = await db.execute(
        select(func.count(func.distinct(AIUsage.agent_run_id))).where(
            AIUsage.agent_run_id.in_(select(recent_runs_subq.c.id))
        )
    )
    usage_count = count_result.scalar() or 0
    if usage_count > 0 and total_cost > 0:
        return Decimal(total_cost) / Decimal(usage_count), "history"
    return _BACKFILL_FALLBACK_PER_RUN_COST, "fallback"


@router.post("/backfill-summaries", response_model=BackfillSummariesResponse)
async def backfill_summaries(
    request: BackfillSummariesRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> BackfillSummariesResponse:
    """Enqueue summarization for pending/failed runs. Admin-only.

    If ``dry_run=true``, returns the eligible count and estimated cost
    without enqueuing anything. Otherwise creates a ``SummaryBackfillJob``
    orchestration row and publishes one ``agent-summarization`` message
    per run tagged with the job_id; progress is broadcast on the
    ``summary-backfill:{job_id}`` channel.
    """
    caller = await load_caller(db, user)
    reach = operation_reach(caller, "POST /api/agent-runs/backfill-summaries")

    # Build the base query: completed runs that still need (re-)summarization.
    conditions = [
        AgentRun.status == "completed",
        AgentRun.summary_status.in_(request.statuses),
        *_agent_run_reach_conditions(reach),
    ]
    if request.agent_id is not None:
        conditions.append(AgentRun.agent_id == request.agent_id)
    if request.prompt_version_below is not None:
        # NULL-versioned rows match (they pre-date versioning). Rows tagged
        # with an older version also match; current-or-newer versions skip.
        conditions.append(
            or_(
                AgentRun.summary_prompt_version.is_(None),
                AgentRun.summary_prompt_version < request.prompt_version_below,
            )
        )

    id_query = (
        select(AgentRun.id)
        .where(*conditions)
        .order_by(desc(AgentRun.created_at))
        .limit(request.limit)
    )
    run_ids = list((await db.execute(id_query)).scalars().all())
    eligible = len(run_ids)

    per_run_cost, basis = await _estimate_per_run_cost(db, reach=reach)
    estimated_total = (per_run_cost * Decimal(eligible)).quantize(Decimal("0.0001"))

    if request.dry_run or eligible == 0:
        return BackfillSummariesResponse(
            job_id=None,
            queued=0,
            eligible=eligible,
            estimated_cost_usd=estimated_total,
            cost_basis=basis,  # type: ignore[arg-type]
        )

    # Persist the orchestration row first so the worker can increment counters.
    from uuid import uuid4

    central_job_id = uuid4()
    job = SummaryBackfillJob(
        id=central_job_id,
        agent_id=request.agent_id,
        requested_by=caller_user_id(caller),
        status="running",
        total=eligible,
        estimated_cost_usd=estimated_total,
    )
    db.add(job)
    # The runs reference the job, so it must exist before they are updated.
    await db.flush()

    # Flip all targeted runs back to pending so the UI reflects the queued state
    # immediately (the summarizer's idempotent short-circuit on 'completed' will
    # skip them otherwise, but admins asked for a retry — respect that).
    from sqlalchemy import update as sql_update
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id.in_(run_ids))
        .values(
            summary_status="pending",
            summary_error=None,
            summary_backfill_job_id=central_job_id,
        )
    )
    from src.jobs.platform.summary_backfill import (
        SUMMARY_BACKFILL_DEFINITION,
        SummaryBackfillPayload,
    )
    from src.services.platform_jobs import enqueue_platform_job, publish_platform_job_update

    platform_job, _ = await enqueue_platform_job(
        db,
        SUMMARY_BACKFILL_DEFINITION,
        SummaryBackfillPayload(backfill_job_id=job.id, run_ids=run_ids),
        dedupe_key=str(job.id),
        priority=100,
        organization_id=None,
        requested_by_user_id=caller_user_id(caller),
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="summary_backfill",
        resource_id=str(job.id),
        title=f"Summarizing {eligible} agent runs",
        action_url="/agents",
        job_id=central_job_id,
    )
    await db.commit()
    await publish_platform_job_update(platform_job)

    return BackfillSummariesResponse(
        job_id=job.id,
        queued=eligible,
        eligible=eligible,
        estimated_cost_usd=estimated_total,
        cost_basis=basis,  # type: ignore[arg-type]
    )


@router.get(
    "/backfill-jobs/{job_id}",
    response_model=SummaryBackfillJobResponse,
)
async def get_backfill_job(
    job_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> SummaryBackfillJobResponse:
    """Admin-only: current progress for a summary backfill job."""
    caller = await load_caller(db, user)
    reach = operation_reach(caller, "GET /api/agent-runs/backfill-jobs/{job_id}")
    job = (
        await db.execute(
            select(SummaryBackfillJob).where(SummaryBackfillJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Summary backfill job {job_id} not found",
        )
    if not await _backfill_job_in_reach(db, caller, job, reach):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Summary backfill job {job_id} not found")
    return SummaryBackfillJobResponse.model_validate(job)


@router.post(
    "/backfill-jobs/{job_id}/cancel",
    response_model=SummaryBackfillJobResponse,
)
async def cancel_backfill_job(
    job_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> SummaryBackfillJobResponse:
    """Mark a backfill job as cancelled so the UI unblocks.

    This does NOT withdraw summaries already dispatched — executors keep
    summarising those runs. What it does do:

    - flips ``status`` from ``running`` to ``cancelled``
    - sets ``completed_at`` so the row stops appearing in "active" queries
    - broadcasts final state so the progress card dismisses itself

    Use this when progress has stalled and the counter never advanced —
    admins need a way out.
    """
    caller = await load_caller(db, user)
    reach = operation_reach(caller, "POST /api/agent-runs/backfill-jobs/{job_id}/cancel")
    job = (
        await db.execute(
            select(SummaryBackfillJob).where(SummaryBackfillJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Summary backfill job {job_id} not found",
        )
    if not await _backfill_job_in_reach(db, caller, job, reach):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Summary backfill job {job_id} not found")
    if job.status != "running":
        # Already terminal — return current state without re-broadcasting.
        return SummaryBackfillJobResponse.model_validate(job)

    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()

    from src.models.orm.platform_jobs import PlatformJob
    from src.services.platform_jobs import request_platform_job_cancel

    platform_job = await db.get(PlatformJob, job_id)
    if platform_job is not None:
        await request_platform_job_cancel(db, platform_job)

    # Broadcast so any attached progress card dismisses itself.
    from src.core.pubsub import publish_summary_backfill_update
    await publish_summary_backfill_update(
        job_id,
        {
            "total": job.total,
            "succeeded": job.succeeded,
            "failed": job.failed,
            "status": job.status,
            "actual_cost_usd": str(job.actual_cost_usd),
            "estimated_cost_usd": str(job.estimated_cost_usd),
        },
    )

    return SummaryBackfillJobResponse.model_validate(job)
