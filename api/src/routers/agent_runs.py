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
from sqlalchemy import desc, func, literal_column, or_, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.cache.keys import agent_run_steps_stream_key
from src.core.cache.redis_client import get_redis
from src.core.database import get_session_factory
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.agent_run_flag_conversations import (
    FlagConversationResponse,
    SendFlagMessageRequest,
)
from src.models.contracts.agent_runs import (
    AgentRunCreateRequest,
    AgentRunDetailResponse,
    AgentRunListResponse,
    AgentRunRerunResponse,
    AgentRunResponse,
    AgentRunStepResponse,
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
from src.models.contracts.executions import AIUsagePublicSimple, AIUsageTotalsSimple
from src.models.orm.agent_run_verdict_history import AgentRunVerdictHistory
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.models.orm.agents import Agent
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.core.redis_client import get_redis_client
from src.services.execution.agent_run_service import (
    enqueue_agent_run,
    wait_for_agent_run_result,
)
from src.services.execution.dry_run import evaluate_against_prompt
from src.services.execution.run_summarizer import enqueue_summarize
from src.services.execution.tuning_service import (
    append_user_message_and_reply,
    get_or_create_conversation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-runs", tags=["Agent Runs"])


def _run_to_response(run: AgentRun) -> AgentRunResponse:
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
) -> AgentRunListResponse:
    """List agent runs with optional filters."""
    # Build base query — exclude delegation sub-runs from top-level list
    query = select(AgentRun).join(AgentRun.agent).where(AgentRun.parent_run_id.is_(None))

    # Org filter: non-superusers see only their org's runs
    if not user.is_superuser:
        if user.organization_id:
            query = query.where(AgentRun.org_id == user.organization_id)

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
    query = query.order_by(desc(AgentRun.created_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    runs = result.scalars().all()

    return AgentRunListResponse(
        items=[_run_to_response(run) for run in runs],
        total=total,
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
    _enforce_agent_scope(agent_id, user)
    conditions = [AgentRun.agent_id == agent_id]
    if not user.is_superuser and user.organization_id:
        conditions.append(AgentRun.org_id == user.organization_id)

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
    _enforce_agent_scope(agent_id, user)
    conditions = [AgentRun.agent_id == agent_id]
    if not user.is_superuser and user.organization_id:
        conditions.append(AgentRun.org_id == user.organization_id)

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
    if not _is_platform_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can view backfill jobs",
        )
    q = select(SummaryBackfillJob).order_by(desc(SummaryBackfillJob.created_at))
    if active:
        q = q.where(SummaryBackfillJob.status == "running")
    jobs = (await db.execute(q)).scalars().all()
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
    if not _is_platform_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can preview backfills",
        )
    statuses = ["pending", "failed"]
    if prompt_version_below is not None or include_completed:
        statuses.append("completed")
    conditions = [
        AgentRun.status == "completed",
        AgentRun.summary_status.in_(statuses),
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

    per_run_cost, basis = await _estimate_per_run_cost(db)
    estimated_total = (per_run_cost * Decimal(count)).quantize(Decimal("0.0001"))

    return BackfillEligibleResponse(
        eligible=count,
        estimated_cost_usd=estimated_total,
        cost_basis=basis,  # type: ignore[arg-type]
    )


@router.get("/{run_id}")
async def get_agent_run(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentRunDetailResponse:
    """Get agent run detail with steps."""
    query = (
        select(AgentRun)
        .options(selectinload(AgentRun.steps))
        .where(AgentRun.id == run_id)
    )

    # Org filter: non-superusers see only their org's runs
    if not user.is_superuser:
        if user.organization_id:
            query = query.where(AgentRun.org_id == user.organization_id)

    result = await db.execute(query)
    run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )

    # Fetch AI usage records for this run
    ai_usage_result = await db.execute(
        select(AIUsage)
        .where(AIUsage.agent_run_id == run_id)
        .order_by(AIUsage.timestamp)
    )
    ai_usage_entries = ai_usage_result.scalars().all()

    ai_usage_list: list[AIUsagePublicSimple] | None = None
    ai_totals_response: AIUsageTotalsSimple | None = None

    if ai_usage_entries:
        ai_usage_list = [
            AIUsagePublicSimple(
                provider=entry.provider,
                model=entry.model,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                cost=str(entry.cost) if entry.cost else None,
                duration_ms=entry.duration_ms,
                timestamp=entry.timestamp.isoformat(),
                sequence=entry.sequence,
            )
            for entry in ai_usage_entries
        ]

        # Calculate totals
        totals_result = await db.execute(
            select(
                func.sum(AIUsage.input_tokens).label("total_input"),
                func.sum(AIUsage.output_tokens).label("total_output"),
                func.sum(AIUsage.cost).label("total_cost"),
                func.sum(AIUsage.duration_ms).label("total_duration"),
                func.count(AIUsage.id).label("call_count"),
            ).where(AIUsage.agent_run_id == run_id)
        )
        totals_row = totals_result.one()
        ai_totals_response = AIUsageTotalsSimple(
            total_input_tokens=int(totals_row.total_input or 0),
            total_output_tokens=int(totals_row.total_output or 0),
            total_cost=str(totals_row.total_cost or Decimal("0")),
            total_duration_ms=int(totals_row.total_duration or 0),
            call_count=int(totals_row.call_count or 0),
        )

    # Fetch child run IDs for delegation sub-runs
    child_ids_result = await db.execute(
        select(AgentRun.id)
        .where(AgentRun.parent_run_id == run_id)
        .order_by(AgentRun.created_at)
    )
    child_run_ids = [row[0] for row in child_ids_result.all()]

    # Dual-read steps: Redis Stream when in-progress, DB when complete
    steps_response: list[AgentRunStepResponse] = []
    is_in_progress = run.status in ("queued", "running", "cancelling")

    if is_in_progress:
        # Read from Redis Stream (steps are uncommitted in DB during execution)
        try:
            async with get_redis() as r:
                stream_key = agent_run_steps_stream_key(str(run_id))
                entries = await r.xrange(stream_key, min="-", max="+")  # type: ignore[misc]
                for _entry_id, data in entries:
                    content_raw = data.get("content", "{}")
                    content = json.loads(content_raw) if content_raw else None
                    tokens_str = data.get("tokens_used", "")
                    duration_str = data.get("duration_ms", "")
                    steps_response.append(AgentRunStepResponse(
                        id=UUID(data["id"]),
                        run_id=UUID(data["run_id"]),
                        step_number=int(data["step_number"]),
                        type=data["type"],
                        content=content,
                        tokens_used=int(tokens_str) if tokens_str else None,
                        duration_ms=int(duration_str) if duration_str else None,
                        created_at=datetime.fromisoformat(data["created_at"]),
                    ))
        except Exception:
            logger.warning(f"Failed to read steps from Redis for run {log_safe(run_id)}, falling back to DB")
            # Fall back to DB steps (may be empty if uncommitted)
            steps_response = [
                AgentRunStepResponse(
                    id=step.id, run_id=step.run_id, step_number=step.step_number,
                    type=step.type, content=step.content, tokens_used=step.tokens_used,
                    duration_ms=step.duration_ms, created_at=step.created_at,
                )
                for step in run.steps
            ]
    else:
        # Completed — read from DB (steps are committed)
        steps_response = [
            AgentRunStepResponse(
                id=step.id, run_id=step.run_id, step_number=step.step_number,
                type=step.type, content=step.content, tokens_used=step.tokens_used,
                duration_ms=step.duration_ms, created_at=step.created_at,
            )
            for step in run.steps
        ]

    return AgentRunDetailResponse(
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
        child_run_ids=child_run_ids,
        steps=steps_response,
        ai_usage=ai_usage_list,
        ai_totals=ai_totals_response,
    )


@router.post("/{run_id}/rerun")
async def rerun_agent_run(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentRunRerunResponse:
    """Rerun an agent run with the same input (async, non-blocking)."""
    query = select(AgentRun).where(AgentRun.id == run_id)

    # Org filter: non-superusers see only their org's runs
    if not user.is_superuser:
        if user.organization_id:
            query = query.where(AgentRun.org_id == user.organization_id)

    result = await db.execute(query)
    original = result.scalar_one_or_none()

    if not original:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )

    new_run_id = await enqueue_agent_run(
        agent_id=str(original.agent_id),
        trigger_type="rerun",
        input_data=original.input,
        trigger_source=str(run_id),
        output_schema=original.output_schema,
        org_id=str(original.org_id) if original.org_id else None,
        caller_user_id=str(user.user_id),
        caller_email=user.email,
        caller_name=getattr(user, "name", None),
        sync=False,
    )

    return AgentRunRerunResponse(run_id=UUID(new_run_id))


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "budget_exceeded", "timeout"}


@router.post("/{run_id}/cancel")
async def cancel_agent_run(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Cancel a queued or running agent run."""
    query = select(AgentRun).where(AgentRun.id == run_id)

    # Org filter: non-superusers see only their org's runs
    if not user.is_superuser:
        if user.organization_id:
            query = query.where(AgentRun.org_id == user.organization_id)

    result = await db.execute(query)
    agent_run = result.scalar_one_or_none()

    if not agent_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )

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
        # Not yet picked up by worker — cancel directly
        agent_run.status = "cancelled"
        agent_run.completed_at = datetime.now(timezone.utc)
        await db.commit()

        # Also mark the Redis context so worker skips if it picks up concurrently
        from src.core.cache.redis_client import get_redis
        redis_key = f"bifrost:agent_run:{run_id}:context"
        async with get_redis() as r:
            context_raw = await r.get(redis_key)
            if context_raw:
                ctx = json.loads(context_raw)
                ctx["cancelled"] = True
                ttl = await r.ttl(redis_key)
                await r.set(redis_key, json.dumps(ctx), ex=max(ttl, 60))

        return {"run_id": str(run_id), "status": "cancelled"}

    # Running — set to cancelling and signal via Redis
    agent_run.status = "cancelling"
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
    query = select(AgentRun).where(AgentRun.id == run_id)

    # Org filter: non-superusers see only their org's runs
    if not user.is_superuser:
        if user.organization_id:
            query = query.where(AgentRun.org_id == user.organization_id)

    result = await db.execute(query)
    run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
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
    query = select(AgentRun).where(AgentRun.id == run_id)

    # Org filter: non-superusers see only their org's runs
    if not user.is_superuser:
        if user.organization_id:
            query = query.where(AgentRun.org_id == user.organization_id)

    result = await db.execute(query)
    run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )

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
    query = select(AgentRun).where(AgentRun.id == run_id)
    if not user.is_superuser and user.organization_id:
        query = query.where(AgentRun.org_id == user.organization_id)
    run = (await db.execute(query)).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )

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
    query = select(AgentRun).where(AgentRun.id == run_id)
    if not user.is_superuser and user.organization_id:
        query = query.where(AgentRun.org_id == user.organization_id)
    run = (await db.execute(query)).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
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
    is_admin = user.is_superuser or any(
        role in ["Platform Admin", "Platform Owner"] for role in user.roles
    )
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can regenerate run summaries",
        )

    run = (
        await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )

    run.summary_status = "pending"
    run.summary_error = None
    await db.commit()

    await enqueue_summarize(run_id)

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
    query = select(AgentRun).where(AgentRun.id == run_id)
    if not user.is_superuser and user.organization_id:
        query = query.where(AgentRun.org_id == user.organization_id)

    run = (await db.execute(query)).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
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


@router.post("/execute")
async def execute_agent_run(
    request: AgentRunCreateRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Execute an agent synchronously via the SDK."""
    # Look up agent by name (case-insensitive)
    result = await db.execute(
        select(Agent).where(Agent.name.ilike(request.agent_name))
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{request.agent_name}' not found",
        )

    # Paused agents short-circuit gracefully — HTTP 200 with structured body.
    # Downstream consumers (webhook senders, SDK) discriminate on status="paused".
    if not agent.is_active:
        return {
            "status": "paused",
            "accepted": False,
            "message": f"Agent '{agent.name}' is paused. Request not processed.",
            "agent_id": str(agent.id),
        }

    # Enqueue the agent run for sync execution
    run_id = await enqueue_agent_run(
        agent_id=str(agent.id),
        trigger_type="api",
        input_data=request.input,
        output_schema=request.output_schema,
        org_id=str(user.organization_id) if user.organization_id else None,
        caller_user_id=str(user.user_id),
        caller_email=user.email,
        caller_name=getattr(user, "name", None),
        sync=True,
    )

    # Wait for the result
    result_data = await wait_for_agent_run_result(run_id, timeout=request.timeout)

    if result_data is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Agent run timed out",
        )

    return result_data


# -----------------------------------------------------------------------------
# Bulk summary backfill (admin-only)
# -----------------------------------------------------------------------------

_BACKFILL_FALLBACK_PER_RUN_COST = Decimal("0.002")


def _is_platform_admin(user) -> bool:  # type: ignore[no-untyped-def]
    return bool(user.is_superuser) or any(
        role in ["Platform Admin", "Platform Owner"] for role in user.roles
    )


async def _estimate_per_run_cost(db) -> tuple[Decimal, str]:  # type: ignore[no-untyped-def]
    """Average cost-per-summarizer-call over the last 100 completed summaries.

    Returns ``(per_run_cost, basis)`` where basis is 'history' or 'fallback'.
    """
    recent_runs_subq = (
        select(AgentRun.id)
        .where(AgentRun.summary_status == "completed")
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
    if not _is_platform_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can trigger summary backfills",
        )

    # Build the base query: completed runs that still need (re-)summarization.
    conditions = [
        AgentRun.status == "completed",
        AgentRun.summary_status.in_(request.statuses),
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

    per_run_cost, basis = await _estimate_per_run_cost(db)
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
    job = SummaryBackfillJob(
        agent_id=request.agent_id,
        requested_by=user.user_id,
        status="running",
        total=eligible,
        estimated_cost_usd=estimated_total,
    )
    db.add(job)

    # Flip all targeted runs back to pending so the UI reflects the queued state
    # immediately (the summarizer's idempotent short-circuit on 'completed' will
    # skip them otherwise, but admins asked for a retry — respect that).
    from sqlalchemy import update as sql_update
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id.in_(run_ids))
        .values(summary_status="pending", summary_error=None)
    )
    await db.commit()

    # Now enqueue one message per run, tagged with the job id. Backfills go
    # to a dedicated queue so a 2000-run bulk operation doesn't starve the
    # live ``agent-summarization`` path that serves just-finished runs.
    from src.jobs.rabbitmq import publish_message
    from src.services.execution.run_summarizer import SUMMARIZE_BACKFILL_QUEUE

    for rid in run_ids:
        await publish_message(
            SUMMARIZE_BACKFILL_QUEUE,
            {"run_id": str(rid), "backfill_job_id": str(job.id)},
        )

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
    if not _is_platform_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can view backfill jobs",
        )
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

    This does NOT drain messages already on the ``agent-summarization``
    queue — RabbitMQ will keep delivering them and the worker will keep
    summarising individual runs. What it does do:

    - flips ``status`` from ``running`` to ``cancelled``
    - sets ``completed_at`` so the row stops appearing in "active" queries
    - broadcasts final state so the progress card dismisses itself

    Use this when progress has stalled (e.g. the worker was restarted
    mid-job and prefetched messages went back on the queue but the counter
    never advanced) — admins need a way out.
    """
    if not _is_platform_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can cancel backfill jobs",
        )
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
    if job.status != "running":
        # Already terminal — return current state without re-broadcasting.
        return SummaryBackfillJobResponse.model_validate(job)

    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()

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


