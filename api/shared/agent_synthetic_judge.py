"""Synthetic semantic judge planning and accounting helpers."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from src.services.agent_evaluations.simulator_models import redact_value
from src.services.model_pricing import canonical_provider

SEMANTIC_JUDGE_JOB_ID_KEY = "semantic_judge_job_id"
MAX_SYNTHETIC_JUDGE_RATIONALE_CHARS = 2000


@dataclass(frozen=True)
class SyntheticJudgePlan:
    execution_id: UUID
    result_id: UUID
    side: str
    evidence: dict[str, Any]
    assertion: dict[str, Any]
    assertion_index: int
    semantic_index: int
    assertion_hash: str
    request_payload: dict[str, Any]
    request_fingerprint: str
    idempotency_key: str
    item_id: str
    provider: str
    model: str
    profile_id: UUID
    profile_fingerprint: str


def canonical_hash(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def has_semantic_pending(result: Any) -> bool:
    return bool((result.comparison or {}).get("_semantic_pending"))


def semantic_child_job_id(result: Any) -> UUID | None:
    raw = (result.comparison or {}).get(SEMANTIC_JUDGE_JOB_ID_KEY)
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("malformed semantic child job id") from exc


def has_malformed_semantic_child_job_id(result: Any) -> bool:
    try:
        semantic_child_job_id(result)
    except ValueError:
        return True
    return False


def persist_semantic_child_job_id(result: Any, job_id: UUID) -> None:
    comparison = dict(result.comparison or {})
    existing = comparison.get(SEMANTIC_JUDGE_JOB_ID_KEY)
    if existing is not None and str(existing) != str(job_id):
        raise ValueError("semantic child job linkage mismatch")
    comparison[SEMANTIC_JUDGE_JOB_ID_KEY] = str(job_id)
    result.comparison = comparison


def clear_semantic_pending_if_done(result: Any) -> None:
    if any(
        outcome.get("type") == "llm_judge"
        and outcome.get("judge_execution_state") in {"started", None}
        and outcome.get("actual") == "pending"
        for outcome in result.assertion_results or []
        if isinstance(outcome, dict)
    ):
        return
    comparison = dict(result.comparison or {})
    comparison.pop("_semantic_pending", None)
    result.comparison = comparison


def build_judge_plans(
    result: Any,
    *,
    execution_id: UUID,
    result_id: UUID,
) -> list[SyntheticJudgePlan]:
    comparison = dict(result.comparison or {})
    pending = comparison.get("_semantic_pending")
    if not pending:
        return []
    definitions = [
        definition
        for definition in list(pending.get("definitions") or [])
        if isinstance(definition, dict) and definition.get("type") == "llm_judge"
    ]
    if not definitions:
        return []
    sides: list[tuple[str, dict[str, Any]]] = [
        ("baseline", dict(pending.get("baseline_evidence") or {}))
    ]
    if pending.get("candidate_evidence") is not None:
        sides.append(("candidate", dict(pending.get("candidate_evidence") or {})))

    plans: list[SyntheticJudgePlan] = []
    for side, evidence in sides:
        semantic_index = 0
        for assertion_index, outcome in enumerate(result.assertion_results or []):
            if not isinstance(outcome, dict) or outcome.get("type") != "llm_judge":
                continue
            if outcome.get("side", "baseline") != side:
                continue
            if outcome.get("judge_execution_state") == "completed":
                semantic_index += 1
                continue
            if outcome.get("judge_execution_state") == "ambiguous":
                semantic_index += 1
                continue
            try:
                assertion = definitions[semantic_index]
            except IndexError:
                break
            plan = _build_plan(
                result,
                execution_id=execution_id,
                result_id=result_id,
                side=side,
                evidence=evidence,
                assertion=assertion,
                assertion_index=assertion_index,
                semantic_index=semantic_index,
            )
            plans.append(plan)
            semantic_index += 1
    return plans


def _build_plan(
    result: Any,
    *,
    execution_id: UUID,
    result_id: UUID,
    side: str,
    evidence: dict[str, Any],
    assertion: dict[str, Any],
    assertion_index: int,
    semantic_index: int,
) -> SyntheticJudgePlan:
    params = assertion.get("params") or {}
    snapshot = dict(params.get("judge_snapshot") or {})
    provider = canonical_provider(
        str(snapshot.get("provider") or ""),
        snapshot.get("endpoint"),
    )
    model = str(snapshot.get("model") or "")
    profile_id = UUID(str(snapshot["profile_id"]))
    profile_fingerprint = canonical_hash(snapshot)
    evidence_for_judge = redact_value(evidence)
    request_payload = {
        "rubric": params.get("rubric"),
        "evidence": evidence_for_judge,
    }
    request_fingerprint = canonical_hash(
        {
            "prompt_version": params.get("prompt_version"),
            "threshold": params.get("threshold"),
            "judge_snapshot": snapshot,
            "request": request_payload,
        }
    )
    assertion_digest = canonical_hash(assertion)
    identity = {
        "kind": "synthetic_semantic_judge",
        "execution_id": str(execution_id),
        "result_id": str(result_id),
        "case_id": str(result.case_id),
        "case_version": result.case_version,
        "repetition_index": result.repetition_index,
        "side": side,
        "assertion_index": assertion_index,
        "assertion_hash": assertion_digest,
        "request_fingerprint": request_fingerprint,
    }
    identity_hash = canonical_hash(identity)
    return SyntheticJudgePlan(
        execution_id=execution_id,
        result_id=result_id,
        side=side,
        evidence=evidence_for_judge,
        assertion=assertion,
        assertion_index=assertion_index,
        semantic_index=semantic_index,
        assertion_hash=assertion_digest,
        request_payload=request_payload,
        request_fingerprint=request_fingerprint,
        idempotency_key=f"synthetic-semantic:{identity_hash}",
        item_id=identity_hash,
        provider=provider,
        model=model,
        profile_id=profile_id,
        profile_fingerprint=profile_fingerprint,
    )


def mark_started(result: Any, plan: SyntheticJudgePlan, *, attempt_id: UUID) -> None:
    outcomes = list(result.assertion_results or [])
    current = dict(outcomes[plan.assertion_index])
    current.update(
        {
            "actual": "pending",
            "passed": False,
            "detail": "semantic judge call was started; verdict is not yet durable",
            "reason": "judge_started",
            "authoritative": False,
            "nondeterministic": True,
            "judge_execution_state": "started",
            "accounting_attempt_id": str(attempt_id),
            "assertion_index": plan.assertion_index,
            "assertion_hash": plan.assertion_hash,
            "request_fingerprint": plan.request_fingerprint,
        }
    )
    outcomes[plan.assertion_index] = current
    result.assertion_results = outcomes


def mark_completed(result: Any, plan: SyntheticJudgePlan, outcome: dict[str, Any]) -> None:
    outcomes = list(result.assertion_results or [])
    current = dict(outcome)
    current.update(
        {
            "side": plan.side,
            "assertion_index": plan.assertion_index,
            "assertion_hash": plan.assertion_hash,
            "request_fingerprint": plan.request_fingerprint,
            "judge_execution_state": (
                "completed" if outcome.get("reason") == "semantic_judge" else "error"
            ),
        }
    )
    existing = outcomes[plan.assertion_index]
    if isinstance(existing, dict) and existing.get("accounting_attempt_id"):
        current["accounting_attempt_id"] = existing["accounting_attempt_id"]
    outcomes[plan.assertion_index] = current
    result.assertion_results = outcomes
    clear_semantic_pending_if_done(result)


def _terminal_semantic_state(outcome: dict[str, Any]) -> bool:
    return outcome.get("judge_execution_state") in {"completed", "ambiguous", "error"}


def mark_ambiguous(result: Any) -> bool:
    changed = False
    outcomes: list[dict[str, Any]] = []
    for outcome in result.assertion_results or []:
        if (
            isinstance(outcome, dict)
            and outcome.get("type") == "llm_judge"
            and outcome.get("judge_execution_state") == "started"
        ):
            updated = dict(outcome)
            updated.update(
                {
                    "passed": False,
                    "actual": None,
                    "detail": "semantic judge attempt was started but no durable verdict exists",
                    "reason": "judge_attempt_ambiguous",
                    "judge_execution_state": "ambiguous",
                    "authoritative": False,
                    "nondeterministic": True,
                }
            )
            outcomes.append(updated)
            changed = True
        else:
            outcomes.append(outcome)
    if changed:
        result.assertion_results = outcomes
        clear_semantic_pending_if_done(result)
    return changed


def mark_unavailable(result: Any, *, reason: str = "judge_attempt_ambiguous") -> bool:
    changed = False
    outcomes: list[dict[str, Any]] = []
    for outcome in result.assertion_results or []:
        if (
            isinstance(outcome, dict)
            and outcome.get("type") == "llm_judge"
            and not _terminal_semantic_state(outcome)
        ):
            detail = (
                "semantic judge attempt was started but no durable verdict exists"
                if outcome.get("judge_execution_state") == "started"
                else "semantic judge attempt has no durable worker outcome"
            )
            updated = dict(outcome)
            updated.update(
                {
                    "passed": False,
                    "actual": None,
                    "detail": detail,
                    "reason": reason,
                    "judge_execution_state": "ambiguous",
                    "authoritative": False,
                    "nondeterministic": True,
                }
            )
            outcomes.append(updated)
            changed = True
        else:
            outcomes.append(outcome)
    if changed:
        result.assertion_results = outcomes
        clear_semantic_pending_if_done(result)
    return changed


def mark_plan_ambiguous(
    result: Any,
    plan: SyntheticJudgePlan,
    *,
    attempt_id: UUID | None = None,
) -> None:
    outcomes = list(result.assertion_results or [])
    current = dict(outcomes[plan.assertion_index])
    current.update(
        {
            "passed": False,
            "actual": None,
            "detail": "semantic judge attempt was started but no durable verdict exists",
            "reason": "judge_attempt_ambiguous",
            "judge_execution_state": "ambiguous",
            "authoritative": False,
            "nondeterministic": True,
            "assertion_index": plan.assertion_index,
            "assertion_hash": plan.assertion_hash,
            "request_fingerprint": plan.request_fingerprint,
        }
    )
    if attempt_id is not None:
        current["accounting_attempt_id"] = str(attempt_id)
    outcomes[plan.assertion_index] = current
    result.assertion_results = outcomes
    clear_semantic_pending_if_done(result)


def usage_from_response(response: Any) -> dict[str, Any] | None:
    input_tokens = getattr(response, "input_tokens", None)
    output_tokens = getattr(response, "output_tokens", None)
    if (
        not isinstance(input_tokens, int)
        or isinstance(input_tokens, bool)
        or input_tokens < 0
    ):
        return None
    if (
        not isinstance(output_tokens, int)
        or isinstance(output_tokens, bool)
        or output_tokens < 0
    ):
        return None
    cache_read_tokens = getattr(response, "cache_read_tokens", 0)
    cache_write_tokens = getattr(response, "cache_write_tokens", 0)
    if (
        not isinstance(cache_read_tokens, int)
        or isinstance(cache_read_tokens, bool)
        or cache_read_tokens < 0
        or not isinstance(cache_write_tokens, int)
        or isinstance(cache_write_tokens, bool)
        or cache_write_tokens < 0
    ):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "provider_cost": getattr(response, "provider_cost", None),
    }


def usage_projection(usage: dict[str, Any] | None) -> dict[str, Any]:
    if usage is None:
        return {}
    projected: dict[str, Any] = {
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cost_usd": (
            str(usage["provider_cost"])
            if usage.get("provider_cost") is not None
            else None
        ),
    }
    if usage.get("cache_read_tokens"):
        projected["cache_read_tokens"] = usage["cache_read_tokens"]
    if usage.get("cache_write_tokens"):
        projected["cache_write_tokens"] = usage["cache_write_tokens"]
    return projected


def completed_outcome(
    plan: SyntheticJudgePlan,
    *,
    score: float | None,
    detail: str,
    passed: bool,
    reason: str = "semantic_judge",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = plan.assertion.get("params") or {}
    return {
        "code": "llm_judge",
        "type": "llm_judge",
        "label": plan.assertion.get("label"),
        "passed": passed,
        "expected": f"score >= {params.get('threshold')}",
        "actual": score,
        "detail": detail,
        "reason": reason,
        "evidence_sequences": [],
        "authoritative": False,
        "nondeterministic": True,
        "judge_snapshot": redact_value(params.get("judge_snapshot") or {}),
        "judge_evidence": plan.evidence,
        "judge_rubric": redact_value(params.get("rubric")),
        "judge_threshold": params.get("threshold"),
        "judge_usage": usage_projection(usage),
        "evidence_hash": canonical_hash(plan.evidence),
    }


ACTIVE_SYNTHETIC_CHILD_STATUSES = {"queued", "running", "waiting", "cancel_requested"}
ACTIVE_PARENT_STATUSES = {"queued", "running", "waiting"}


def _dedupe_key(result_id: UUID) -> str:
    return f"synthetic-semantic-result:{result_id}"


def _resource_lock_key(execution_id: UUID) -> str:
    return f"agent-evaluation-semantic:{execution_id}"


async def enqueue_synthetic_semantic_judge_job(db, *, execution: Any, result: Any) -> Any | None:
    """Create or reuse the internal semantic postprocessing job for a result."""
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import SYNTHETIC_SEMANTIC_JUDGE_DEFINITION
    from src.models.orm.agent_evaluations import AgentEvaluationSuite
    from src.models.orm.platform_jobs import PlatformJob
    from src.models.orm.users import User
    from src.services.platform_jobs import enqueue_platform_job

    if not (result.comparison or {}).get("_semantic_pending"):
        return None
    try:
        existing_id = semantic_child_job_id(result)
    except ValueError:
        mark_unavailable(result, reason="judge_child_link_malformed")
        return None
    if existing_id is not None:
        existing = await db.get(PlatformJob, existing_id)
        if existing is not None:
            return existing
        mark_unavailable(result)
        return None
    suite = await db.get(AgentEvaluationSuite, execution.suite_id)
    if suite is None:
        mark_unavailable(result)
        return None
    parent_job = (
        await db.execute(
            select(PlatformJob)
            .where(PlatformJob.id == execution.platform_job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if parent_job is None or _parent_cancelled(parent_job) or execution.status == "cancelled":
        mark_unavailable(result, reason="judge_parent_cancelled")
        return None
    try:
        requester_id = UUID(str(parent_job.requested_by_user_id))
    except (TypeError, ValueError):
        mark_unavailable(result)
        return None
    if await db.get(User, requester_id) is None:
        mark_unavailable(result)
        return None
    job, _ = await enqueue_platform_job(
        db,
        SYNTHETIC_SEMANTIC_JUDGE_DEFINITION,
        SyntheticSemanticJudgePayload(
            execution_id=execution.id,
            result_id=result.id,
        ),
        dedupe_key=_dedupe_key(result.id),
        resource_lock_key=_resource_lock_key(execution.id),
        organization_id=suite.org_id,
        requested_by_user_id=parent_job.requested_by_user_id,
        requested_by_email=parent_job.requested_by_email,
        requested_by_name=parent_job.requested_by_name,
        resource_type="agent_evaluation",
        resource_id=str(execution.id),
        title=f"Evaluating semantic assertions for {parent_job.title}",
        action_url=parent_job.action_url,
    )
    try:
        persist_semantic_child_job_id(result, job.id)
    except ValueError:
        mark_unavailable(result, reason="judge_child_link_mismatch")
        return None
    return job




async def valid_semantic_child_job_id_for_result(db: Any, result: Any) -> UUID | None:
    child = await _linked_child_for_result(db, result)
    return child.id if child is not None else None


async def _linked_child_for_result(db: Any, result: Any) -> Any | None:
    from src.models.orm.agent_evaluations import AgentEvaluationExecution, AgentEvaluationSuite
    from src.models.orm.platform_jobs import PlatformJob

    child_id = semantic_child_job_id(result)
    if child_id is None:
        return None
    child = await db.get(PlatformJob, child_id)
    if child is None or child.job_type != "agent.evaluation_synthetic_semantic":
        return None
    payload = child.payload or {}
    if (
        str(payload.get("execution_id")) != str(result.execution_id)
        or str(payload.get("result_id")) != str(result.id)
    ):
        return None
    execution = await db.get(AgentEvaluationExecution, result.execution_id)
    if execution is None:
        return None
    suite = await db.get(AgentEvaluationSuite, execution.suite_id)
    if suite is None or child.organization_id != suite.org_id:
        return None
    parent = await db.get(PlatformJob, execution.platform_job_id)
    if parent is None or str(child.requested_by_user_id) != str(parent.requested_by_user_id):
        return None
    return child

async def reconcile_synthetic_semantic_child(db, result: Any) -> bool:
    """Apply terminal child loss to unresolved judge observations without replay."""
    try:
        child_id = semantic_child_job_id(result)
    except ValueError:
        return mark_unavailable(result, reason="judge_child_link_malformed")
    if child_id is None:
        return False
    child = await _linked_child_for_result(db, result)
    if child is None:
        return mark_unavailable(result, reason="judge_child_link_mismatch")
    if child.status in ACTIVE_SYNTHETIC_CHILD_STATUSES:
        return False
    if child.status in {"failed", "cancelled", "succeeded"}:
        return mark_unavailable(result)
    return False


async def synthetic_semantic_child_active(db, result: Any) -> bool:
    try:
        child_id = semantic_child_job_id(result)
    except ValueError:
        return False
    if child_id is None:
        return False
    child = await _linked_child_for_result(db, result)
    return child is not None and child.status in ACTIVE_SYNTHETIC_CHILD_STATUSES


async def run_synthetic_semantic_judge_job(context: Any, payload: Any) -> dict[str, Any]:
    from src.core.database import get_db_context

    async with get_db_context() as db:
        locked = await _locked_domain_then_child(db, context, payload)
        execution, result, _suite, _parent_job, _child_job = locked
        plans = build_judge_plans(result, execution_id=execution.id, result_id=result.id)
        await db.commit()

    completed = 0
    for plan in plans:
        attempt_id = await _begin_attempt(context, payload, plan)
        if attempt_id is None:
            continue
        call_result = await _execute_provider_call(plan)
        await _record_usage(plan, call_result)
        if await _persist_outcome(context, payload, plan, call_result.outcome):
            completed += 1
    return {
        "execution_id": str(payload.execution_id),
        "result_id": str(payload.result_id),
        "completed": completed,
    }


def _parent_cancelled(parent_job: Any) -> bool:
    return parent_job.cancel_requested_at is not None or parent_job.status in {
        "cancel_requested",
        "cancelled",
    }


def _parent_not_live(parent_job: Any) -> bool:
    return _parent_cancelled(parent_job) or parent_job.status not in ACTIVE_PARENT_STATUSES


async def _locked_domain_then_child(db: Any, context: Any, payload: Any):
    from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
    from src.models.orm.agent_evaluations import (
        AgentEvaluationExecution,
        AgentEvaluationResult,
        AgentEvaluationSuite,
    )
    from src.models.orm.platform_jobs import PlatformJob

    execution = await db.scalar(
        select(AgentEvaluationExecution)
        .where(AgentEvaluationExecution.id == payload.execution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if execution is None:
        raise PlatformJobFailure("execution_not_found", "Evaluation execution does not exist.", retryable=False)
    result = await db.scalar(
        select(AgentEvaluationResult)
        .where(
            AgentEvaluationResult.id == payload.result_id,
            AgentEvaluationResult.execution_id == payload.execution_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if result is None:
        raise PlatformJobFailure("result_not_found", "Evaluation result does not exist.", retryable=False)
    suite = await db.get(AgentEvaluationSuite, execution.suite_id)
    parent_job = await db.scalar(
        select(PlatformJob)
        .where(PlatformJob.id == execution.platform_job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if suite is None or parent_job is None:
        raise PlatformJobFailure("evaluation_owner_missing", "Evaluation owner records are missing.", retryable=False)
    _validate_domain_binding(context, execution, result, suite, parent_job)
    child_job = await _linked_child_for_result(db, result)
    if child_job is not None and child_job.id != context.job_id:
        child_job = None
    locked_at = await db.scalar(select(func.clock_timestamp()))
    if (
        child_job is None
        or child_job.lease_token != context.lease_token
        or child_job.status != "running"
        or child_job.cancel_requested_at is not None
        or child_job.lease_expires_at is None
        or locked_at is None
        or child_job.lease_expires_at <= locked_at
    ):
        raise PlatformJobCancelled
    return execution, result, suite, parent_job, child_job


def _validate_domain_binding(
    context: Any,
    execution: Any,
    result: Any,
    suite: Any,
    parent_job: Any,
) -> None:
    from src.jobs.platform.base import PlatformJobFailure

    if context.organization_id != suite.org_id:
        raise PlatformJobFailure(
            "semantic_job_tenant_mismatch",
            "Semantic judge job tenant does not match evaluation suite.",
            retryable=False,
        )
    if str(context.requested_by_user_id) != str(parent_job.requested_by_user_id):
        raise PlatformJobFailure(
            "semantic_job_requester_mismatch",
            "Semantic judge job requester does not match parent evaluation job.",
            retryable=False,
        )
    try:
        UUID(str(parent_job.requested_by_user_id))
    except (TypeError, ValueError) as exc:
        raise PlatformJobFailure(
            "semantic_job_requester_invalid",
            "Semantic judge requester is not a durable user identity.",
            retryable=False,
        ) from exc
    if result.execution_id != execution.id:
        raise PlatformJobFailure(
            "semantic_job_result_mismatch",
            "Semantic judge result does not belong to the execution.",
            retryable=False,
        )
    if execution.status not in ACTIVE_PARENT_STATUSES or _parent_not_live(parent_job):
        raise PlatformJobFailure(
            "semantic_job_parent_not_active",
            "Semantic judge parent evaluation is no longer active.",
            retryable=False,
        )
    try:
        linked_child_id = semantic_child_job_id(result)
    except ValueError as exc:
        raise PlatformJobFailure(
            "semantic_job_link_malformed",
            "Semantic judge result has a malformed child job link.",
            retryable=False,
        ) from exc
    if linked_child_id != context.job_id:
        raise PlatformJobFailure(
            "semantic_job_link_mismatch",
            "Semantic judge result is not linked to this child job.",
            retryable=False,
        )


def _plan_matches_frozen_marker(result: Any, plan: SyntheticJudgePlan, *, require_started: bool) -> bool:
    outcomes = list(result.assertion_results or [])
    if plan.assertion_index >= len(outcomes):
        return False
    current = outcomes[plan.assertion_index]
    if not isinstance(current, dict):
        return False
    if current.get("type") != "llm_judge" or current.get("side", "baseline") != plan.side:
        return False
    if require_started and current.get("judge_execution_state") != "started":
        return False
    if not require_started and current.get("judge_execution_state") in {"completed", "ambiguous", "error"}:
        return False
    existing_state = current.get("judge_execution_state")
    marker_index = current.get("assertion_index")
    marker_hash = current.get("assertion_hash")
    marker_fingerprint = current.get("request_fingerprint")
    if require_started or existing_state == "started":
        return (
            marker_index == plan.assertion_index
            and marker_hash == plan.assertion_hash
            and marker_fingerprint == plan.request_fingerprint
        )
    if any(value is not None for value in (marker_index, marker_hash, marker_fingerprint)):
        return (
            marker_index == plan.assertion_index
            and marker_hash == plan.assertion_hash
            and marker_fingerprint == plan.request_fingerprint
        )
    return existing_state in (None, "pending") and current.get("actual") == "pending"


async def _begin_attempt(context: Any, payload: Any, plan: SyntheticJudgePlan) -> UUID | None:
    from shared.quality_usage import begin_quality_usage_attempt
    from src.core.database import get_db_context
    from src.jobs.platform.base import PlatformJobFailure
    from src.models.orm.users import User

    async with get_db_context() as db:
        execution, result, suite, parent_job, _child_job = await _locked_domain_then_child(
            db, context, payload
        )
        if not _plan_matches_frozen_marker(result, plan, require_started=False):
            await db.commit()
            return None
        current_plans = {
            p.idempotency_key: p
            for p in build_judge_plans(result, execution_id=execution.id, result_id=result.id)
        }
        if plan.idempotency_key not in current_plans:
            await db.commit()
            return None
        user_id = UUID(str(parent_job.requested_by_user_id))
        if await db.get(User, user_id) is None:
            raise PlatformJobFailure(
                "semantic_job_requester_missing",
                "Semantic judge requester no longer exists.",
                retryable=False,
            )
        attempt = await begin_quality_usage_attempt(
            db,
            idempotency_key=plan.idempotency_key,
            quality_operation_type="synthetic_evaluation",
            quality_operation_id=execution.id,
            quality_operation_item_id=plan.item_id,
            usage_purpose="synthetic_semantic_judge",
            provider=plan.provider,
            model=plan.model,
            request_fingerprint=plan.request_fingerprint,
            organization_id=suite.org_id,
            user_id=user_id,
            platform_job_id=context.job_id,
            profile_id=plan.profile_id,
            profile_name=None,
            profile_fingerprint=plan.profile_fingerprint,
        )
        if not attempt.created:
            mark_plan_ambiguous(result, plan, attempt_id=attempt.attempt.id)
            await db.commit()
            return None
        mark_started(result, plan, attempt_id=attempt.attempt.id)
        await db.commit()
        return attempt.attempt.id


@dataclass(frozen=True)
class _CallResult:
    outcome: dict[str, Any]
    usage: dict[str, Any] | None
    unobserved_reason: str | None = None
    duration_ms: int | None = None


async def _execute_provider_call(plan: SyntheticJudgePlan) -> _CallResult:
    from src.core.database import get_db_context
    from src.services.llm import LLMMessage
    from src.services.llm.factory import get_llm_config
    from src.services.llm.pydantic_client import PydanticAIClient

    params = plan.assertion.get("params") or {}
    snapshot = dict(params.get("judge_snapshot") or {})
    try:
        async with get_db_context() as db:
            config = await get_llm_config(db, profile_id=plan.profile_id)
            for field in (
                "provider",
                "model",
                "endpoint",
                "openai_transport",
                "anthropic_prompt_cache_supported",
                "default_max_tokens",
                "extra_params",
            ):
                if getattr(config, field) != snapshot.get(field):
                    raise ValueError("configured judge no longer matches frozen snapshot")
        client = PydanticAIClient(config)
        started = time.perf_counter()
        response = await client.complete(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "Evaluate the rubric against the redacted evidence. "
                        "Return JSON only: {\"score\": number 0..1, \"rationale\": string}."
                    ),
                ),
                LLMMessage(role="user", content=json.dumps(plan.request_payload, default=str)),
            ],
            model=plan.model,
        )
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        usage = usage_from_response(response)
        try:
            verdict = json.loads(response.content or "{}")
            score = float(verdict["score"])
            if not 0 <= score <= 1:
                raise ValueError("judge score must be between 0 and 1")
            rationale = str(verdict.get("rationale") or "")
            if len(rationale) > MAX_SYNTHETIC_JUDGE_RATIONALE_CHARS:
                raise ValueError("judge rationale exceeds the bounded size limit")
            passed = score >= float(params["threshold"])
            return _CallResult(
                completed_outcome(plan, score=score, detail=rationale, passed=passed, usage=usage),
                usage,
                duration_ms=duration_ms,
            )
        except Exception:
            return _CallResult(
                completed_outcome(
                    plan,
                    score=None,
                    detail="semantic judge returned malformed or invalid structured output",
                    passed=False,
                    reason="judge_invalid_response",
                    usage=usage,
                ),
                usage,
                None if usage is not None else "missing_usage",
                duration_ms=duration_ms if usage is not None else None,
            )
    except Exception:
        return _CallResult(
            completed_outcome(
                plan,
                score=None,
                detail="semantic judge provider or configuration failed",
                passed=False,
                reason="judge_provider_error",
                usage=None,
            ),
            None,
            "provider_error",
        )


async def _record_usage(plan: SyntheticJudgePlan, call_result: _CallResult) -> None:
    from shared.quality_usage import mark_quality_usage_unobserved, record_quality_usage_observation
    from src.core.database import get_db_context

    async with get_db_context() as db:
        if call_result.usage is not None:
            await record_quality_usage_observation(
                db,
                idempotency_key=plan.idempotency_key,
                quality_operation_type="synthetic_evaluation",
                quality_operation_id=plan.execution_id,
                usage_purpose="synthetic_semantic_judge",
                provider=plan.provider,
                model=plan.model,
                request_fingerprint=plan.request_fingerprint,
                input_tokens=call_result.usage["input_tokens"],
                output_tokens=call_result.usage["output_tokens"],
                cache_read_tokens=call_result.usage.get("cache_read_tokens", 0),
                cache_write_tokens=call_result.usage.get("cache_write_tokens", 0),
                provider_cost=call_result.usage.get("provider_cost"),
                duration_ms=call_result.duration_ms,
                quality_operation_item_id=plan.item_id,
            )
        else:
            await mark_quality_usage_unobserved(
                db,
                idempotency_key=plan.idempotency_key,
                reason=call_result.unobserved_reason or "missing_usage",
            )
        await db.commit()


async def _persist_outcome(
    context: Any,
    payload: Any,
    plan: SyntheticJudgePlan,
    outcome: dict[str, Any],
) -> bool:
    from src.core.database import get_db_context
    from src.jobs.platform.base import PlatformJobFailure

    async with get_db_context() as db:
        try:
            _execution, result, _suite, _parent_job, _child_job = await _locked_domain_then_child(
                db, context, payload
            )
        except PlatformJobFailure as exc:
            if exc.code == "semantic_job_parent_not_active":
                await db.rollback()
                return False
            raise
        if not _plan_matches_frozen_marker(result, plan, require_started=True):
            await db.commit()
            return False
        mark_completed(result, plan, outcome)
        await db.commit()
        return True
