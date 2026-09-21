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

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

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
    apply_terminal_event,
    finalize_execution,
    next_batch,
    plan_result_work_items,
)

JOB_TYPE = "agent.evaluation_suite"
PAYLOAD_VERSION = 1
DESIGNER_RECONCILIATION_BATCH = 50

logger = logging.getLogger(__name__)


class AgentEvaluationSuitePayload(BaseModel):
    execution_id: UUID


def _mark_cancellation_pending(execution) -> None:
    """Fence Studio admission while runtime cancellation drains run trees."""
    execution.status = "cancelled"
    # ``NULL`` is intentional: the reconciler resumes this durable cleanup
    # after a crash until every root/descendant cancellation was requested.
    execution.completed_at = None


async def _shared_job_cancel_requested(db, execution) -> bool:
    """Lock and inspect the canonical PlatformJob cancellation state."""
    if execution.platform_job_id is None:
        return False
    from src.models.orm.platform_jobs import PlatformJob

    job = await db.scalar(
        select(PlatformJob)
        .where(PlatformJob.id == execution.platform_job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return job is not None and (
        job.cancel_requested_at is not None
        or job.status in ("cancel_requested", "cancelled")
    )


async def _load_results(db, execution_id: UUID, *, lock: bool = False) -> list:
    from src.models.orm.agent_evaluations import AgentEvaluationResult

    statement = select(AgentEvaluationResult).where(
        AgentEvaluationResult.execution_id == execution_id
    ).execution_options(populate_existing=lock)
    if lock:
        statement = statement.with_for_update()
    rows = (await db.execute(statement)).scalars().all()
    return list(rows)


async def _dispatch_case_run(
    db,
    *,
    execution,
    result,
    item: dict[str, Any],
) -> UUID | None:
    """Atomically fence one result side, then publish its synthetic run.

    The execution and result rows are held ``FOR UPDATE`` by both callers.
    Persisting the result-side run id with the run/session makes retry and a
    racing terminal event harmless: neither can admit a second side.
    """
    from src.jobs.rabbitmq import publish_message
    from src.models.orm.agent_evaluations import (
        AgentEvaluationExecution,
        AgentEvaluationResult,
        AgentEvaluationSuite,
        AgentSimulationSession,
    )
    from src.services.agent_evaluations.runner import (
        admit_synthetic_run,
        build_synthetic_correlation,
    )

    result_id = result.id
    execution = await db.scalar(
        select(AgentEvaluationExecution)
        .where(AgentEvaluationExecution.id == execution.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if execution is None or execution.status == "cancelled":
        return None
    if await _shared_job_cancel_requested(db, execution):
        _mark_cancellation_pending(execution)
        await db.commit()
        return None
    result = await db.scalar(
        select(AgentEvaluationResult)
        .where(AgentEvaluationResult.id == result_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if result is None:
        raise PlatformJobFailure("result_not_found", "Evaluation result was deleted.", retryable=False)

    run_field = "baseline_run_id" if item["side"] == "baseline" else "candidate_run_id"
    existing_run_id = getattr(result, run_field)
    if existing_run_id is not None:
        await _publish_if_active(db, execution.id, existing_run_id, publish_message)
        return existing_run_id
    case = next(
        (definition for definition in (execution.case_definitions or [])
         if definition.get("id") == str(item["case_id"])
         and definition.get("version") == item["case_version"]),
        None,
    )
    if case is None:
        raise PlatformJobFailure(
            "case_not_found", f"Case {item['case_id']} no longer exists.",
            retryable=False,
        )
    execution_row: AgentEvaluationExecution = execution
    candidate_snapshot: dict[str, Any]
    if item["side"] == "candidate":
        candidate_snapshot = dict(execution_row.candidate_snapshot or {})
    else:
        candidate_snapshot = dict(execution_row.baseline_snapshot or {})
    if not candidate_snapshot:
        raise PlatformJobFailure(
            "missing_snapshot",
            f"Execution has no frozen {item['side']} snapshot.",
            retryable=False,
        )
    try:
        agent_id = UUID(str(candidate_snapshot["agent_id"]))
    except (KeyError, ValueError) as exc:
        raise PlatformJobFailure(
            "invalid_snapshot", "Execution has an invalid frozen Agent snapshot.", retryable=False
        ) from exc
    suite = await db.get(AgentEvaluationSuite, execution_row.suite_id)
    if suite is None:
        raise PlatformJobFailure("suite_not_found", "Evaluation suite was deleted.", retryable=False)
    correlation = build_synthetic_correlation(
        suite_id=execution_row.suite_id,
        case_id=UUID(str(case["id"])),
        execution_id=execution_row.id,
        side=item["side"],
        candidate_id=execution_row.candidate_id,
        repetition_index=item["repetition_index"],
    )
    run = await admit_synthetic_run(
        db,
        candidate_snapshot=candidate_snapshot,
        case_input=dict(case.get("input") or {}),
        output_schema=case.get("output_schema"),
        correlation=correlation,
        agent_id=agent_id,
        org_id=suite.org_id,
    )
    from src.services.agent_evaluations.simulator_models import canonical_hash, fresh_state
    tool_schemas = {
        tool["name"]: dict(tool.get("parameters") or {})
        for tool in candidate_snapshot.get("tools", [])
    }
    fixture = dict(case.get("fixture") or {})
    db.add(AgentSimulationSession(
        execution_id=execution_row.id,
        result_id=result.id,
        case_id=UUID(str(case["id"])),
        case_version=case["version"], side=item["side"],
        run_id=run.id, root_run_id=run.id,
        fixture=fixture, tool_schemas=tool_schemas,
        state=fresh_state(fixture),
        initial_state_hash=canonical_hash(fixture.get("entities", {})),
    ))
    setattr(result, run_field, run.id)
    if result.status == "pending":
        result.status = "running"
    await db.commit()
    await _publish_if_active(db, execution.id, run.id, publish_message)
    return run.id


async def _publish_if_active(db, execution_id: UUID, run_id: UUID, publisher) -> None:
    """Publish only while cancellation cannot win the dispatch race.

    The admitted run/result fence is committed before broker publication.  A
    second, short execution lock closes the cancellation window: cancellation
    either marks the persisted run first (no publish), or waits until this
    nudge is issued and then cancels the queued/running run normally.
    """
    from src.models.orm.agent_evaluations import AgentEvaluationExecution
    from src.models.orm.agent_runs import AgentRun

    execution = await db.scalar(
        select(AgentEvaluationExecution)
        .where(AgentEvaluationExecution.id == execution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    run = await db.get(AgentRun, run_id)
    if execution is None or run is None:
        await db.commit()
        return
    if execution.status == "cancelled" or await _shared_job_cancel_requested(
        db, execution
    ):
        _mark_cancellation_pending(execution)
        await db.commit()
        return
    if run.status == "queued":
        await publisher("agent-runs", {"run_id": str(run.id)})
    await db.commit()


def _result_for_item(results: list, item: dict[str, Any]):
    for result in results:
        if (
            str(result.case_id) == item["case_id"]
            and result.case_version == item["case_version"]
            and result.repetition_index == item["repetition_index"]
        ):
            return result
    raise PlatformJobFailure("result_not_found", "Evaluation work item has no result row.", retryable=False)


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


async def _in_flight_count(db, results: list) -> int:
    """Count live admitted sides from durable AgentRun state, not stale rows."""
    from src.models.orm.agent_runs import AgentRun
    from src.services.agent_runtime import types as runtime_types

    run_ids = [
        run_id
        for result in results
        for run_id in (result.baseline_run_id, result.candidate_run_id)
        if run_id is not None
    ]
    if not run_ids:
        return 0
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.id.in_(run_ids),
                AgentRun.status.not_in(runtime_types.TERMINAL_STATUSES),
            )
        )
        or 0
    )


async def _dispatch_follow_up_batch(db, execution, results: list) -> int:
    """Admit the next durable result sides after a scheduler reconciliation."""
    planned = plan_result_work_items(
        results, include_candidate=execution.candidate_id is not None
    )
    batch = next_batch(
        planned,
        _started_keys(results),
        await _in_flight_count(db, results),
        EVALUATION_CONCURRENCY_DEFAULT,
    )
    admitted = 0
    for item in batch:
        run_id = await _dispatch_case_run(
            db,
            execution=execution,
            result=_result_for_item(results, item),
            item=item,
        )
        if execution.status == "cancelled":
            break
        if run_id is not None:
            admitted += 1
    return admitted


async def _process_semantic_postprocessing(db, execution, results: list) -> bool:
    """Recover/enqueue semantic child jobs; return True while child work is active."""
    from shared.agent_synthetic_judge import (
        enqueue_synthetic_semantic_judge_job,
        has_semantic_pending,
        reconcile_synthetic_semantic_child,
        semantic_child_job_id,
        synthetic_semantic_child_active,
    )

    active = False
    for result in results:
        if await reconcile_synthetic_semantic_child(db, result):
            continue
        if await synthetic_semantic_child_active(db, result):
            active = True
            continue
        if has_semantic_pending(result):
            try:
                linked_child_id = semantic_child_job_id(result)
            except ValueError:
                await reconcile_synthetic_semantic_child(db, result)
                continue
            if linked_child_id is None:
                child = await enqueue_synthetic_semantic_judge_job(
                    db, execution=execution, result=result
                )
                if child is not None:
                    active = True
            elif await synthetic_semantic_child_active(db, result):
                active = True
    return active


async def run_agent_evaluation_suite(
    context: PlatformJobContext,
    payload: AgentEvaluationSuitePayload,
) -> dict | None:
    from src.core.database import get_db_context
    from src.models.orm.agent_evaluations import AgentEvaluationExecution

    cancellation_pending = False
    already_cancelled = False
    async with get_db_context() as db:
        execution = await db.scalar(
            select(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == payload.execution_id
            ).with_for_update()
            .execution_options(populate_existing=True)
        )
        if execution is None:
            raise PlatformJobFailure("execution_not_found", "Evaluation execution does not exist.", retryable=False)
        if execution.status in ("succeeded", "failed"):
            return {
                "execution_id": str(execution.id),
                "status": execution.status,
                "reused": True,
            }
        if execution.status == "cancelled":
            already_cancelled = True
            cancellation_pending = execution.completed_at is None
        elif await _shared_job_cancel_requested(db, execution):
            _mark_cancellation_pending(execution)
            await db.commit()
            cancellation_pending = True
        else:
            # The shared-job probe holds the PlatformJob row lock. Release it
            # before context.report opens its own fenced PlatformJob session.
            await db.commit()

            # ``autoflush=False`` means a dirty ``running`` assignment could
            # otherwise survive the unlock and be flushed after a concurrent
            # canceller has fenced the execution. Re-lock from the database,
            # re-check the canonical job, and durably record ``running``
            # before any later refresh/report/dispatch work.
            execution = await db.scalar(
                select(AgentEvaluationExecution)
                .where(AgentEvaluationExecution.id == payload.execution_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if execution is None:
                raise PlatformJobFailure(
                    "execution_not_found",
                    "Evaluation execution does not exist.",
                    retryable=False,
                )
            if execution.status in ("succeeded", "failed"):
                return {
                    "execution_id": str(execution.id),
                    "status": execution.status,
                    "reused": True,
                }
            if execution.status == "cancelled":
                already_cancelled = True
                cancellation_pending = execution.completed_at is None
            elif await _shared_job_cancel_requested(db, execution):
                _mark_cancellation_pending(execution)
                await db.commit()
                cancellation_pending = True
            else:
                execution.status = "running"
                await db.commit()
        if not (cancellation_pending or already_cancelled):
            results = await _load_results(db, payload.execution_id, lock=True)
            planned = plan_result_work_items(
                results, include_candidate=execution.candidate_id is not None
            )
            ceiling = min(EVALUATION_CONCURRENCY_DEFAULT, EVALUATION_CONCURRENCY_MAX)
            batch = next_batch(
                planned,
                _started_keys(results),
                await _in_flight_count(db, results),
                ceiling,
            )
            await context.report(
                "Dispatching synthetic runs",
                current=execution.completed_cases,
                total=execution.total_cases or len(results),
            )
            for item in batch:
                await _dispatch_case_run(
                    db,
                    execution=execution,
                    result=_result_for_item(results, item),
                    item=item,
                )
                if execution.status == "cancelled":
                    cancellation_pending = True
                    break
            if not cancellation_pending:
                await context.report(
                    "Waiting for synthetic runs",
                    current=execution.completed_cases,
                    total=execution.total_cases or len(results),
                )
    if cancellation_pending or already_cancelled:
        if cancellation_pending:
            await cancel_evaluation_execution(payload.execution_id)
        from src.jobs.platform.base import PlatformJobCancelled

        raise PlatformJobCancelled
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
    from src.models.orm.agent_evaluations import AgentEvaluationExecution
    from src.services.platform_jobs import finish_deferred_platform_job

    async with get_db_context() as db:
        execution = await db.scalar(
            select(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == execution_id
            ).with_for_update()
            .execution_options(populate_existing=True)
        )
        if execution is None or execution.status == "cancelled":
            return False
        if await _shared_job_cancel_requested(db, execution):
            _mark_cancellation_pending(execution)
            await db.commit()
            await cancel_evaluation_execution(execution_id)
            return False
        results = await _load_results(db, execution_id, lock=True)
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
        # Never trust a process-local callback payload for a durable result.
        # The scheduler and this callback share the exact persisted evidence
        # projection so restarts cannot silently change assertion inputs.
        del evidence, status
        from src.services.agent_evaluations.evidence import (
            EvaluationEvidenceError,
            load_persisted_evaluation_evidence,
        )

        try:
            persisted_evidence = await load_persisted_evaluation_evidence(db, run_id)
        except EvaluationEvidenceError as exc:
            logger.warning(
                "Synthetic terminal evidence is unavailable for %s", run_id,
                exc_info=True,
            )
            persisted_evidence = {"terminal_status": "error", "evidence_error": str(exc)}
        advanced = apply_terminal_event(
            target,
            side=side,
            run_id=run_id,
            status=persisted_evidence["terminal_status"],
            evidence=persisted_evidence,
            expects_candidate=execution.candidate_id is not None,
        )
        if not advanced:
            await _process_semantic_postprocessing(db, execution, results)
            await db.commit()
            return False
        semantic_active = await _process_semantic_postprocessing(db, execution, results)
        await db.commit()
        # The first commit makes the terminal delivery durable. Reacquire the
        # execution/result fence before finalizing or admitting follow-ups so
        # a concurrent cancellation cannot be overwritten by a stale object.
        execution = await db.scalar(
            select(AgentEvaluationExecution)
            .where(AgentEvaluationExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if execution is None or execution.status == "cancelled":
            return True
        if await _shared_job_cancel_requested(db, execution):
            _mark_cancellation_pending(execution)
            await db.commit()
            await cancel_evaluation_execution(execution_id)
            return True
        results = await _load_results(db, execution_id, lock=True)
        semantic_active = await _process_semantic_postprocessing(db, execution, results)
        if semantic_active:
            await db.commit()
            await _dispatch_follow_up_batch(db, execution, results)
            if execution.status == "cancelled":
                await cancel_evaluation_execution(execution_id)
            return True
        summary = finalize_execution(execution, results)
        await db.commit()
        if summary["completed"] < summary["total"]:
            await _dispatch_follow_up_batch(db, execution, results)
            if execution.status == "cancelled":
                await cancel_evaluation_execution(execution_id)
            return True
        await finish_deferred_platform_job(
            execution.platform_job_id,
            status="succeeded" if summary["failed"] == 0 else "failed",
            result={"execution_id": str(execution_id), **summary},
        )
        return True


async def cancel_evaluation_execution(execution_id: UUID) -> int:
    """Request fenced cancellation for unfinished synthetic run trees."""
    from datetime import datetime, timezone

    from src.core.cache.redis_client import get_redis
    from src.core.redis_client import get_redis_client
    from src.core.database import get_db_context, get_session_factory
    from src.models.orm.agent_evaluations import AgentEvaluationExecution
    from src.models.orm.agent_runs import AgentRun
    from src.services.agent_runtime.delegation import (
        cascade_cancel,
        notify_parent_of_completion,
    )
    from src.services.agent_runtime import run_store
    from src.services.agent_runtime import types as runtime_types

    # The runtime owns AgentRun cancellation: it carries lease fencing,
    # completion outbox, parent wake, cancellation signal, and descendants.
    # Studio first snapshots its roots, then invokes that shared transition
    # for every run in each tree before terminalizing its own projection.
    semantic_child_ids: list[UUID] = []
    async with get_db_context() as db:
        execution = await db.scalar(
            select(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == execution_id
            ).with_for_update()
            .execution_options(populate_existing=True)
        )
        if execution is None:
            return 0
        if execution.status in ("succeeded", "failed"):
            return 0
        results = await _load_results(db, execution_id, lock=True)
        root_run_ids = list(
            dict.fromkeys(
                run_id
                for result in results
                for run_id in (result.baseline_run_id, result.candidate_run_id)
                if run_id is not None
            )
        )
        from shared.agent_synthetic_judge import valid_semantic_child_job_id_for_result

        semantic_child_ids = []
        for result in results:
            try:
                child_id = await valid_semantic_child_job_id_for_result(db, result)
            except ValueError:
                child_id = None
            if child_id is not None:
                semantic_child_ids.append(child_id)
        # Fence admission before releasing the execution lock. A crashing
        # cancellation resumes from this durable ``cancelled + NULL`` marker.
        _mark_cancellation_pending(execution)
        await db.commit()

    if not root_run_ids:
        async with get_db_context() as db:
            execution = await db.scalar(
                select(AgentEvaluationExecution)
                .where(AgentEvaluationExecution.id == execution_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if execution is not None and execution.status == "cancelled":
                execution.completed_at = datetime.now(timezone.utc)
                await db.commit()
        return 0

    for child_id in semantic_child_ids:
        async with get_db_context() as db:
            from src.models.orm.platform_jobs import PlatformJob
            from src.services.platform_jobs import request_platform_job_cancel
            child = await db.get(PlatformJob, child_id)
            if child is not None:
                await request_platform_job_cancel(db, child)

    requested = 0
    cleanup_complete = True
    for run_id in root_run_ids:
        run = None
        async with get_db_context() as db:
            try:
                run = await run_store.request_cancellation(
                    db, run_id, error="Evaluation execution cancelled"
                )
            except runtime_types.InvalidTransitionError:
                # A terminal result root can still own live descendants, so
                # it must continue through the root-scoped cascade below.
                run = await db.get(AgentRun, run_id)
            if run is not None and run.status not in runtime_types.TERMINAL_STATUSES:
                requested += 1
        if run is None:
            # The root was deleted (and its descendants cascaded) after the
            # result snapshot. Nothing remains for this root to cancel.
            continue
        # Cascade only from the requested result root. ``root_run_id`` is an
        # observability grouping, not a cancellation scope: using it would
        # cancel a sibling result side. The runtime helper owns descendant
        # locks, worker cancel flags, and parent wakeups.
        if run.status == runtime_types.CANCELLING:
            try:
                await get_redis_client().set_agent_run_cancel_flag(str(run.id))
            except Exception:
                # The state transition is already durable. Keep the shared
                # cascade attempt below (which also writes running-child
                # flags) instead of turning a transient cache outage into a
                # failed Studio cancellation request.
                logger.exception("Evaluation cancel flag failed for %s", run.id)
                cleanup_complete = False
        try:
            async with get_redis() as redis:
                await cascade_cancel(get_session_factory(), redis, run.id)
        except Exception:
            logger.exception("Evaluation descendant cancellation failed for %s", run.id)
            cleanup_complete = False
        if run.status == runtime_types.CANCELLED:
            try:
                await notify_parent_of_completion(get_session_factory(), run.id)
            except Exception:
                logger.exception("Evaluation parent wake failed for %s", run.id)
                cleanup_complete = False

    async with get_db_context() as db:
        execution = await db.scalar(
            select(AgentEvaluationExecution)
            .where(AgentEvaluationExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if execution is None or execution.status in ("succeeded", "failed"):
            return requested
        if execution.status == "cancelled" and cleanup_complete:
            execution.completed_at = datetime.now(timezone.utc)
            await db.commit()
    return requested


async def reconcile_agent_evaluation_jobs() -> int:
    """Heal event-before-wait and lost-notification races.

    For every active execution, terminal synthetic runs not yet applied are
    applied idempotently, the next bounded batch dispatches, and fully
    terminal executions finish their deferred PlatformJob.
    """
    from src.core.database import get_db_context
    from src.core.pubsub import publish_agent_run_update
    from src.models.orm.agents import Agent
    from src.models.orm.agent_evaluations import AgentEvaluationExecution
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.platform_jobs import finish_deferred_platform_job

    healed = 0
    cancellation_ids: set[UUID] = set()
    async with get_db_context() as db:
        # Designer runs are ordinary synthetic AgentRuns; materialize their
        # contract-validated output into drafts on the same scheduler pass.
        from src.models.orm.agent_runs import AgentRun
        from src.services.agent_evaluations.test_designer import materialize_designer_drafts
        designer_run_ids = (
            await db.scalars(
                select(AgentRun.id)
                .where(
                    AgentRun.trigger_type == "evaluation_synthetic",
                    AgentRun.status == "completed",
                    AgentRun.correlation["evaluation_designer"].as_boolean().is_(True),
                    AgentRun.correlation["designer_materialized"].as_boolean().is_not(True),
                )
                .order_by(AgentRun.completed_at, AgentRun.id)
                .limit(DESIGNER_RECONCILIATION_BATCH)
            )
        ).all()
        for designer_run_id in designer_run_ids:
            materialized_run = None
            agent_name = "Test Designer"
            try:
                # A malformed proposal must not poison the scheduler's outer
                # transaction or prevent the bounded evaluation sweep below.
                async with db.begin_nested():
                    # Claim and predicate the current row under the same lock:
                    # a concurrent reconciliation must see the committed
                    # materialization rather than infer a transition from a
                    # stale pre-lock identity map or a zero draft count.
                    materialized_run = await db.scalar(
                        select(AgentRun)
                        .where(
                            AgentRun.id == designer_run_id,
                            AgentRun.trigger_type == "evaluation_synthetic",
                            AgentRun.status == "completed",
                            AgentRun.correlation["evaluation_designer"].as_boolean().is_(True),
                            AgentRun.correlation["designer_materialized"].as_boolean().is_not(True),
                        )
                        .with_for_update(of=AgentRun, skip_locked=True)
                        .execution_options(populate_existing=True)
                    )
                    if materialized_run is None:
                        continue
                    draft_count = await materialize_designer_drafts(db, materialized_run)
                    if not (materialized_run.correlation or {}).get("designer_materialized"):
                        materialized_run = None
                        continue
                    if materialized_run.agent_id is not None:
                        agent_name = await db.scalar(
                            select(Agent.name).where(Agent.id == materialized_run.agent_id)
                        ) or agent_name
                # Materialization is its own committed boundary. The event is
                # only a UI hint, so publishing it must neither precede this
                # durable state nor let a pubsub outage break the sweep.
                if materialized_run is None:
                    continue
                await db.commit()
                healed += draft_count
                try:
                    await publish_agent_run_update(materialized_run, agent_name)
                except Exception:
                    logger.exception(
                        "Test Designer materialization update failed for synthetic run %s",
                        materialized_run.id,
                    )
            except Exception:
                # A failed commit can invalidate the outer transaction too.
                await db.rollback()
                # Leave the correlation unmarked so the next reconciliation
                # retries after an operator-visible run failure is repaired.
                logger.exception(
                    "Test Designer materialization failed for synthetic run %s",
                    designer_run_id,
                )
                continue
        active = (
            await db.execute(
                select(AgentEvaluationExecution)
                .outerjoin(PlatformJob, PlatformJob.id == AgentEvaluationExecution.platform_job_id)
                .where(or_(
                    AgentEvaluationExecution.status.in_(("queued", "running", "waiting")),
                    # Results may finish before the shared runner commits
                    # its deferred state, or a process may die between the
                    # evaluation and PlatformJob completion commits.
                    and_(
                        AgentEvaluationExecution.status.in_(("succeeded", "failed")),
                        PlatformJob.status == "waiting",
                    ),
                ))
            )
        ).scalars().all()
        cancellation_ids.update(
            (
                await db.scalars(
                    select(AgentEvaluationExecution.id).where(
                        AgentEvaluationExecution.status == "cancelled",
                        AgentEvaluationExecution.completed_at.is_(None),
                    )
                )
            ).all()
        )
        for stale_execution in active:
            execution = await db.scalar(
                select(AgentEvaluationExecution)
                .where(AgentEvaluationExecution.id == stale_execution.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if execution is None or execution.status == "cancelled":
                continue
            if await _shared_job_cancel_requested(db, execution):
                _mark_cancellation_pending(execution)
                cancellation_ids.add(execution.id)
                await db.commit()
                continue
            results = await _load_results(db, execution.id, lock=True)
            for result in results:
                for side, run_id in (
                    ("baseline", result.baseline_run_id),
                    ("candidate", result.candidate_run_id),
                ):
                    if run_id is None:
                        continue
                    run = await db.get(AgentRun, run_id)
                    from src.services.agent_runtime import types as runtime_types

                    if run is None or run.status not in runtime_types.TERMINAL_STATUSES:
                        continue
                    from src.services.agent_evaluations.evidence import (
                        EvaluationEvidenceError,
                        load_persisted_evaluation_evidence,
                    )

                    try:
                        evidence = await load_persisted_evaluation_evidence(
                            db, run.id
                        )
                    except EvaluationEvidenceError as exc:
                        logger.warning(
                            "Synthetic evidence unavailable while reconciling %s",
                            run.id,
                            exc_info=True,
                        )
                        evidence = {"terminal_status": "error", "evidence_error": str(exc)}
                    if apply_terminal_event(
                        result, side=side, run_id=run_id,
                        status=evidence["terminal_status"], evidence=evidence,
                        expects_candidate=execution.candidate_id is not None,
                    ):
                        healed += 1
            semantic_active = await _process_semantic_postprocessing(db, execution, results)
            if semantic_active:
                await db.commit()
                admitted = await _dispatch_follow_up_batch(db, execution, results)
                if execution.status == "cancelled":
                    cancellation_ids.add(execution.id)
                else:
                    healed += admitted
                continue
            summary = finalize_execution(execution, results)
            await db.commit()
            if await _shared_job_cancel_requested(db, execution):
                _mark_cancellation_pending(execution)
                cancellation_ids.add(execution.id)
                await db.commit()
                continue
            # finish_deferred_platform_job uses a separate session and locks
            # the same PlatformJob row. Do not retain the probe lock here.
            await db.commit()
            if summary["completed"] >= summary["total"] and summary["total"] > 0:
                await finish_deferred_platform_job(
                    execution.platform_job_id,
                    status="succeeded" if summary["failed"] == 0 else "failed",
                    result={"execution_id": str(execution.id), **summary},
                )
            elif summary["completed"] < summary["total"]:
                admitted = await _dispatch_follow_up_batch(db, execution, results)
                if execution.status == "cancelled":
                    cancellation_ids.add(execution.id)
                else:
                    healed += admitted
    for execution_id in cancellation_ids:
        try:
            healed += await cancel_evaluation_execution(execution_id)
        except Exception:
            logger.exception(
                "Evaluation cancellation reconciliation failed for %s", execution_id
            )
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
