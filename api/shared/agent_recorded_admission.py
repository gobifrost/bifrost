"""Admission and execution helpers for recorded agent evaluations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.agent_recorded_evaluation import (
    aggregate_recorded_evaluation,
    aggregate_recorded_pair,
    evaluate_recorded_assertions,
)
from shared.agent_recorded_evidence import (
    RecordedEvidenceError,
    RecordedEvidenceOversized,
    load_recorded_run_evidence,
)
from shared.models import (
    RecordedEvaluationCreate,
    RecordedEvaluationResultPublic,
    RecordedEvaluationResultsPage,
)
from shared.quality_usage import (
    begin_quality_usage_attempt,
    mark_quality_usage_unobserved,
    record_quality_usage_observation,
)
from src.core.database import get_db_context
from src.core.principal import UserPrincipal
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobFailure,
)
from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite
from src.models.orm.agent_recorded_evaluations import (
    AgentRecordedEvaluation,
    AgentRecordedEvaluationResult,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.platform_jobs import PlatformJob
from shared.agent_recorded_judge import (
    ambiguous_outcome,
    assertion_hash,
    execute_recorded_semantic_judge,
    prepare_recorded_judge_request,
    started_outcome,
)
from src.services.execution.agent_run_access import agent_run_visibility_conditions
from src.services.platform_jobs import (
    enqueue_platform_job,
    lock_running_platform_job_for_result_write,
)
from src.services.model_pricing import canonical_provider

MAX_RECORDED_RUNS = 20
MAX_RECORDED_TESTS = 100
MAX_RECORDED_PAIRS = 1000
MAX_FROZEN_INPUT_BYTES = 4 * 1024 * 1024


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _hash(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _unique_ordered(ids: list[UUID]) -> list[UUID]:
    ordered: list[UUID] = []
    seen: set[UUID] = set()
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _collect_delegated_source_ids(evidence: dict[str, Any]) -> list[str]:
    source_ids: list[str] = []

    def visit(node: dict[str, Any]) -> None:
        run_id = node.get("run_id")
        if isinstance(run_id, str) and run_id not in source_ids:
            source_ids.append(run_id)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                visit(child)

    delegation = evidence.get("delegation") or {}
    for child in delegation.get("children") or []:
        if isinstance(child, dict):
            visit(child)
    return source_ids


async def _entity_access_allowed(
    entity: Any, user: UserPrincipal, db: AsyncSession
) -> bool:
    if user.is_superuser:
        return True
    if getattr(entity, "organization_id", None) not in (None, user.organization_id):
        return False
    raw_level = getattr(entity, "access_level", "authenticated")
    level = getattr(raw_level, "value", str(raw_level)).lower()
    if level == "everyone":
        return True
    if level == "authenticated":
        return not user.is_external
    if level == "private":
        return getattr(entity, "owner_user_id", None) == user.user_id
    if level == "role_based":
        from shared.role_cache import get_user_roles

        role_ids, _ = await get_user_roles(user.user_id, db)
        return bool(
            set(role_ids)
            & {getattr(role, "id", None) for role in (getattr(entity, "roles", []) or [])}
        )
    return False


async def _load_authorized_agent(
    db: AsyncSession, user: UserPrincipal, agent_id: UUID
) -> Agent:
    agent = (
        await db.execute(
            select(Agent)
            .options(selectinload(Agent.roles))
            .where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    if (
        agent is None
        or (not user.is_superuser and user.organization_id is None)
        or not await _entity_access_allowed(agent, user, db)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    if not user.is_superuser and agent.organization_id not in (None, user.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return agent


async def _load_cases(
    db: AsyncSession,
    *,
    agent_id: UUID,
    org_id: UUID | None,
    body: RecordedEvaluationCreate,
) -> list[AgentEvaluationCase]:
    if body.all_tests and body.case_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Specify either all_tests=true or case_ids, not both.",
        )
    if not body.all_tests and not body.case_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Specify case_ids or all_tests=true.",
        )
    query = (
        select(AgentEvaluationCase)
        .join(AgentEvaluationSuite, AgentEvaluationSuite.id == AgentEvaluationCase.suite_id)
        .where(
            AgentEvaluationSuite.agent_id == agent_id,
            AgentEvaluationSuite.org_id == org_id,
            AgentEvaluationSuite.status == "published",
            AgentEvaluationCase.enabled.is_(True),
            AgentEvaluationCase.accepted.is_(True),
        )
        .order_by(AgentEvaluationCase.position, AgentEvaluationCase.created_at)
    )
    if body.case_ids:
        requested = _unique_ordered(body.case_ids)
        query = query.where(AgentEvaluationCase.id.in_(requested))
    cases = list((await db.execute(query.limit(MAX_RECORDED_TESTS + 1))).scalars().all())
    if len(cases) > MAX_RECORDED_TESTS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Recorded evaluations support at most {MAX_RECORDED_TESTS} tests.",
        )
    if body.case_ids and {case.id for case in cases} != set(body.case_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test not found.")
    if not cases:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No accepted enabled tests found for this agent.",
        )
    return cases


async def _infer_target_org_id(
    db: AsyncSession,
    *,
    agent_id: UUID,
    user: UserPrincipal,
    run_ids: list[UUID],
) -> UUID | None:
    target_org_id = user.organization_id
    inferred = not user.is_superuser
    if not user.is_superuser and user.organization_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    for run_id in _unique_ordered(run_ids):
        run = await db.get(AgentRun, run_id)
        if run is None or run.agent_id != agent_id or run.trigger_type == "evaluation_synthetic":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
        if not user.is_superuser:
            if run.org_id != target_org_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
            continue
        if not inferred:
            target_org_id = run.org_id
            inferred = True
        elif run.org_id != target_org_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Recorded evaluations require selected runs from one tenant.",
            )
    return target_org_id


async def _load_and_freeze_runs(
    db: AsyncSession,
    *,
    agent_id: UUID,
    user: UserPrincipal,
    run_ids: list[UUID],
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for run_id in _unique_ordered(run_ids):
        run = await db.get(AgentRun, run_id)
        if run is None or run.agent_id != agent_id or run.trigger_type == "evaluation_synthetic":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
        try:
            projection = await load_recorded_run_evidence(db, run_id, user=user)
        except RecordedEvidenceOversized as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except RecordedEvidenceError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.") from exc
        if projection["completeness"].get("terminal_status") is not True:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Recorded evaluations require terminal production runs.",
            )
        source_ids = [
            str(run_id),
            *_collect_delegated_source_ids(projection["evidence"]),
        ]
        source_refs: list[dict[str, Any]] = []
        for source_id in source_ids:
            source = await db.get(AgentRun, UUID(source_id))
            if source is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
            source_refs.append(
                {
                    "run_id": str(source.id),
                    "agent_id": str(source.agent_id) if source.agent_id else None,
                    "org_id": str(source.org_id) if source.org_id else None,
                    "selected": source.id == run_id,
                }
            )
        runs.append(
            {
                "run_id": str(run_id),
                "evidence": projection["evidence"],
                "completeness": projection["completeness"],
                "evidence_refs": projection["evidence_refs"],
                "limitations": projection["limitations"],
                "source_runs": source_refs,
                "evidence_hash": _hash(projection),
            }
        )
    return runs


def _freeze_cases(cases: list[AgentEvaluationCase]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": str(case.id),
            "suite_id": str(case.suite_id),
            "name": case.name,
            "version": case.version,
            "assertions": case.assertions or [],
            "assertions_hash": _hash(case.assertions or []),
        }
        for case in cases
    ]


def _applicability_for(
    body: RecordedEvaluationCreate, *, case_id: str, run_id: str
) -> tuple[str, str]:
    for override in body.applicability_overrides:
        if str(override.case_id) == case_id and str(override.run_id) == run_id:
            return override.applicability, "caller_declared_override"
    return body.applicability, "caller_declared_default"


def _validate_applicability_overrides(
    body: RecordedEvaluationCreate,
    *,
    case_ids: set[str],
    run_ids: set[str],
) -> None:
    seen: dict[tuple[str, str], str] = {}
    for override in body.applicability_overrides:
        key = (str(override.case_id), str(override.run_id))
        if key[0] not in case_ids or key[1] not in run_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Applicability override references an unselected case/run pair.",
            )
        previous = seen.get(key)
        if previous is not None and previous != override.applicability:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Conflicting applicability overrides for one case/run pair.",
            )
        seen[key] = override.applicability


async def admit_recorded_evaluation(
    db: AsyncSession,
    *,
    user: UserPrincipal,
    body: RecordedEvaluationCreate,
) -> tuple[AgentRecordedEvaluation, PlatformJob, bool]:
    from src.jobs.platform.agent_recorded_evaluation import (
        AGENT_RECORDED_EVALUATION_DEFINITION,
        AGENT_RECORDED_SEMANTIC_EVALUATION_DEFINITION,
        AgentRecordedEvaluationPayload,
    )

    run_ids = _unique_ordered(body.run_ids)
    if not run_ids or len(run_ids) > MAX_RECORDED_RUNS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Select between 1 and {MAX_RECORDED_RUNS} runs.",
        )
    if body.judge_mode == "semantic" and not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recorded semantic evaluation requires a platform administrator.",
        )
    agent = await _load_authorized_agent(db, user, body.agent_id)
    org_id = await _infer_target_org_id(
        db, agent_id=agent.id, user=user, run_ids=run_ids
    )
    cases = await _load_cases(db, agent_id=agent.id, org_id=org_id, body=body)
    pair_count = len(cases) * len(run_ids)
    if pair_count > MAX_RECORDED_PAIRS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Recorded evaluations support at most {MAX_RECORDED_PAIRS} case/run pairs.",
        )
    frozen_runs = await _load_and_freeze_runs(
        db, agent_id=agent.id, user=user, run_ids=run_ids
    )
    frozen_cases = _freeze_cases(cases)
    _validate_applicability_overrides(
        body,
        case_ids={case["case_id"] for case in frozen_cases},
        run_ids={run["run_id"] for run in frozen_runs},
    )
    applicability = {
        f"{case['case_id']}:{run['run_id']}": {
            "value": _applicability_for(
                body, case_id=case["case_id"], run_id=run["run_id"]
            )[0],
            "source": _applicability_for(
                body, case_id=case["case_id"], run_id=run["run_id"]
            )[1],
        }
        for case in frozen_cases
        for run in frozen_runs
    }
    frozen_input = {
        "agent": {
            "id": str(agent.id),
            "name": agent.name,
            "org_id": str(agent.organization_id) if agent.organization_id else None,
        },
        "cases": frozen_cases,
        "runs": frozen_runs,
        "applicability": applicability,
        "created_by": str(user.user_id),
        "judge_mode": body.judge_mode,
    }
    frozen_bytes = len(_canonical_json(frozen_input).encode("utf-8"))
    if frozen_bytes > MAX_FROZEN_INPUT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Recorded evaluation frozen input exceeds 4 MiB.",
        )
    dedupe_material = {
        "requester": str(user.user_id),
        "tenant": str(org_id) if org_id else None,
        "agent_id": str(agent.id),
        "case_hashes": [
            (case["case_id"], case["version"], case["assertions_hash"])
            for case in sorted(frozen_cases, key=lambda item: item["case_id"])
        ],
        "run_hashes": [
            (run["run_id"], run["evidence_hash"])
            for run in sorted(frozen_runs, key=lambda item: item["run_id"])
        ],
        "applicability": applicability,
        "judge_mode": body.judge_mode,
    }
    dedupe_key = "recorded:" + _hash(dedupe_material)
    evaluation_id = uuid4()
    job_definition = (
        AGENT_RECORDED_SEMANTIC_EVALUATION_DEFINITION
        if body.judge_mode == "semantic"
        else AGENT_RECORDED_EVALUATION_DEFINITION
    )
    job, reused = await enqueue_platform_job(
        db,
        job_definition,
        AgentRecordedEvaluationPayload(evaluation_id=evaluation_id),
        dedupe_key=dedupe_key,
        organization_id=org_id,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="agent_recorded_evaluation",
        resource_id=str(evaluation_id),
        title=f"Recorded evaluation for {agent.name}",
        action_url=None,
    )
    if reused:
        existing = await db.scalar(
            select(AgentRecordedEvaluation).where(
                AgentRecordedEvaluation.platform_job_id == job.id
            )
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Recorded evaluation job is missing its domain record.",
            )
        return existing, job, True
    evaluation = AgentRecordedEvaluation(
        id=evaluation_id,
        org_id=org_id,
        agent_id=agent.id,
        requested_by_user_id=str(user.user_id),
        requested_by_email=user.email,
        platform_job_id=job.id,
        frozen_input=frozen_input,
    )
    db.add(evaluation)
    await db.flush()
    return evaluation, job, False



async def _persist_recorded_pair_result(
    db: AsyncSession,
    *,
    evaluation_id: UUID,
    case: dict[str, Any],
    run: dict[str, Any],
    app: dict[str, Any],
    outcomes: list[dict[str, Any]],
    run_evidence_refs: list[dict[str, Any]],
    run_limitations: list[str],
    error: str | None = None,
) -> dict[str, Any]:
    pair = aggregate_recorded_pair(outcomes)
    statement = pg_insert(AgentRecordedEvaluationResult).values(
        evaluation_id=evaluation_id,
        case_id=UUID(case["case_id"]),
        case_version=int(case["version"]),
        run_id=UUID(run["run_id"]),
        applicability=app.get("value", "unknown"),
        applicability_source=app.get("source", "caller_declared_default"),
        outcome=pair["outcome"],
        complete=pair["complete"],
        assertion_outcomes=outcomes,
        counts=pair["counts"],
        evidence_refs=run_evidence_refs,
        limitations=run_limitations,
        error=error,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_recorded_eval_result_pair",
        set_={
            "applicability": statement.excluded.applicability,
            "applicability_source": statement.excluded.applicability_source,
            "outcome": statement.excluded.outcome,
            "complete": statement.excluded.complete,
            "assertion_outcomes": statement.excluded.assertion_outcomes,
            "counts": statement.excluded.counts,
            "evidence_refs": statement.excluded.evidence_refs,
            "limitations": statement.excluded.limitations,
            "error": statement.excluded.error,
        },
    )
    await db.execute(statement)
    return pair


def _terminal_recorded_judge_outcome(outcome: dict[str, Any]) -> bool:
    if outcome.get("type") != "llm_judge":
        return False
    if outcome.get("judge_execution_state") == "started":
        return False
    return outcome.get("outcome") in {"passed", "failed", "insufficient_evidence", "error"}


def _paid_recorded_judge_marker(outcome: dict[str, Any]) -> bool:
    state = outcome.get("judge_execution_state")
    return bool(outcome.get("accounting_attempt_id")) or state in {
        "started",
        "completed",
        "ambiguous",
    }


def _marker_identity_mismatch_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    updated = dict(outcome)
    updated.update(
        {
            "outcome": "error",
            "passed": False,
            "actual": None,
            "reason": "judge_marker_identity_mismatch",
            "detail": "semantic judge marker no longer matches the frozen assertion identity",
            "judge_execution_state": "error",
        }
    )
    return updated


def _merge_existing_recorded_semantic_outcomes(
    fresh: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Resume one pair by stable assertion index/hash, never by whole-pair state."""
    merged = list(fresh)
    changed = False
    for index, current in enumerate(fresh):
        if current.get("type") != "llm_judge" or current.get("outcome") != "pending_judge":
            continue
        if index >= len(existing):
            continue
        prior = existing[index]
        if not isinstance(prior, dict) or prior.get("type") != "llm_judge":
            continue
        prior_hash = prior.get("assertion_hash")
        if (
            index >= len(assertions)
            or prior.get("assertion_index") != index
            or prior_hash != assertion_hash(assertions[index])
        ):
            if _paid_recorded_judge_marker(prior):
                merged[index] = _marker_identity_mismatch_outcome(prior)
                changed = True
            continue
        if prior.get("judge_execution_state") == "started":
            merged[index] = ambiguous_outcome(prior)
            changed = True
        elif _terminal_recorded_judge_outcome(prior):
            merged[index] = prior
            changed = True
    return merged, changed


def _recorded_judge_identity(
    *,
    evaluation_id: UUID,
    case: dict[str, Any],
    run: dict[str, Any],
    assertion_index: int,
    assertion: dict[str, Any],
) -> tuple[str, str]:
    item = ":".join(
        [
            str(case["case_id"]),
            str(run["run_id"]),
            str(assertion_index),
            assertion_hash(assertion),
        ]
    )
    return f"recorded-semantic:{evaluation_id}:{item}", item


async def _resolve_recorded_semantic_outcomes(
    context: PlatformJobContext,
    *,
    evaluation: AgentRecordedEvaluation,
    case: dict[str, Any],
    run: dict[str, Any],
    app: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    async with get_db_context() as db:
        existing = await db.scalar(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation.id,
                AgentRecordedEvaluationResult.case_id == UUID(case["case_id"]),
                AgentRecordedEvaluationResult.run_id == UUID(run["run_id"]),
            )
        )
        if existing is not None:
            existing_outcomes = list(existing.assertion_outcomes or [])
            merged, changed = _merge_existing_recorded_semantic_outcomes(
                outcomes, existing_outcomes, list(case.get("assertions") or [])
            )
            if changed:
                fence = await lock_running_platform_job_for_result_write(
                    db, job_id=context.job_id, lease_token=context.lease_token
                )
                if fence is None:
                    raise PlatformJobCancelled
                await _persist_recorded_pair_result(
                    db,
                    evaluation_id=evaluation.id,
                    case=case,
                    run=run,
                    app=app,
                    outcomes=merged,
                    run_evidence_refs=run.get("evidence_refs") or [],
                    run_limitations=run.get("limitations") or [],
                    error=(
                        "judge_attempt_ambiguous"
                        if any(
                            item.get("reason") == "judge_attempt_ambiguous"
                            for item in merged
                            if isinstance(item, dict)
                        )
                        else existing.error
                    ),
                )
                await db.commit()
                outcomes = merged

    resolved = list(outcomes)
    assertions = list(case.get("assertions") or [])
    for index, outcome in enumerate(list(resolved)):
        if outcome.get("type") != "llm_judge" or outcome.get("outcome") != "pending_judge":
            continue
        assertion = assertions[index]
        prepared = prepare_recorded_judge_request(
            assertion, run["evidence"], run["completeness"]
        )
        if prepared.insufficient_outcome is not None:
            resolved[index] = prepared.insufficient_outcome
            continue
        assert prepared.request_payload is not None
        assert prepared.request_fingerprint is not None
        idem_key, item_id = _recorded_judge_identity(
            evaluation_id=evaluation.id,
            case=case,
            run=run,
            assertion_index=index,
            assertion=assertion,
        )
        params = assertion.get("params") or {}
        snapshot = dict(params.get("judge_snapshot") or {})
        accounting_provider = canonical_provider(
            str(snapshot.get("provider") or ""),
            snapshot.get("endpoint"),
        )
        async with get_db_context() as db:
            fence = await lock_running_platform_job_for_result_write(
                db, job_id=context.job_id, lease_token=context.lease_token
            )
            if fence is None:
                raise PlatformJobCancelled
            attempt_result = await begin_quality_usage_attempt(
                db,
                idempotency_key=idem_key,
                quality_operation_type="recorded_evaluation",
                quality_operation_id=evaluation.id,
                quality_operation_item_id=item_id,
                usage_purpose="recorded_semantic_judge",
                provider=accounting_provider,
                model=str(snapshot.get("model") or ""),
                request_fingerprint=prepared.request_fingerprint,
                organization_id=evaluation.org_id,
                user_id=UUID(evaluation.requested_by_user_id),
                platform_job_id=context.job_id,
                profile_id=UUID(str(snapshot["profile_id"])),
                profile_name=snapshot.get("profile_name"),
                profile_fingerprint=_hash(snapshot),
            )
            if not attempt_result.created:
                resolved[index] = ambiguous_outcome(
                    started_outcome(
                        assertion,
                        accounting_attempt_id=attempt_result.attempt.id,
                        assertion_index=index,
                        request_fingerprint=prepared.request_fingerprint,
                        selector=params.get("recorded_evidence_requirements") or [],
                    )
                )
                await _persist_recorded_pair_result(
                    db,
                    evaluation_id=evaluation.id,
                    case=case,
                    run=run,
                    app=app,
                    outcomes=resolved,
                    run_evidence_refs=run.get("evidence_refs") or [],
                    run_limitations=run.get("limitations") or [],
                    error="judge_attempt_ambiguous",
                )
                await db.commit()
                continue
            resolved[index] = started_outcome(
                assertion,
                accounting_attempt_id=attempt_result.attempt.id,
                assertion_index=index,
                request_fingerprint=prepared.request_fingerprint,
                selector=params.get("recorded_evidence_requirements") or [],
            )
            await _persist_recorded_pair_result(
                db,
                evaluation_id=evaluation.id,
                case=case,
                run=run,
                app=app,
                outcomes=resolved,
                run_evidence_refs=run.get("evidence_refs") or [],
                run_limitations=run.get("limitations") or [],
            )
            await db.commit()
        call_result = await execute_recorded_semantic_judge(
            assertion=assertion,
            request_payload=prepared.request_payload,
        )
        async with get_db_context() as db:
            if call_result.usage is not None:
                await record_quality_usage_observation(
                    db,
                    idempotency_key=idem_key,
                    quality_operation_type="recorded_evaluation",
                    quality_operation_id=evaluation.id,
                    quality_operation_item_id=item_id,
                    usage_purpose="recorded_semantic_judge",
                    provider=accounting_provider,
                    model=str(snapshot.get("model") or ""),
                    request_fingerprint=prepared.request_fingerprint,
                    input_tokens=call_result.usage["input_tokens"],
                    output_tokens=call_result.usage["output_tokens"],
                    cache_read_tokens=call_result.usage.get("cache_read_tokens", 0),
                    cache_write_tokens=call_result.usage.get("cache_write_tokens", 0),
                    provider_cost=call_result.usage.get("provider_cost"),
                    duration_ms=call_result.usage.get("duration_ms"),
                )
            else:
                await mark_quality_usage_unobserved(
                    db,
                    idempotency_key=idem_key,
                    reason=call_result.unobserved_reason or "missing_usage",
                )
            await db.commit()
        async with get_db_context() as db:
            fence = await lock_running_platform_job_for_result_write(
                db, job_id=context.job_id, lease_token=context.lease_token
            )
            if fence is None:
                raise PlatformJobCancelled
            final = dict(call_result.outcome)
            final.update(
                {
                    "accounting_attempt_id": str(attempt_result.attempt.id),
                    "assertion_index": index,
                    "assertion_hash": assertion_hash(assertion),
                    "request_fingerprint": prepared.request_fingerprint,
                    "recorded_evidence_requirements": params.get("recorded_evidence_requirements") or [],
                }
            )
            if final.get("judge_execution_state") != "completed":
                final["judge_execution_state"] = "error"
            resolved[index] = final
            await _persist_recorded_pair_result(
                db,
                evaluation_id=evaluation.id,
                case=case,
                run=run,
                app=app,
                outcomes=resolved,
                run_evidence_refs=run.get("evidence_refs") or [],
                run_limitations=run.get("limitations") or [],
                error=(None if final.get("outcome") != "error" else final.get("reason")),
            )
            await db.commit()
    return resolved


async def execute_recorded_evaluation_job(
    context: PlatformJobContext,
    *,
    evaluation_id: UUID,
) -> dict[str, Any]:
    async with get_db_context() as db:
        evaluation = await db.get(AgentRecordedEvaluation, evaluation_id)
        if evaluation is None:
            raise PlatformJobFailure(
                "recorded_evaluation_not_found",
                "Recorded evaluation not found.",
                retryable=False,
            )
        if (
            evaluation.platform_job_id != context.job_id
            or evaluation.org_id != context.organization_id
        ):
            raise PlatformJobFailure(
                "recorded_evaluation_job_mismatch",
                "Recorded evaluation is not bound to this platform job.",
                retryable=False,
            )
        job = await db.get(PlatformJob, context.job_id)
        frozen = evaluation.frozen_input
        mode = frozen.get("judge_mode", "exact")
        expected_job_type = (
            "agent.evaluation_recorded_semantic"
            if mode == "semantic"
            else "agent.evaluation_recorded"
        )
        if job is None or job.job_type != expected_job_type:
            raise PlatformJobFailure(
                "recorded_evaluation_job_mismatch",
                "Recorded evaluation mode is not bound to this platform job type.",
                retryable=False,
            )
    cases = frozen.get("cases") or []
    runs = frozen.get("runs") or []
    applicability = frozen.get("applicability") or {}
    total = len(cases) * len(runs)
    done = 0
    pair_verdicts: list[dict[str, Any]] = []
    await context.report("Evaluating recorded pairs", current=0, total=total, percent=0)
    for case in cases:
        for run in runs:
            key = f"{case['case_id']}:{run['run_id']}"
            app = applicability.get(key) or {
                "value": "unknown",
                "source": "caller_declared_default",
            }
            outcomes = evaluate_recorded_assertions(
                case.get("assertions") or [],
                run["evidence"],
                completeness=run["completeness"],
                applicability=app.get("value", "unknown"),
            )
            if frozen.get("judge_mode", "exact") == "semantic" and app.get("value") == "applicable":
                outcomes = await _resolve_recorded_semantic_outcomes(
                    context,
                    evaluation=evaluation,
                    case=case,
                    run=run,
                    app=app,
                    outcomes=outcomes,
                )
            pair = aggregate_recorded_pair(outcomes)
            pair_verdicts.append(pair)
            async with get_db_context() as db:
                fence = await lock_running_platform_job_for_result_write(
                    db, job_id=context.job_id, lease_token=context.lease_token
                )
                if fence is None:
                    raise PlatformJobCancelled
                await _persist_recorded_pair_result(
                    db,
                    evaluation_id=evaluation_id,
                    case=case,
                    run=run,
                    app=app,
                    outcomes=outcomes,
                    run_evidence_refs=run.get("evidence_refs") or [],
                    run_limitations=run.get("limitations") or [],
                )
                await db.commit()
            done += 1
            await context.report(
                "Evaluating recorded pairs",
                current=done,
                total=total,
                percent=100 * done / total if total else 100,
            )
    aggregate = aggregate_recorded_evaluation(pair_verdicts)
    async with get_db_context() as db:
        fence = await lock_running_platform_job_for_result_write(
            db, job_id=context.job_id, lease_token=context.lease_token
        )
        if fence is None:
            raise PlatformJobCancelled
        evaluation = await db.get(AgentRecordedEvaluation, evaluation_id)
        if evaluation is None:
            raise PlatformJobFailure(
                "recorded_evaluation_not_found",
                "Recorded evaluation not found.",
                retryable=False,
            )
        evaluation.aggregate = aggregate
        evaluation.completed_at = datetime.now(timezone.utc)
        await db.commit()
    return {
        "evaluation_id": str(evaluation_id),
        "total": aggregate["total"],
        "counts": aggregate["counts"],
        "complete": aggregate["complete"],
        "gate_passed": aggregate["gate_passed"],
        "all_inapplicable": aggregate["all_inapplicable"],
    }


async def assert_recorded_evaluation_readable(
    db: AsyncSession, *, evaluation: AgentRecordedEvaluation, user: UserPrincipal
) -> None:
    if not (user.is_superuser or evaluation.requested_by_user_id == str(user.user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recorded evaluation not found.")
    await _load_authorized_agent(db, user, evaluation.agent_id)
    frozen_source_refs: list[dict[str, Any]] = []
    for run in (evaluation.frozen_input.get("runs") or []):
        source_refs = run.get("source_runs")
        if not source_refs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recorded evaluation not found.",
            )
        frozen_source_refs.extend(source_refs)
    if not frozen_source_refs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recorded evaluation not found.",
        )

    source_ids = {UUID(source_ref["run_id"]) for source_ref in frozen_source_refs}
    visible_sources = {
        source.id: source
        for source in (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.id.in_(source_ids),
                    *agent_run_visibility_conditions(user),
                )
            )
        ).scalars()
    }
    if set(visible_sources) != source_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recorded evaluation not found.",
        )

    for source_ref in frozen_source_refs:
        source = visible_sources[UUID(source_ref["run_id"])]
        current_agent_id = str(source.agent_id) if source.agent_id else None
        if (
            source.org_id != evaluation.org_id
            or current_agent_id != source_ref.get("agent_id")
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recorded evaluation not found.",
            )


async def get_recorded_results_page(
    db: AsyncSession,
    *,
    evaluation_id: UUID,
    user: UserPrincipal,
    limit: int,
    offset: int,
) -> RecordedEvaluationResultsPage:
    evaluation = await db.get(AgentRecordedEvaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recorded evaluation not found.")
    await assert_recorded_evaluation_readable(db, evaluation=evaluation, user=user)
    total = (
        await db.execute(
            select(func.count())
            .select_from(AgentRecordedEvaluationResult)
            .where(AgentRecordedEvaluationResult.evaluation_id == evaluation.id)
        )
    ).scalar_one()
    rows = list(
        (
            await db.execute(
                select(AgentRecordedEvaluationResult)
                .where(AgentRecordedEvaluationResult.evaluation_id == evaluation.id)
                .order_by(
                    AgentRecordedEvaluationResult.case_id,
                    AgentRecordedEvaluationResult.run_id,
                )
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    job = await db.get(PlatformJob, evaluation.platform_job_id)
    terminal_job_status = job.status if job and job.status in {"failed", "cancelled"} else None
    results: list[RecordedEvaluationResultPublic] = []
    for row in rows:
        result = RecordedEvaluationResultPublic.model_validate(row)
        if terminal_job_status is not None:
            projected_outcomes = [
                ambiguous_outcome(outcome)
                if isinstance(outcome, dict)
                and outcome.get("type") == "llm_judge"
                and outcome.get("judge_execution_state") == "started"
                else outcome
                for outcome in result.assertion_outcomes
            ]
            if projected_outcomes != result.assertion_outcomes:
                pair = aggregate_recorded_pair(projected_outcomes)
                result = result.model_copy(
                    update={
                        "assertion_outcomes": projected_outcomes,
                        "outcome": pair["outcome"],
                        "complete": pair["complete"],
                        "counts": pair["counts"],
                        "error": "judge_attempt_ambiguous",
                    }
                )
        results.append(result)
    return RecordedEvaluationResultsPage(
        evaluation_id=evaluation.id,
        job_id=evaluation.platform_job_id,
        aggregate=evaluation.aggregate,
        results=results,
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )
