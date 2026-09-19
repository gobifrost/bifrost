"""``agent.evaluation_suite`` PlatformJob: durable baseline/candidate fan-out.

Payload v1 carries the execution ID only; frozen suite/candidate references
live on the durable feature rows. The handler dispatches a bounded batch of
synthetic AgentRuns, reports progress, then raises ``PlatformJobDeferred``
so the scheduler slot is released. Agent terminal events (and the
reconciler below, which closes event-before-wait and lost-notification
races) update case results idempotently and dispatch the next batch. When
every result is terminal the deferred job finishes through the shared
service. Cancellation cancels only unfinished synthetic AgentRuns.

No Studio polling/status transports are added: status, notification,
retry, and cancellation all flow through the shared PlatformJob contract.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobDeferred,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.services.agent_evaluations.executions import (
    EVALUATION_CONCURRENCY_DEFAULT,
    EVALUATION_CONCURRENCY_MAX,
    TERMINAL_RESULT_STATUSES,
    apply_terminal_event,
    finalize_execution,
    next_batch,
    plan_work_items,
    unfinished_run_ids,
)

JOB_TYPE = "agent.evaluation_suite"
PAYLOAD_VERSION = 1


class AgentEvaluationSuitePayload(BaseModel):
    execution_id: UUID


async def _load_execution(db, execution_id: UUID):
    from src.models.orm.agent_evaluations import AgentEvaluationExecution

    execution = await db.get(AgentEvaluationExecution, execution_id)
    if execution is None:
        raise PlatformJobFailure(
            "execution_not_found",
            f"Evaluation execution {execution_id} does not exist.",
            retryable=False,
        )
    return execution


async def _load_results(db, execution_id: UUID) -> list:
    from src.models.orm.agent_evaluations import AgentEvaluationResult

    rows = (
        await db.execute(
            select(AgentEvaluationResult).where(
                AgentEvaluationResult.execution_id == execution_id
            )
        )
    ).scalars().all()
    return list(rows)


async def _dispatch_case_run(
    db,
    *,
    execution,
    item: dict[str, Any],
) -> UUID:
    """Admit one synthetic AgentRun row and publish its queue nudge."""
    from src.jobs.rabbitmq import publish_message
    from src.models.orm.agent_evaluations import (
        AgentEvaluationCase,
        AgentEvaluationExecution,
    )
    from src.services.agent_evaluations.runner import (
        admit_synthetic_run,
        build_synthetic_correlation,
    )

    case = await db.get(AgentEvaluationCase, UUID(str(item["case_id"])))
    if case is None:
        raise PlatformJobFailure(
            "case_not_found", f"Case {item['case_id']} no longer exists.",
            retryable=False,
        )
    execution_row: AgentEvaluationExecution = execution
    candidate_snapshot: dict[str, Any] | None = None
    agent_id = execution_row.baseline_agent_id
    if item["side"] == "candidate":
        from src.models.orm.agent_evaluations import AgentCandidateSnapshot

        candidate = await db.get(
            AgentCandidateSnapshot, execution_row.candidate_id
        )
        if candidate is None:
            raise PlatformJobFailure(
                "candidate_not_found", "Candidate snapshot no longer exists.",
                retryable=False,
            )
        candidate_snapshot = dict(candidate.snapshot)
        agent_id = candidate.base_agent_id
    else:
        candidate_snapshot = None
    if candidate_snapshot is None:
        if item["side"] == "candidate":
            raise PlatformJobFailure(
                "candidate_not_found", "Candidate snapshot is missing.",
                retryable=False,
            )
        from src.models.orm.agents import Agent

        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
        if agent is None:
            raise PlatformJobFailure(
                "agent_not_found", "Baseline agent no longer exists.",
                retryable=False,
            )
        from src.services.agent_runtime.execution_snapshot import snapshot_agent

        live_snapshot = await snapshot_agent(db, agent)
        candidate_snapshot = {
            **live_snapshot,
            "evaluation": {
                "mode": "evaluation_synthetic",
                "evaluation_only": True,
                "candidate_hash": None,
                "case_input_hash": None,
            },
        }
    correlation = build_synthetic_correlation(
        suite_id=execution_row.suite_id,
        case_id=case.id,
        execution_id=execution_row.id,
        side=item["side"],
        candidate_id=execution_row.candidate_id,
        repetition_index=item["repetition_index"],
    )
    run = await admit_synthetic_run(
        db,
        candidate_snapshot=candidate_snapshot,
        case_input=dict(case.input or {}),
        output_schema=case.output_schema,
        correlation=correlation,
        agent_id=agent_id,
    )
    await db.commit()
    await publish_message("agent-runs", {"run_id": str(run.id)})
    return run.id


def _started_keys(results: list) -> set[tuple]:
    keys = set()
    for result in results:
        for side, run_id in (
            ("baseline", result.baseline_run_id),
            ("candidate", result.candidate_run_id),
        ):
            if run_id is not None:
                keys.add(
                    (
                        str(result.case_id),
                        result.case_version,
                        result.repetition_index,
                        side,
                    )
                )
    return keys


def _in_flight(results: list) -> int:
    count = 0
    for result in results:
        if result.status in TERMINAL_RESULT_STATUSES:
            continue
        count += sum(
            1
            for run_id in (result.baseline_run_id, result.candidate_run_id)
            if run_id is not None
        )
    return count


async def run_agent_evaluation_suite(
    context: PlatformJobContext,
    payload: AgentEvaluationSuitePayload,
) -> dict | None:
    from src.core.database import get_db_context

    async with get_db_context() as db:
        execution = await _load_execution(db, payload.execution_id)
        if execution.status in ("succeeded", "failed", "cancelled"):
            return {
                "execution_id": str(execution.id),
                "status": execution.status,
                "reused": True,
            }
        execution.status = "running"
        results = await _load_results(db, payload.execution_id)
        planned = plan_work_items(
            [
                {
                    "id": str(r.case_id),
                    "version": r.case_version,
                    "position": 0,
                    "enabled": True,
                    "accepted": True,
                    "repetitions": 1,
                }
                for r in results
            ],
            include_candidate=execution.candidate_id is not None,
        )
        ceiling = min(EVALUATION_CONCURRENCY_DEFAULT, EVALUATION_CONCURRENCY_MAX)
        batch = next_batch(planned, _started_keys(results), _in_flight(results), ceiling)
        await context.report(
            "Dispatching synthetic runs",
            current=execution.completed_cases,
            total=execution.total_cases or len(results),
        )
        for item in batch:
            run_id = await _dispatch_case_run(db, execution=execution, item=item)
            for result in results:
                if (
                    str(result.case_id) == item["case_id"]
                    and result.repetition_index == item["repetition_index"]
                ):
                    field = (
                        "baseline_run_id"
                        if item["side"] == "baseline"
                        else "candidate_run_id"
                    )
                    if getattr(result, field) is None:
                        setattr(result, field, run_id)
                        if result.status == "pending":
                            result.status = "running"
            await db.commit()
        await context.report(
            "Waiting for synthetic runs",
            current=execution.completed_cases,
            total=execution.total_cases or len(results),
        )
    raise PlatformJobDeferred(
        "Waiting for synthetic agent runs",
        {"execution_id": str(payload.execution_id)},
    )


async def apply_synthetic_terminal(
    execution_id: UUID,
    *,
    side: str,
    case_id: UUID,
    repetition_index: int,
    run_id: UUID,
    status: str,
    evidence: dict[str, Any] | None = None,
) -> bool:
    """Record one terminal synthetic run; dispatch follow-ups; maybe finish."""
    from src.core.database import get_db_context
    from src.services.platform_jobs import finish_deferred_platform_job

    async with get_db_context() as db:
        execution = await _load_execution(db, execution_id)
        results = await _load_results(db, execution_id)
        target = next(
            (
                r
                for r in results
                if r.case_id == case_id and r.repetition_index == repetition_index
            ),
            None,
        )
        if target is None:
            return False
        advanced = apply_terminal_event(
            target, side=side, run_id=run_id, status=status,
            evidence=evidence or {},
            expects_candidate=execution.candidate_id is not None,
        )
        if not advanced:
            return False
        await db.commit()
        summary = finalize_execution(execution, results)
        await db.commit()
        if summary["completed"] < summary["total"]:
            planned = plan_work_items(
                [
                    {
                        "id": str(r.case_id),
                        "version": r.case_version,
                        "position": 0,
                        "enabled": True,
                        "accepted": True,
                        "repetitions": 1,
                    }
                    for r in results
                ],
                include_candidate=execution.candidate_id is not None,
            )
            for item in next_batch(
                planned,
                _started_keys(results),
                _in_flight(results),
                EVALUATION_CONCURRENCY_DEFAULT,
            ):
                run_id = await _dispatch_case_run(db, execution=execution, item=item)
                for result in results:
                    if (
                        str(result.case_id) == item["case_id"]
                        and result.repetition_index == item["repetition_index"]
                    ):
                        field = (
                            "baseline_run_id"
                            if item["side"] == "baseline"
                            else "candidate_run_id"
                        )
                        if getattr(result, field) is None:
                            setattr(result, field, run_id)
                await db.commit()
            return True
        await finish_deferred_platform_job(
            execution.platform_job_id,
            status="succeeded" if summary["failed"] == 0 else "failed",
            result={"execution_id": str(execution_id), **summary},
        )
        return True


async def cancel_evaluation_execution(execution_id: UUID) -> int:
    """Cancel unfinished synthetic AgentRuns for an execution."""
    from datetime import datetime, timezone

    from src.core.database import get_db_context
    from src.models.orm.agent_runs import AgentRun

    async with get_db_context() as db:
        execution = await _load_execution(db, execution_id)
        results = await _load_results(db, execution_id)
        run_ids = unfinished_run_ids(results)
        for run_id in run_ids:
            run = await db.get(AgentRun, run_id)
            if run is not None and run.status not in (
                "completed", "failed", "cancelled", "timeout",
                "budget_exceeded", "contract_failed", "recovery_required",
            ):
                run.status = "cancelled"
                run.completed_at = datetime.now(timezone.utc)
        execution.status = "cancelled"
        await db.commit()
        return len(run_ids)


async def reconcile_agent_evaluation_jobs() -> int:
    """Heal event-before-wait and lost-notification races.

    For every active execution, terminal synthetic runs not yet applied are
    applied idempotently, the next bounded batch dispatches, and fully
    terminal executions finish their deferred PlatformJob.
    """
    from src.core.database import get_db_context
    from src.models.orm.agent_evaluations import AgentEvaluationExecution
    from src.models.orm.agent_runs import AgentRun
    from src.services.platform_jobs import finish_deferred_platform_job

    healed = 0
    async with get_db_context() as db:
        active = (
            await db.execute(
                select(AgentEvaluationExecution).where(
                    AgentEvaluationExecution.status.in_(
                        ("queued", "running", "waiting")
                    )
                )
            )
        ).scalars().all()
        for execution in active:
            results = await _load_results(db, execution.id)
            for result in results:
                for side, run_id in (
                    ("baseline", result.baseline_run_id),
                    ("candidate", result.candidate_run_id),
                ):
                    if run_id is None:
                        continue
                    run = await db.get(AgentRun, run_id)
                    if run is None or run.status in (
                        "queued", "running", "waiting_child",
                        "waiting_children", "sleeping",
                    ):
                        continue
                    evidence = {
                        "terminal_status": run.status,
                        "output": run.output,
                        "tool_calls": [],
                        "simulator_state": {},
                        "delegation": {"children": []},
                        "usage": {
                            "iterations": run.iterations_used,
                            "tokens": run.tokens_used,
                        },
                        "real_tool_executions": 0,
                        "output_schema": run.output_schema,
                    }
                    if apply_terminal_event(
                        result, side=side, run_id=run_id,
                        status=run.status, evidence=evidence,
                        expects_candidate=execution.candidate_id is not None,
                    ):
                        healed += 1
            summary = finalize_execution(execution, results)
            if summary["completed"] >= summary["total"] and summary["total"] > 0:
                await finish_deferred_platform_job(
                    execution.platform_job_id,
                    status="succeeded" if summary["failed"] == 0 else "failed",
                    result={"execution_id": str(execution.id), **summary},
                )
            await db.commit()
    return healed


AGENT_EVALUATION_SUITE_DEFINITION = PlatformJobDefinition(
    job_type=JOB_TYPE,
    payload_version=PAYLOAD_VERSION,
    payload_model=AgentEvaluationSuitePayload,
    handler=run_agent_evaluation_suite,
    policy=PlatformJobPolicy(
        timeout_seconds=30 * 60,
        max_attempts=2,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=128,
        allow_running_cancellation=True,
    ),
)
