"""
Usage Reports Router

Provides usage reports endpoints for platform administrators.
Similar to ROI reports but focused on AI usage metrics.

Endpoint Structure:
- GET /api/reports/usage - Complete usage report for a date range
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from src.core.auth import Context, CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession, ReadSnapshotDbSession
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
)
from src.models.orm import (
    AIUsage,
    Conversation,
    Execution,
    KnowledgeStorageDaily,
    Organization,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from shared.models import QualityUsageBreakdownResponse
from shared.quality_usage_reporting import (
    QualityUsageReportFilters,
    UsageReportPagination,
    summarize_quality_usage,
    utc_inclusive_dates_to_half_open,
)

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
    global_only: bool = Query(
        default=False, description="Filter to platform-global rows with no organization"
    ),
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
    if org_id and global_only:
        raise HTTPException(
            status_code=422, detail="org_id and global_only cannot be used together"
        )
    filter_org_id = None if global_only else org_id or (str(ctx.org_id) if ctx.org_id else None)
    if filter_org_id:
        base_conditions.append(AIUsage.organization_id == filter_org_id)
    elif global_only:
        base_conditions.append(AIUsage.organization_id.is_(None))

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
        elif global_only:
            exec_conditions.append(Execution.organization_id.is_(None))

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
        elif global_only:
            workflow_query = workflow_query.where(AIUsage.organization_id.is_(None))

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
        elif global_only:
            conv_query = conv_query.where(AIUsage.organization_id.is_(None))

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
        elif global_only:
            agent_query = agent_query.where(AIUsage.organization_id.is_(None))

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
    by_organization: list[OrganizationUsage] = []
    if not global_only:
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
        elif global_only:
            storage_query = storage_query.where(
                KnowledgeStorageDaily.organization_id.is_(None)
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
        elif global_only:
            trends_storage_query = trends_storage_query.where(
                KnowledgeStorageDaily.organization_id.is_(None)
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
    "/usage/breakdown",
    response_model=QualityUsageBreakdownResponse,
    summary="Get quality usage breakdown",
    description=(
        "Get testing/review-aware AI usage breakdown for a date range. "
        "Platform admin only."
    ),
    dependencies=[RequirePlatformAdmin],
)
async def get_usage_breakdown(
    ctx: Context,
    user: CurrentActiveUser,
    db: ReadSnapshotDbSession,
    start_date: date = Query(..., description="Start date in UTC (inclusive)"),
    end_date: date = Query(..., description="End date in UTC (inclusive)"),
    org_id: Annotated[
        UUID | None, Query(description="Filter by organization UUID")
    ] = None,
    global_only: Annotated[
        bool, Query(description="Filter to platform-global rows with no organization")
    ] = False,
    source: Annotated[
        Literal["all", "executions", "chat", "agents"], Query()
    ] = "all",
    purpose: Annotated[str | None, Query()] = None,
    provider: Annotated[str | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
    profile_id: Annotated[UUID | None, Query()] = None,
    profile_fingerprint: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QualityUsageBreakdownResponse:
    del user
    try:
        start_at, end_at = utc_inclusive_dates_to_half_open(start_date, end_date)
        if org_id and global_only:
            raise ValueError("org_id and global_only cannot be used together")
        filter_org_id = None if global_only else org_id or ctx.org_id
        report = await summarize_quality_usage(
            db,
            QualityUsageReportFilters(
                scope=(
                    "organization"
                    if filter_org_id
                    else "global_only"
                    if global_only
                    else "platform"
                ),
                organization_id=filter_org_id,
                start_at=start_at,
                end_at=end_at,
                source=source,
                purpose=purpose,
                provider=provider,
                model=model,
                profile_id=profile_id,
                profile_fingerprint=profile_fingerprint,
                pagination=UsageReportPagination(limit=limit, offset=offset),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return QualityUsageBreakdownResponse.model_validate(report)
