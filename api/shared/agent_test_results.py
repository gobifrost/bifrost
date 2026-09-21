"""Agent-wide latest-results read model (Phase 4c, read-only).

Per logical test: the most recent terminal simulation result and the most
recent complete recorded result, each with its execution context. Pending or
in-progress work reads as unknown (null), never passing. Executions and
evaluations the caller cannot see are omitted without failing the list.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.agent_test_service import _service_error, current_test_versions
from shared.evaluation_matrix_admission import _entity_access_allowed
from src.core.principal import UserPrincipal
from src.models.orm.agent_evaluations import (
    AgentEvaluationExecution,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.agent_recorded_evaluations import (
    AgentRecordedEvaluation,
    AgentRecordedEvaluationResult,
)
from src.models.orm.agents import Agent
from src.services.agent_evaluations.executions import TERMINAL_RESULT_STATUSES

_TERMINAL_EXECUTION_STATUSES = ("succeeded", "failed", "cancelled")


async def _agent_or_404(
    db: AsyncSession, user: UserPrincipal, agent_id: UUID
) -> Agent:
    agent = (
        await db.execute(
            select(Agent)
            .options(selectinload(Agent.roles))
            .where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    if agent is None or not agent.is_active:
        _service_error("agent_not_found", "Agent not found.", 404)
    if not await _entity_access_allowed(agent, user, db):
        _service_error("agent_not_found", "Agent not found.", 404)
    return agent


async def _visible_execution_ids(
    db: AsyncSession, user: UserPrincipal, *, suite_ids: list[UUID]
) -> set[UUID]:
    """Executions whose suite is tenant-visible with an accessible baseline."""
    if not suite_ids:
        return set()
    suites = (
        (
            await db.execute(
                select(AgentEvaluationSuite).where(
                    AgentEvaluationSuite.id.in_(suite_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    visible_suite_ids: set[UUID] = set()
    for suite in suites:
        if not user.is_superuser and suite.org_id != user.organization_id:
            continue
        visible_suite_ids.add(suite.id)
    if not visible_suite_ids:
        return set()
    executions = (
        (
            await db.execute(
                select(AgentEvaluationExecution).where(
                    AgentEvaluationExecution.suite_id.in_(visible_suite_ids),
                    AgentEvaluationExecution.status.in_(_TERMINAL_EXECUTION_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    visible: set[UUID] = set()
    for execution in executions:
        if execution.baseline_agent_id is None:
            continue
        baseline = (
            await db.execute(
                select(Agent)
                .options(selectinload(Agent.roles))
                .where(Agent.id == execution.baseline_agent_id)
            )
        ).scalar_one_or_none()
        if baseline is None or not baseline.is_active:
            continue
        if await _entity_access_allowed(baseline, user, db):
            visible.add(execution.id)
    return visible


async def latest_results_for_agent(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """Per-logical-test latest simulation + recorded results with context."""
    agent = await _agent_or_404(db, user, agent_id)
    org_id = agent.organization_id or user.organization_id
    current = await current_test_versions(db, agent_id=agent.id, org_id=org_id)
    current.sort(key=lambda pair: (pair[0].name, str(pair[0].logical_test_id)))
    total = len(current)
    page = current[offset : offset + limit]
    if not page:
        return [], total

    case_ids = [row.id for row, _ in page]
    logical_by_case = {row.id: row.logical_test_id for row, _ in page}
    version_by_case = {row.id: row.version for row, _ in page}

    suite_ids = list({suite.id for _, suite in page})
    visible_executions = await _visible_execution_ids(
        db, user, suite_ids=suite_ids
    )
    sim_latest: dict[UUID, dict] = {}
    if visible_executions:
        sim_rows = (
            (
                await db.execute(
                    select(AgentEvaluationResult, AgentEvaluationExecution)
                    .join(
                        AgentEvaluationExecution,
                        AgentEvaluationExecution.id
                        == AgentEvaluationResult.execution_id,
                    )
                    .where(
                        AgentEvaluationResult.case_id.in_(case_ids),
                        AgentEvaluationResult.status.in_(TERMINAL_RESULT_STATUSES),
                        AgentEvaluationExecution.id.in_(visible_executions),
                    )
                    .order_by(desc(AgentEvaluationResult.created_at))
                )
            )
            .all()
        )
        for result, execution in sim_rows:
            # A result only describes the version it ran: match the current
            # version, otherwise the test changed since this run.
            if version_by_case.get(result.case_id) != result.case_version:
                continue
            logical = logical_by_case[result.case_id]
            if logical in sim_latest:
                continue
            sim_latest[logical] = {
                "execution_id": execution.id,
                "case_version": result.case_version,
                "profile_id": execution.profile_id,
                "candidate_id": execution.candidate_id,
                "status": result.status,
                "created_at": result.created_at,
            }

    evaluations = (
        (
            await db.execute(
                select(AgentRecordedEvaluation).where(
                    AgentRecordedEvaluation.agent_id == agent.id,
                    AgentRecordedEvaluation.org_id.is_not_distinct_from(org_id),
                )
            )
        )
        .scalars()
        .all()
    )
    if not user.is_superuser:
        evaluations = [
            row for row in evaluations if row.org_id == user.organization_id
        ]
    # Canonical recorded readability (requester ownership + live source-run
    # visibility) applies per evaluation; failures are omitted, never fatal.
    from fastapi import HTTPException

    from shared.agent_recorded_admission import assert_recorded_evaluation_readable

    readable_ids: set[UUID] = set()
    eval_by_id = {}
    for row in evaluations:
        try:
            await assert_recorded_evaluation_readable(db, evaluation=row, user=user)
        except HTTPException:
            continue
        readable_ids.add(row.id)
        eval_by_id[row.id] = row
    eval_ids = list(readable_ids)
    rec_latest: dict[UUID, dict] = {}
    if eval_ids:
        rec_rows = (
            (
                await db.execute(
                    select(AgentRecordedEvaluationResult)
                    .where(
                        AgentRecordedEvaluationResult.evaluation_id.in_(eval_ids),
                        AgentRecordedEvaluationResult.complete.is_(True),
                    )
                    .order_by(desc(AgentRecordedEvaluationResult.created_at))
                )
            )
            .scalars()
            .all()
        )
        case_lookup = {row.id: row for row, _ in page}
        for result in rec_rows:
            case = case_lookup.get(result.case_id)
            if case is None or case.version != result.case_version:
                continue
            logical = case.logical_test_id
            if logical in rec_latest:
                continue
            evaluation = eval_by_id[result.evaluation_id]
            frozen = (
                evaluation.frozen_input
                if isinstance(evaluation.frozen_input, dict)
                else {}
            )
            rec_latest[logical] = {
                "evaluation_id": evaluation.id,
                "run_id": result.run_id,
                "case_version": result.case_version,
                "outcome": result.outcome,
                "applicability": result.applicability,
                "judge_mode": frozen.get("judge_mode"),
                "created_at": result.created_at,
            }

    items = []
    for row, _suite in page:
        logical = row.logical_test_id
        items.append(
            {
                "logical_test_id": logical,
                "version": row.version,
                "origin_suite_name": _suite.name,
                "simulation": sim_latest.get(logical),
                "recorded": rec_latest.get(logical),
            }
        )
    return items, total
