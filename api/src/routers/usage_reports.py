"""
Usage Reports Router

Provides usage reports endpoints for platform administrators.
Similar to ROI reports but focused on AI usage metrics.

Endpoint Structure:
- GET /api/reports/usage - Complete usage report for a date range
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import case, func, select

from src.core.auth import Context, CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.models import (
    UsageReportResponse,
    UsageReportSummary,
    UsageTrend,
    WorkflowUsage,
    ConversationUsage,
    AgentUsage,
    OrganizationUsage,
    KnowledgeStorageUsage,
    KnowledgeStorageTrend,
    WorkflowResourceReport,
    WorkflowResourceRun,
    WorkflowResourceSummary as WorkflowResourceSummaryModel,
    WorkflowResourceWorkflow,
)
from src.models.enums import ExecutionStatus
from src.models.orm import (
    AIUsage,
    Conversation,
    Execution,
    KnowledgeStorageDaily,
    Organization,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["Usage Reports"])


@router.get(
    "/usage",
    response_model=UsageReportResponse,
    summary="Get usage report",
    description="Get AI usage report for a date range. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_usage_report(
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    source: Literal["executions", "chat", "agents", "all"] = Query(
        default="all", description="Source filter: executions, chat, agents, or all"
    ),
    org_id: str | None = Query(default=None, description="Filter by organization ID"),
) -> UsageReportResponse:
    """
    Get usage report data similar to ROI reports.

    Returns:
    - summary: Overall AI usage summary
    - trends: Daily AI usage trends
    - by_workflow: Usage breakdown by workflow
    - by_conversation: Usage breakdown by conversation (when source includes chat)
    - by_organization: Usage breakdown by organization
    """
    # Build base filter conditions
    base_conditions = [
        func.date(AIUsage.timestamp) >= start_date,
        func.date(AIUsage.timestamp) <= end_date,
    ]

    # Organization filter - from query param or context header
    filter_org_id = org_id or (str(ctx.org_id) if ctx.org_id else None)
    if filter_org_id:
        base_conditions.append(AIUsage.organization_id == filter_org_id)

    # Source filter
    if source == "executions":
        base_conditions.append(AIUsage.execution_id.isnot(None))
    elif source == "chat":
        base_conditions.append(AIUsage.conversation_id.isnot(None))
    elif source == "agents":
        base_conditions.append(AIUsage.agent_run_id.isnot(None))

    # 1. Get summary totals
    summary_query = select(
        func.coalesce(func.sum(AIUsage.input_tokens), 0).label("total_input_tokens"),
        func.coalesce(func.sum(AIUsage.output_tokens), 0).label("total_output_tokens"),
        func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("total_ai_cost"),
        func.coalesce(func.count(AIUsage.id), 0).label("total_ai_calls"),
    ).where(*base_conditions)

    summary_result = await db.execute(summary_query)
    summary_row = summary_result.one()

    # Get resource metrics from executions (if source includes executions)
    total_cpu_seconds = 0.0
    peak_memory_bytes = 0

    if source in ("executions", "all"):
        exec_conditions = [
            func.date(Execution.started_at) >= start_date,
            func.date(Execution.started_at) <= end_date,
        ]
        if filter_org_id:
            exec_conditions.append(Execution.organization_id == filter_org_id)

        exec_metrics_query = select(
            func.coalesce(func.sum(Execution.cpu_total_seconds), 0.0).label("total_cpu"),
            func.coalesce(func.max(Execution.peak_memory_bytes), 0).label("peak_memory"),
        ).where(*exec_conditions)

        exec_result = await db.execute(exec_metrics_query)
        exec_row = exec_result.one()
        total_cpu_seconds = float(exec_row.total_cpu or 0)
        peak_memory_bytes = int(exec_row.peak_memory or 0)

    summary = UsageReportSummary(
        total_ai_cost=Decimal(str(summary_row.total_ai_cost or 0)),
        total_input_tokens=int(summary_row.total_input_tokens or 0),
        total_output_tokens=int(summary_row.total_output_tokens or 0),
        total_ai_calls=int(summary_row.total_ai_calls or 0),
        total_cpu_seconds=total_cpu_seconds,
        peak_memory_bytes=peak_memory_bytes,
    )

    # 2. Get daily trends
    trends_query = (
        select(
            func.date(AIUsage.timestamp).label("trend_date"),
            func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("ai_cost"),
            func.coalesce(func.sum(AIUsage.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AIUsage.output_tokens), 0).label("output_tokens"),
        )
        .where(*base_conditions)
        .group_by(func.date(AIUsage.timestamp))
        .order_by(func.date(AIUsage.timestamp))
    )

    trends_result = await db.execute(trends_query)
    trends = [
        UsageTrend(
            date=row.trend_date,
            ai_cost=Decimal(str(row.ai_cost or 0)),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
        )
        for row in trends_result.all()
    ]

    # 3. Get usage by workflow (only if source includes executions)
    by_workflow: list[WorkflowUsage] = []
    if source in ("executions", "all"):
        workflow_query = (
            select(
                Execution.workflow_name,
                func.count(func.distinct(Execution.id)).label("execution_count"),
                func.coalesce(func.sum(AIUsage.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AIUsage.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("ai_cost"),
                func.coalesce(func.sum(Execution.cpu_total_seconds), 0.0).label("cpu_seconds"),
                func.coalesce(func.max(Execution.peak_memory_bytes), 0).label("memory_bytes"),
            )
            .join(Execution, AIUsage.execution_id == Execution.id)
            .where(
                AIUsage.execution_id.isnot(None),
                func.date(AIUsage.timestamp) >= start_date,
                func.date(AIUsage.timestamp) <= end_date,
            )
        )

        if filter_org_id:
            workflow_query = workflow_query.where(AIUsage.organization_id == filter_org_id)

        workflow_query = workflow_query.group_by(Execution.workflow_name).order_by(
            func.sum(AIUsage.cost).desc()
        ).limit(50)

        workflow_result = await db.execute(workflow_query)
        by_workflow = [
            WorkflowUsage(
                workflow_name=row.workflow_name or "Unknown",
                execution_count=int(row.execution_count or 0),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                ai_cost=Decimal(str(row.ai_cost or 0)),
                cpu_seconds=float(row.cpu_seconds or 0),
                memory_bytes=int(row.memory_bytes or 0),
            )
            for row in workflow_result.all()
        ]

    # 4. Get usage by conversation (only if source includes chat)
    by_conversation: list[ConversationUsage] = []
    if source in ("chat", "all"):
        conv_query = (
            select(
                Conversation.id.label("conversation_id"),
                Conversation.title.label("conversation_title"),
                func.count(func.distinct(AIUsage.message_id)).label("message_count"),
                func.coalesce(func.sum(AIUsage.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AIUsage.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("ai_cost"),
            )
            .join(Conversation, AIUsage.conversation_id == Conversation.id)
            .where(
                AIUsage.conversation_id.isnot(None),
                func.date(AIUsage.timestamp) >= start_date,
                func.date(AIUsage.timestamp) <= end_date,
            )
        )

        if filter_org_id:
            conv_query = conv_query.where(AIUsage.organization_id == filter_org_id)

        conv_query = conv_query.group_by(Conversation.id, Conversation.title).order_by(
            func.sum(AIUsage.cost).desc()
        ).limit(50)

        conv_result = await db.execute(conv_query)
        by_conversation = [
            ConversationUsage(
                conversation_id=str(row.conversation_id),
                conversation_title=row.conversation_title,
                message_count=int(row.message_count or 0),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                ai_cost=Decimal(str(row.ai_cost or 0)),
            )
            for row in conv_result.all()
        ]

    # 5. Get usage by agent (only if source includes agents)
    by_agent: list[AgentUsage] = []
    if source in ("agents", "all"):
        agent_query = (
            select(
                Agent.name.label("agent_name"),
                func.count(func.distinct(AgentRun.id)).label("run_count"),
                func.coalesce(func.sum(AIUsage.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AIUsage.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("ai_cost"),
            )
            .join(AgentRun, AIUsage.agent_run_id == AgentRun.id)
            .join(Agent, AgentRun.agent_id == Agent.id)
            .where(
                AIUsage.agent_run_id.isnot(None),
                func.date(AIUsage.timestamp) >= start_date,
                func.date(AIUsage.timestamp) <= end_date,
            )
        )

        if filter_org_id:
            agent_query = agent_query.where(AIUsage.organization_id == filter_org_id)

        agent_query = agent_query.group_by(Agent.name).order_by(
            func.sum(AIUsage.cost).desc()
        ).limit(50)

        agent_result = await db.execute(agent_query)
        by_agent = [
            AgentUsage(
                agent_name=row.agent_name or "Unknown",
                run_count=int(row.run_count or 0),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                ai_cost=Decimal(str(row.ai_cost or 0)),
            )
            for row in agent_result.all()
        ]

    # 6. Get usage by organization
    org_query = (
        select(
            Organization.id.label("org_id"),
            Organization.name.label("org_name"),
            func.count(
                func.distinct(AIUsage.execution_id)
            ).filter(AIUsage.execution_id.isnot(None)).label("execution_count"),
            func.count(
                func.distinct(AIUsage.conversation_id)
            ).filter(AIUsage.conversation_id.isnot(None)).label("conversation_count"),
            func.coalesce(func.sum(AIUsage.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AIUsage.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("ai_cost"),
        )
        .join(Organization, AIUsage.organization_id == Organization.id)
        .where(
            AIUsage.organization_id.isnot(None),
            func.date(AIUsage.timestamp) >= start_date,
            func.date(AIUsage.timestamp) <= end_date,
        )
    )

    # Apply source filter
    if source == "executions":
        org_query = org_query.where(AIUsage.execution_id.isnot(None))
    elif source == "chat":
        org_query = org_query.where(AIUsage.conversation_id.isnot(None))
    elif source == "agents":
        org_query = org_query.where(AIUsage.agent_run_id.isnot(None))

    org_query = org_query.group_by(Organization.id, Organization.name).order_by(
        func.sum(AIUsage.cost).desc()
    ).limit(50)

    org_result = await db.execute(org_query)
    by_organization = [
        OrganizationUsage(
            organization_id=str(row.org_id),
            organization_name=row.org_name or "Unknown",
            execution_count=int(row.execution_count or 0),
            conversation_count=int(row.conversation_count or 0),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            ai_cost=Decimal(str(row.ai_cost or 0)),
        )
        for row in org_result.all()
    ]

    # 6. Get knowledge storage usage
    knowledge_storage: list[KnowledgeStorageUsage] = []
    knowledge_storage_trends: list[KnowledgeStorageTrend] = []
    knowledge_storage_as_of: date | None = None

    # Get the latest snapshot date
    latest_date_query = select(func.max(KnowledgeStorageDaily.snapshot_date))
    latest_date_result = await db.execute(latest_date_query)
    latest_date = latest_date_result.scalar()

    if latest_date:
        knowledge_storage_as_of = latest_date

        # Get latest snapshot with org names
        storage_query = (
            select(
                KnowledgeStorageDaily.organization_id,
                func.coalesce(Organization.name, "Global").label("org_name"),
                KnowledgeStorageDaily.namespace,
                KnowledgeStorageDaily.document_count,
                KnowledgeStorageDaily.size_bytes,
            )
            .outerjoin(
                Organization, KnowledgeStorageDaily.organization_id == Organization.id
            )
            .where(KnowledgeStorageDaily.snapshot_date == latest_date)
            .order_by(KnowledgeStorageDaily.size_bytes.desc())
        )

        # Apply org filter if specified
        if filter_org_id:
            storage_query = storage_query.where(
                KnowledgeStorageDaily.organization_id == filter_org_id
            )

        storage_result = await db.execute(storage_query)
        knowledge_storage = [
            KnowledgeStorageUsage(
                organization_id=str(row.organization_id) if row.organization_id else None,
                organization_name=row.org_name,
                namespace=row.namespace,
                document_count=row.document_count,
                size_bytes=row.size_bytes,
                size_mb=round(row.size_bytes / 1048576, 2),
            )
            for row in storage_result.all()
        ]

        # Get storage trends for the date range
        trends_storage_query = (
            select(
                KnowledgeStorageDaily.snapshot_date,
                func.sum(KnowledgeStorageDaily.document_count).label("docs"),
                func.sum(KnowledgeStorageDaily.size_bytes).label("bytes"),
            )
            .where(KnowledgeStorageDaily.snapshot_date >= start_date)
            .where(KnowledgeStorageDaily.snapshot_date <= end_date)
            .group_by(KnowledgeStorageDaily.snapshot_date)
            .order_by(KnowledgeStorageDaily.snapshot_date)
        )

        if filter_org_id:
            trends_storage_query = trends_storage_query.where(
                KnowledgeStorageDaily.organization_id == filter_org_id
            )

        trends_storage_result = await db.execute(trends_storage_query)
        knowledge_storage_trends = [
            KnowledgeStorageTrend(
                date=row.snapshot_date,
                total_documents=int(row.docs or 0),
                total_size_bytes=int(row.bytes or 0),
                total_size_mb=round((row.bytes or 0) / 1048576, 2),
            )
            for row in trends_storage_result.all()
        ]

    return UsageReportResponse(
        summary=summary,
        trends=trends,
        by_workflow=by_workflow,
        by_conversation=by_conversation,
        by_agent=by_agent,
        by_organization=by_organization,
        knowledge_storage=knowledge_storage,
        knowledge_storage_trends=knowledge_storage_trends,
        knowledge_storage_as_of=knowledge_storage_as_of,
    )


@router.get(
    "/workflow-resources",
    response_model=WorkflowResourceReport,
    summary="Get workflow resource report",
    description="Get per-execution workflow resource usage for a time window. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_workflow_resource_report(
    db: DbSession,
    started_after: datetime = Query(..., description="Start of window (timezone-aware ISO)"),
    started_before: datetime = Query(..., description="End of window (timezone-aware ISO)"),
    view: Literal["runs", "workflows"] = Query(default="runs", description="Aggregate by run or workflow"),
    sort: Literal["cpu", "elapsed", "memory", "ai", "started"] = Query(default="started", description="Sort runs by column"),
    page: int = Query(default=1, ge=1, description="1-based page number"),
    page_size: int = Query(default=25, ge=1, le=100, description="Items per page (max 100)"),
    org_id: UUID | None = Query(default=None, description="Filter by organization ID"),
    workflow_id: UUID | None = Query(default=None, description="Filter by workflow ID"),
    workflow: str | None = Query(default=None, description="Partial workflow name filter"),
    status: ExecutionStatus | None = Query(default=None, description="Filter by execution status"),
) -> WorkflowResourceReport:
    """
    Return one row per execution (runs view) or workflow identity and name
    (workflows view) with resource metrics and aggregated AI usage.

    AI usage is pre-aggregated by execution before joining so multiple AI
    calls do not multiply the run count and executions without AI still appear.
    """
    # Time window validation
    if started_after.tzinfo is None or started_before.tzinfo is None:
        raise HTTPException(status_code=422, detail="started_after and started_before must be timezone-aware")
    if started_before <= started_after:
        raise HTTPException(status_code=422, detail="started_before must be after started_after")
    if started_before - started_after > timedelta(days=31):
        raise HTTPException(status_code=422, detail="Time window must not exceed 31 days")

    # Build filters against the authoritative execution record.
    filters = [
        Execution.started_at >= started_after,
        Execution.started_at <= started_before,
    ]
    if org_id is not None:
        filters.append(Execution.organization_id == org_id)
    if workflow_id is not None:
        filters.append(Execution.workflow_id == workflow_id)
    if workflow:
        filters.append(Execution.workflow_name.ilike(f"%{workflow}%"))
    if status is not None:
        filters.append(Execution.status == status)

    # Pre-aggregate AI usage by execution so multiple calls do not multiply rows.
    ai_agg = (
        select(
            AIUsage.execution_id,
            func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("ai_cost"),
            func.count(AIUsage.id).label("ai_calls"),
            func.coalesce(
                func.sum(
                    AIUsage.input_tokens + AIUsage.output_tokens
                ),
                0,
            ).label("ai_tokens"),
        )
        .where(AIUsage.execution_id.in_(select(Execution.id).where(*filters)))
        .group_by(AIUsage.execution_id)
    ).cte("ai_agg")

    # Summary totals (always computed over the full filtered window).
    summary_result = await db.execute(
        select(
            func.count(Execution.id).label("run_count"),
            func.coalesce(func.sum(Execution.cpu_total_seconds), 0.0).label("total_cpu_seconds"),
            func.coalesce(func.sum(Execution.duration_ms), 0).label("total_duration_ms"),
            func.coalesce(func.sum(ai_agg.c.ai_cost), Decimal("0")).label("total_ai_cost"),
            func.coalesce(func.sum(ai_agg.c.ai_calls), 0).label("total_ai_calls"),
        )
        .select_from(Execution)
        .outerjoin(ai_agg, Execution.id == ai_agg.c.execution_id)
        .where(*filters)
    )
    summary_row = summary_result.one()
    summary = WorkflowResourceSummaryModel(
        run_count=int(summary_row.run_count or 0),
        total_cpu_seconds=float(summary_row.total_cpu_seconds or 0),
        total_duration_ms=int(summary_row.total_duration_ms or 0),
        total_ai_cost=Decimal(str(summary_row.total_ai_cost or 0)),
        total_ai_calls=int(summary_row.total_ai_calls or 0),
    )

    if view == "workflows":
        workflow_sort = {
            "cpu": func.coalesce(func.sum(Execution.cpu_total_seconds), 0.0).desc(),
            "elapsed": func.coalesce(func.sum(Execution.duration_ms), 0).desc(),
            "memory": func.max(Execution.peak_process_rss_bytes).desc().nullslast(),
            "ai": func.coalesce(func.sum(ai_agg.c.ai_cost), Decimal("0")).desc(),
            "started": func.max(Execution.started_at).desc().nullslast(),
        }[sort]
        workflow_query = (
            select(
                Execution.workflow_id,
                Execution.workflow_name,
                func.count(Execution.id).label("run_count"),
                func.sum(case((Execution.status == ExecutionStatus.FAILED, 1), else_=0)).label("failed_count"),
                func.coalesce(func.sum(Execution.cpu_total_seconds), 0.0).label("total_cpu_seconds"),
                func.coalesce(func.sum(Execution.duration_ms), 0).label("total_duration_ms"),
                func.max(Execution.peak_cpu_cores).label("max_peak_cpu_cores"),
                func.max(Execution.peak_process_rss_bytes).label("max_peak_process_rss_bytes"),
                func.coalesce(func.sum(ai_agg.c.ai_cost), Decimal("0")).label("total_ai_cost"),
            )
            .select_from(Execution)
            .outerjoin(ai_agg, Execution.id == ai_agg.c.execution_id)
            .where(*filters)
            .group_by(Execution.workflow_id, Execution.workflow_name)
            .order_by(workflow_sort, Execution.workflow_name.asc(), Execution.workflow_id.asc())
        )
        grouped_workflows = (
            select(Execution.workflow_id, Execution.workflow_name)
            .where(*filters)
            .group_by(Execution.workflow_id, Execution.workflow_name)
            .subquery()
        )
        count_result = await db.execute(
            select(func.count()).select_from(grouped_workflows)
        )
        total = int(count_result.scalar() or 0)
        workflow_rows = await db.execute(
            workflow_query.offset((page - 1) * page_size).limit(page_size)
        )
        workflows = [
            WorkflowResourceWorkflow(
                workflow_id=str(row.workflow_id) if row.workflow_id is not None else None,
                workflow_name=row.workflow_name or "Unknown",
                run_count=int(row.run_count or 0),
                failed_count=int(row.failed_count or 0),
                total_cpu_seconds=float(row.total_cpu_seconds or 0),
                total_duration_ms=int(row.total_duration_ms or 0),
                max_peak_cpu_cores=row.max_peak_cpu_cores,
                max_peak_process_rss_bytes=row.max_peak_process_rss_bytes,
                total_ai_cost=Decimal(str(row.total_ai_cost or 0)),
            )
            for row in workflow_rows.all()
        ]
        return WorkflowResourceReport(
            summary=summary,
            runs=[],
            workflows=workflows,
            total=total,
            page=page,
            page_size=page_size,
        )

    # Runs view
    order_clause = {
        "cpu": Execution.cpu_total_seconds.desc().nullslast(),
        "elapsed": Execution.duration_ms.desc().nullslast(),
        "memory": Execution.peak_process_rss_bytes.desc().nullslast(),
        "ai": func.coalesce(ai_agg.c.ai_cost, Decimal("0")).desc(),
        "started": Execution.started_at.desc().nullslast(),
    }[sort]

    runs_query = (
        select(
            Execution.id,
            Execution.workflow_id,
            Execution.workflow_name,
            Organization.name.label("organization_name"),
            Execution.status,
            Execution.started_at,
            Execution.duration_ms,
            Execution.cpu_total_seconds,
            Execution.peak_cpu_cores,
            Execution.peak_process_rss_bytes,
            func.coalesce(ai_agg.c.ai_cost, Decimal("0")).label("ai_cost"),
            func.coalesce(ai_agg.c.ai_calls, 0).label("ai_calls"),
            func.coalesce(ai_agg.c.ai_tokens, 0).label("ai_tokens"),
        )
        .select_from(Execution)
        .outerjoin(ai_agg, Execution.id == ai_agg.c.execution_id)
        .outerjoin(Organization, Execution.organization_id == Organization.id)
        .where(*filters)
        .order_by(order_clause, Execution.id.desc())
    )

    count_result = await db.execute(
        select(func.count(Execution.id)).select_from(Execution).where(*filters)
    )
    total = int(count_result.scalar() or 0)

    runs_rows = await db.execute(
        runs_query.offset((page - 1) * page_size).limit(page_size)
    )
    runs = [
        WorkflowResourceRun(
            execution_id=str(row.id),
            workflow_id=str(row.workflow_id) if row.workflow_id is not None else None,
            workflow_name=row.workflow_name or "Unknown",
            organization_name=row.organization_name,
            status=row.status.value if hasattr(row.status, "value") else str(row.status),
            started_at=row.started_at,
            duration_ms=row.duration_ms,
            cpu_total_seconds=float(row.cpu_total_seconds) if row.cpu_total_seconds is not None else None,
            peak_cpu_cores=row.peak_cpu_cores,
            peak_process_rss_bytes=row.peak_process_rss_bytes,
            ai_cost=Decimal(str(row.ai_cost or 0)),
            ai_calls=int(row.ai_calls or 0),
            ai_tokens=int(row.ai_tokens or 0),
        )
        for row in runs_rows.all()
    ]

    return WorkflowResourceReport(
        summary=summary,
        runs=runs,
        workflows=[],
        total=total,
        page=page,
        page_size=page_size,
    )
