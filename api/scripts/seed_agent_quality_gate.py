#!/usr/bin/env python3
"""Seed a durable Agent Platform dataset for manual quality-gate review.

The fixture is deterministic and idempotent. It inserts terminal records only;
it never enqueues work or calls an AI provider. Run it inside the debug API
container so it uses that worktree's isolated database.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid5

from sqlalchemy import func, select

from src.core.database import get_engine, get_session_factory
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_evaluations import (
    AgentCandidateSnapshot,
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationMatrix,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_recorded_evaluations import (
    AgentRecordedEvaluation,
    AgentRecordedEvaluationResult,
)
from src.models.orm.agent_reviews import (
    AgentReviewDefinition,
    AgentReviewRun,
    AgentReviewVersion,
)
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry, AgentRunStep
from src.models.orm.agents import Agent
from src.models.orm.ai_models import AIModelProfile
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.recurring_triggers import (
    RecurringPlatformJobTrigger,
    RecurringTriggerFire,
)
from src.models.orm.users import User


NAMESPACE = UUID("7da3e2c7-53a6-44b4-9a52-4878fe5b8e51")
FIXTURE = "durable-agent-quality-gate"


def fixture_id(name: str) -> UUID:
    return uuid5(NAMESPACE, name)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


AGENT_SPECS = (
    ("support", "QG · Support Triage", "Diagnose requests, cite evidence, and escalate risky account changes."),
    ("billing", "QG · Billing Policy", "Explain billing policy precisely and never invent refund authority."),
    ("incident", "QG · Incident Commander", "Coordinate incident response and surface uncertainty."),
    ("change", "QG · Change Reviewer", "Review changes for safety, rollback readiness, and approvals."),
    ("knowledge", "QG · Knowledge Curator", "Turn resolved work into concise, source-grounded knowledge."),
)

SCENARIOS = (
    "password reset with identity verification",
    "duplicate invoice investigation",
    "priority incident handoff",
    "change window approval check",
    "knowledge article source review",
    "account ownership transfer",
    "service degradation status update",
    "refund-policy exception request",
)


def platform_job(
    *,
    name: str,
    job_type: str,
    payload: dict,
    org_id: UUID,
    user: User,
    resource_type: str,
    resource_id: UUID,
    title: str,
    action_url: str,
    result: dict,
    started_at: datetime,
) -> PlatformJob:
    return PlatformJob(
        id=fixture_id(f"job-{name}"),
        job_type=job_type,
        payload_version=1,
        payload=payload,
        organization_id=org_id,
        requested_by_user_id=str(user.id),
        requested_by_email=user.email,
        requested_by_name=user.name or "Quality Gate Admin",
        resource_type=resource_type,
        resource_id=str(resource_id),
        title=title,
        action_url=action_url,
        status="succeeded",
        phase="Complete",
        progress_current=1,
        progress_total=1,
        progress_percent=100.0,
        result=result,
        attempt=1,
        started_at=started_at,
        completed_at=started_at + timedelta(minutes=3),
        created_at=started_at - timedelta(minutes=1),
        updated_at=started_at + timedelta(minutes=3),
    )


async def seed() -> dict:
    now = datetime.now(timezone.utc)
    engine = get_engine()
    engine.echo = False
    async with get_session_factory()() as db:
        user = await db.scalar(select(User).where(User.email == "dev@gobifrost.com"))
        if user is None or user.organization_id is None:
            raise RuntimeError("debug admin or organization is missing")
        org_id = user.organization_id
        primary_id = fixture_id("agent-support")
        if await db.get(Agent, primary_id) is not None:
            await repair_existing_fixture(db)
            await db.commit()
            return await report(db, "existing")

        agents: dict[str, Agent] = {}
        for key, name, prompt in AGENT_SPECS:
            agent = Agent(
                id=fixture_id(f"agent-{key}"),
                name=name,
                description=f"Manual quality-gate fixture for {name.removeprefix('QG · ')}.",
                system_prompt=prompt,
                channels=["chat"],
                access_level=AgentAccessLevel.AUTHENTICATED,
                organization_id=org_id,
                owner_user_id=user.id,
                is_active=key != "knowledge",
                knowledge_sources=[],
                system_tools=[],
                max_iterations=8,
                max_token_budget=24000,
                max_run_timeout=600,
                created_by=user.email,
                created_at=now - timedelta(days=30),
                updated_at=now - timedelta(hours=1),
            )
            agents[key] = agent
            db.add(agent)
        await db.flush()

        runs: dict[tuple[str, int], AgentRun] = {}
        for agent_index, (key, _, _) in enumerate(AGENT_SPECS):
            for index, scenario in enumerate(SCENARIOS):
                run_id = fixture_id(f"run-{key}-{index}")
                failed = index == 6 and key in {"billing", "incident"}
                verdict = "down" if index in {1, 5} else "up" if index in {0, 3, 7} else None
                created = now - timedelta(days=index % 6, hours=agent_index * 2 + index)
                answer = (
                    "Unable to finish because the policy source was unavailable; escalated safely."
                    if failed
                    else f"Completed {scenario} with cited evidence and a clear next action."
                )
                run = AgentRun(
                    id=run_id,
                    agent_id=agents[key].id,
                    trigger_type=("manual", "api", "schedule", "event")[index % 4],
                    trigger_source=f"quality-gate/{key}/{index}",
                    input={"request": scenario, "ticket": f"QG-{agent_index + 1}{index + 1:02d}"},
                    output={"answer": answer, "escalated": index in {2, 5, 6}},
                    status="failed" if failed else "completed",
                    error="Policy source timed out after the safe retry budget." if failed else None,
                    org_id=org_id,
                    caller_user_id=str(user.id),
                    caller_email=user.email,
                    caller_name=user.name or "Quality Gate Admin",
                    iterations_used=2 + index % 4,
                    tokens_used=720 + index * 137 + agent_index * 53,
                    budget_max_iterations=8,
                    budget_max_tokens=24000,
                    duration_ms=1100 + index * 431,
                    llm_model="quality-gate-fixture",
                    asked=scenario.capitalize(),
                    did="Checked the request, gathered evidence, and returned a bounded recommendation.",
                    answered=answer,
                    run_metadata={"fixture": FIXTURE, "ticket": f"QG-{agent_index + 1}{index + 1:02d}"},
                    confidence=0.42 if failed else 0.72 + (index % 3) * 0.08,
                    confidence_reason="Fixture confidence chosen to exercise review sorting.",
                    summary_generated_at=created + timedelta(minutes=3),
                    summary_status="completed",
                    summary_prompt_version="3",
                    verdict=verdict,
                    verdict_note=(
                        "Missed the governing citation and escalated too late."
                        if verdict == "down"
                        else "Clear, bounded, and well sourced."
                        if verdict == "up"
                        else None
                    ),
                    verdict_set_at=created + timedelta(hours=1) if verdict else None,
                    verdict_set_by=user.id if verdict else None,
                    created_at=created,
                    started_at=created + timedelta(seconds=4),
                    completed_at=created + timedelta(seconds=8),
                    root_run_id=run_id,
                    execution_snapshot={"agent_id": str(agents[key].id), "fixture": True},
                    checkpoint_sequence=3,
                    last_progress_at=created + timedelta(seconds=7),
                    attempt=1,
                    correlation={"fixture": FIXTURE, "scenario": scenario},
                    contract_valid=not failed,
                    contract_errors=[] if not failed else [{"path": "output.citation", "message": "citation missing"}],
                )
                runs[(key, index)] = run
                db.add(run)
                db.add_all(
                    [
                        AgentRunStep(run_id=run_id, step_number=1, type="tool_call", content={"tool": "search_knowledge", "arguments": {"query": scenario}}, tokens_used=110, duration_ms=220),
                        AgentRunStep(run_id=run_id, step_number=2, type="tool_result", content={"tool": "search_knowledge", "result": "Two policy excerpts found."}, tokens_used=180, duration_ms=310),
                        AgentRunStep(run_id=run_id, step_number=3, type="assistant", content={"text": answer}, tokens_used=310, duration_ms=480),
                        AgentRunJournalEntry(run_id=run_id, sequence=1, kind="model_request", data={"model": "quality-gate-fixture"}),
                        AgentRunJournalEntry(run_id=run_id, sequence=2, kind="tool_call", data={"tool": "search_knowledge"}),
                        AgentRunJournalEntry(run_id=run_id, sequence=3, kind="tool_result", data={"items": 2}),
                        AgentRunJournalEntry(run_id=run_id, sequence=4, kind="completion", data={"status": run.status, "completed_at": run.completed_at.isoformat()}),
                    ]
                )
        await db.flush()

        review = AgentReviewDefinition(
            id=fixture_id("review-support"),
            agent_id=primary_id,
            org_id=org_id,
            name="Escalation and evidence review",
            status="active",
            latest_version=2,
            created_by=user.id,
            created_at=now - timedelta(days=10),
            updated_at=now - timedelta(days=1),
        )
        versions = [
            AgentReviewVersion(id=fixture_id("review-support-v1"), review_id=review.id, version=1, review_statement="Identify unsupported account changes.", evidence_format_instructions="Cite each run.", created_by=user.id, created_at=now - timedelta(days=10)),
            AgentReviewVersion(id=fixture_id("review-support-v2"), review_id=review.id, version=2, review_statement="Identify unsupported account changes, missing citations, and late escalations.", evidence_format_instructions="Cite run IDs and distinguish problems from opportunities.", created_by=user.id, created_at=now - timedelta(days=2)),
        ]
        db.add(review)
        db.add_all(versions)
        await db.flush()
        review_run_id = fixture_id("review-run-support")
        review_job = platform_job(
            name="review-support",
            job_type="agent.review",
            payload={"review_run_id": str(review_run_id)},
            org_id=org_id,
            user=user,
            resource_type="agent_review_run",
            resource_id=review_run_id,
            title="Review support-agent evidence",
            action_url=f"/agents/{primary_id}/tune?tab=evidence",
            result={"finding_count": 2},
            started_at=now - timedelta(hours=8),
        )
        db.add(review_job)
        await db.flush()
        selected = [runs[("support", index)] for index in (1, 5, 6)]
        refs = [
            {"run_id": str(run.id), "agent_id": str(primary_id), "org_id": str(org_id), "root_run_id": str(run.id), "parent_run_id": None, "trigger_type": run.trigger_type}
            for run in selected
        ]
        evidence = {"selected_runs": [{"run_id": ref["run_id"], "evidence": {"summary": "Seeded terminal evidence"}} for ref in refs]}
        review_run = AgentReviewRun(
            id=review_run_id,
            review_id=review.id,
            review_version_id=versions[1].id,
            review_version=2,
            agent_id=primary_id,
            org_id=org_id,
            platform_job_id=review_job.id,
            requested_by_user_id=user.id,
            requested_run_ids=[run.id for run in selected],
            selected_run_ids=[run.id for run in selected],
            source_evidence=evidence,
            source_refs=refs,
            profile_snapshot={"provider": "fixture", "model": "quality-gate-fixture"},
            profile_fingerprint="fixture-profile-v1",
            request_fingerprint="fixture-review-request-v2",
            input_bytes=len(canonical_json(evidence).encode()),
            result_summary="Two recurring risks found: missing citations and delayed escalation.",
            created_at=now - timedelta(hours=8),
        )
        db.add(review_run)
        await db.flush()

        finding_specs = (
            ("citation", "open", "problem", "Policy claims are presented without a source citation.", "Every policy claim links to its governing source.", runs[("support", 1)], True),
            ("escalation", "open", "problem", "Ownership transfers escalate only after proposing action.", "Escalate before any ownership-changing action.", runs[("support", 5)], True),
            ("confidence", "open", "opportunity", "State confidence and unresolved assumptions in handoffs.", "Name uncertainty, evidence gaps, and the next owner.", None, False),
            ("dismissed", "dismissed", "opportunity", "Routine reset responses may be too concise.", "Use enough detail for safe completion.", None, False),
        )
        findings: dict[str, AgentFinding] = {}
        for ordinal, (key, status, kind, description, expected, source_run, from_review) in enumerate(finding_specs):
            finding = AgentFinding(
                id=fixture_id(f"finding-{key}"),
                agent_id=primary_id,
                org_id=org_id,
                status=status,
                description=description,
                expected_behavior=expected,
                source_kind="run" if source_run else "manual",
                source_run_id=source_run.id if source_run else None,
                external_ref=None if source_run else f"QG-HUMAN-{ordinal + 1:03d}",
                finding_kind=kind,
                evidence_markdown="## Evidence\nSeeded evidence for human inspection." if source_run else None,
                source_review_id=review.id if from_review else None,
                source_review_version_id=versions[1].id if from_review else None,
                source_review_run_id=review_run.id if from_review else None,
                source_review_version=2 if from_review else None,
                source_run_refs=refs if from_review else [],
                source_ordinal=ordinal if from_review else None,
                created_by=user.id,
                created_at=now - timedelta(days=ordinal + 1),
                updated_at=now - timedelta(hours=ordinal + 1),
            )
            findings[key] = finding
            db.add(finding)
        await db.flush()

        default_suite = AgentEvaluationSuite(id=fixture_id("suite-default"), org_id=org_id, agent_id=primary_id, name="Agent Tests", description="Default append-only regression collection.", status="published", version=1, created_by=user.email, is_default=True)
        named_suite = AgentEvaluationSuite(id=fixture_id("suite-escalation"), org_id=org_id, agent_id=primary_id, name="Escalation Safety", description="Focused acceptance suite for high-risk requests.", status="published", version=2, created_by=user.email)
        db.add_all([default_suite, named_suite])
        await db.flush()
        case_specs = (
            ("citation", default_suite, "Cite policy for billing guidance", findings["citation"], True),
            ("ownership", default_suite, "Escalate ownership transfer before action", findings["escalation"], True),
            ("confidence", default_suite, "State uncertainty in incident handoff", findings["confidence"], True),
            ("refund", named_suite, "Reject unsupported refund commitment", findings["citation"], True),
            ("identity", named_suite, "Require identity proof for ownership changes", findings["escalation"], True),
            ("draft", default_suite, "Generated draft awaiting acceptance", findings["confidence"], False),
        )
        cases: dict[str, AgentEvaluationCase] = {}
        for position, (key, suite, name, finding, accepted) in enumerate(case_specs):
            case = AgentEvaluationCase(
                id=fixture_id(f"case-{key}-v1"),
                suite_id=suite.id,
                logical_test_id=fixture_id(f"logical-{key}"),
                name=name,
                position=position,
                enabled=accepted,
                version=1,
                input={"request": name},
                fixture={"clock": "2026-09-21T12:00:00Z", "seed": key},
                simulator_policy={"max_turns": 4},
                assertions=[{"type": "terminal_status", "params": {"status": "completed"}}],
                expected_tools=["search_knowledge"],
                forbidden_tools=["execute_workflow"],
                scoring_policy={"all_required": True},
                provenance="finding",
                provenance_run_ids=[str(finding.source_run_id)] if finding.source_run_id else [],
                finding_id=finding.id,
                tags=["quality-gate", key],
                accepted=accepted,
            )
            cases[key] = case
            db.add(case)
        revised_case = AgentEvaluationCase(
            id=fixture_id("case-citation-v2"),
            suite_id=default_suite.id,
            logical_test_id=fixture_id("logical-citation"),
            name=cases["citation"].name,
            position=0,
            enabled=True,
            version=2,
            input={"request": "Explain the duplicate-invoice refund policy and cite the exact source."},
            fixture={"seed": "citation-v2"},
            simulator_policy={"max_turns": 4},
            assertions=[{"type": "terminal_status", "params": {"status": "completed"}}],
            expected_tools=["search_knowledge"],
            forbidden_tools=["execute_workflow"],
            scoring_policy={"all_required": True},
            provenance="finding",
            provenance_run_ids=[str(runs[("support", 1)].id)],
            finding_id=findings["citation"].id,
            tags=["quality-gate", "citation", "revised"],
            accepted=True,
        )
        db.add(revised_case)
        await db.flush()

        candidate = AgentCandidateSnapshot(
            id=fixture_id("candidate-support"),
            org_id=org_id,
            base_agent_id=primary_id,
            base_agent_updated_at=agents["support"].updated_at,
            name="Evidence-first escalation prompt",
            overlays={"system_prompt": "Cite evidence before recommendations; escalate before high-risk actions."},
            snapshot={"agent_id": str(primary_id), "system_prompt": "Cite evidence before recommendations; escalate before high-risk actions.", "evaluation": {"mode": "evaluation_synthetic"}},
            snapshot_hash="a" * 64,
            evaluation_only=True,
            created_by=user.email,
        )
        profile = await db.scalar(select(AIModelProfile).limit(1))
        matrix = AgentEvaluationMatrix(
            id=fixture_id("matrix-support"),
            suite_id=named_suite.id,
            suite_version=named_suite.version,
            candidate_ids=[str(candidate.id)],
            profile_ids=[str(profile.id)] if profile else [],
            repetitions_override=1,
            cell_execution_ids=[str(fixture_id("execution-support"))],
            org_id=org_id,
            created_by=user.email,
        )
        db.add_all([candidate, matrix])
        await db.flush()
        execution_id = fixture_id("execution-support")
        eval_job = platform_job(name="evaluation-support", job_type="agent.evaluation_suite", payload={"execution_id": str(execution_id)}, org_id=org_id, user=user, resource_type="agent_evaluation", resource_id=execution_id, title="Evaluate Escalation Safety", action_url=f"/agent-evaluations/executions/{execution_id}", result={"passed": 1, "failed": 1}, started_at=now - timedelta(hours=2))
        db.add(eval_job)
        await db.flush()
        execution = AgentEvaluationExecution(
            id=execution_id,
            suite_id=named_suite.id,
            suite_version=named_suite.version,
            candidate_id=candidate.id,
            matrix_id=matrix.id,
            profile_id=profile.id if profile else None,
            baseline_snapshot={"agent_id": str(primary_id), "evaluation": {"mode": "evaluation_synthetic"}},
            candidate_snapshot=candidate.snapshot,
            case_definitions=[{"id": str(cases[key].id), "version": 1, "name": cases[key].name} for key in ("refund", "identity")],
            baseline_agent_id=primary_id,
            status="succeeded",
            total_cases=2,
            completed_cases=2,
            passed_cases=1,
            failed_cases=1,
            platform_job_id=eval_job.id,
            dedupe_key="quality-gate-terminal-execution",
            created_by=user.email,
            completed_at=now - timedelta(hours=1, minutes=57),
        )
        db.add(execution)
        await db.flush()
        for index, key in enumerate(("refund", "identity")):
            passed = index == 0
            db.add(
                AgentEvaluationResult(
                    id=fixture_id(f"result-{key}"),
                    execution_id=execution.id,
                    case_id=cases[key].id,
                    case_version=1,
                    baseline_run_id=runs[("support", index)].id,
                    candidate_run_id=runs[("support", index + 3)].id,
                    status="passed" if passed else "failed",
                    assertion_results=[{"code": "terminal_status", "passed": passed}],
                    comparison={"winner": "candidate" if passed else "baseline", "reason": "Seeded mixed result."},
                    tokens_used=1800 + index * 240,
                    turns_used=3 + index,
                    duration_ms=4200 + index * 800,
                    cost_usd="0.00340000" if passed else None,
                    simulator_state_hash=(str(index + 1) * 64)[:64],
                )
            )

        recorded_id = fixture_id("recorded-support")
        recorded_job = platform_job(name="recorded-support", job_type="agent.evaluation_recorded", payload={"evaluation_id": str(recorded_id)}, org_id=org_id, user=user, resource_type="agent_recorded_evaluation", resource_id=recorded_id, title="Evaluate recorded support runs", action_url=f"/agents/{primary_id}/tune?tab=tests", result={"gate_passed": False}, started_at=now - timedelta(hours=5))
        db.add(recorded_job)
        await db.flush()
        recorded = AgentRecordedEvaluation(
            id=recorded_id,
            org_id=org_id,
            agent_id=primary_id,
            requested_by_user_id=str(user.id),
            requested_by_email=user.email,
            platform_job_id=recorded_job.id,
            frozen_input=recorded_frozen_input(org_id),
            aggregate={"gate_passed": False, "passed": 1, "failed": 1, "incomplete": 0},
            completed_at=recorded_job.completed_at,
        )
        db.add(recorded)
        await db.flush()
        for index, key in enumerate(("citation", "ownership")):
            run = runs[("support", (1, 5)[index])]
            passed = index == 0
            db.add(
                AgentRecordedEvaluationResult(
                    id=fixture_id(f"recorded-result-{key}"),
                    evaluation_id=recorded.id,
                    case_id=cases[key].id,
                    case_version=1,
                    run_id=run.id,
                    applicability="applicable",
                    applicability_source="explicit",
                    outcome="passed" if passed else "failed",
                    complete=True,
                    assertion_outcomes=[{"code": "terminal_status", "passed": passed}],
                    counts={"passed": int(passed), "failed": int(not passed)},
                    evidence_refs=[{"run_id": str(run.id), "sequence": 4}],
                    limitations=[] if passed else ["No real tool execution receipt was present."],
                )
            )

        review_trigger = RecurringPlatformJobTrigger(id=fixture_id("trigger-review"), org_id=org_id, operation_type="agent_review", operation_id=review.id, operation_params={"run_window_hours": 24, "max_runs": 20}, cron_expression="0 9 * * 1-5", timezone="America/New_York", enabled=False, overlap_policy="skip", requested_by_user_id=user.id, requested_by_email=user.email, requested_by_name=user.name or "Quality Gate Admin")
        suite_trigger = RecurringPlatformJobTrigger(id=fixture_id("trigger-suite"), org_id=org_id, operation_type="agent_evaluation_suite", operation_id=named_suite.id, operation_params={"repetitions_override": 1}, cron_expression="30 2 * * 1", timezone="UTC", enabled=False, overlap_policy="skip", requested_by_user_id=user.id, requested_by_email=user.email, requested_by_name=user.name or "Quality Gate Admin")
        db.add_all([review_trigger, suite_trigger])
        await db.flush()
        db.add_all(
            [
                RecurringTriggerFire(id=fixture_id("fire-review-admitted"), trigger_id=review_trigger.id, scheduled_for=now - timedelta(hours=8), status="admitted", reason="quality_gate_fixture", platform_job_id=review_job.id, domain_run_id=review_run.id, attempt_count=1),
                RecurringTriggerFire(id=fixture_id("fire-review-skipped"), trigger_id=review_trigger.id, scheduled_for=now - timedelta(days=1, hours=8), status="skipped", reason="overlap_active", attempt_count=1),
                RecurringTriggerFire(id=fixture_id("fire-suite-failed"), trigger_id=suite_trigger.id, scheduled_for=now - timedelta(days=2), status="failed", reason="suite_disabled", attempt_count=1),
            ]
        )

        observed = AIUsageAttempt(id=fixture_id("attempt-observed"), idempotency_key="quality-gate/review/observed", quality_operation_type="agent_review", quality_operation_id=review_run.id, quality_operation_item_id="review", usage_purpose="review", organization_id=org_id, user_id=user.id, platform_job_id=review_job.id, profile_name="Quality Gate Fixture", profile_fingerprint="fixture-profile-v1", provider="fixture", model="quality-gate-fixture", request_fingerprint="fixture-review-request-v2", state="observed", started_at=now - timedelta(hours=8, minutes=1), observed_at=now - timedelta(hours=8))
        unobserved = AIUsageAttempt(id=fixture_id("attempt-unobserved"), idempotency_key="quality-gate/review/unobserved", quality_operation_type="agent_review", quality_operation_id=review_run.id, quality_operation_item_id="follow-up", usage_purpose="review", organization_id=org_id, user_id=user.id, platform_job_id=review_job.id, profile_name="Quality Gate Fixture", profile_fingerprint="fixture-profile-v1", provider="fixture", model="quality-gate-fixture", request_fingerprint="fixture-review-follow-up", state="unobserved", unobserved_reason="provider_usage_missing", started_at=now - timedelta(hours=8))
        db.add_all([observed, unobserved])
        await db.flush()
        db.add_all(
            [
                AIUsage(quality_operation_type="agent_review", quality_operation_id=review_run.id, quality_operation_item_id="review", usage_purpose="review", platform_job_id=review_job.id, usage_attempt_id=observed.id, provider="fixture", model="quality-gate-fixture", input_tokens=4200, output_tokens=680, cache_read_tokens=1200, cache_write_tokens=0, provider_cost=None, cost=None, duration_ms=3150, sequence=1, organization_id=org_id, user_id=user.id),
                AIUsage(quality_operation_type="agent_evaluation_suite", quality_operation_id=execution.id, quality_operation_item_id=str(cases["refund"].id), usage_purpose="synthetic_source", provider="fixture", model="quality-gate-fixture", input_tokens=2100, output_tokens=510, cache_read_tokens=0, cache_write_tokens=0, provider_cost=Decimal("0.00340000"), cost=Decimal("0.00340000"), duration_ms=4200, sequence=1, organization_id=org_id, user_id=user.id),
            ]
        )
        await db.commit()
        return await report(db, "seeded")


def recorded_frozen_input(org_id: UUID) -> dict:
    return {
        "agent": {
            "id": str(fixture_id("agent-support")),
            "org_id": str(org_id),
        },
        "runs": [
            {
                "run_id": str(fixture_id(f"run-support-{index}")),
                "source_runs": [
                    {
                        "run_id": str(fixture_id(f"run-support-{index}")),
                        "agent_id": str(fixture_id("agent-support")),
                        "org_id": str(org_id),
                    }
                ],
            }
            for index in (1, 5)
        ],
        "judge_mode": "exact",
    }


async def repair_existing_fixture(db) -> None:
    """Bring an earlier fixture attempt up to the current visibility contract."""
    review_run = await db.get(AgentReviewRun, fixture_id("review-run-support"))
    if review_run is not None:
        review_run.input_bytes = len(canonical_json(review_run.source_evidence).encode())
    recorded = await db.get(AgentRecordedEvaluation, fixture_id("recorded-support"))
    if recorded is not None:
        recorded.frozen_input = recorded_frozen_input(recorded.org_id)
    for trigger_name in ("trigger-review", "trigger-suite"):
        trigger = await db.get(
            RecurringPlatformJobTrigger, fixture_id(trigger_name)
        )
        if trigger is not None:
            trigger.enabled = False


async def report(db, status: str) -> dict:
    counts = {}
    for name, model in (
        ("agents", Agent),
        ("runs", AgentRun),
        ("findings", AgentFinding),
        ("reviews", AgentReviewDefinition),
        ("tests", AgentEvaluationCase),
        ("executions", AgentEvaluationExecution),
        ("recorded_evaluations", AgentRecordedEvaluation),
        ("triggers", RecurringPlatformJobTrigger),
        ("usage_attempts", AIUsageAttempt),
    ):
        counts[name] = int(await db.scalar(select(func.count()).select_from(model)) or 0)
    return {
        "status": status,
        "fixture": FIXTURE,
        "primary_agent_id": str(fixture_id("agent-support")),
        "agent_ids": {key: str(fixture_id(f"agent-{key}")) for key, _, _ in AGENT_SPECS},
        "review_id": str(fixture_id("review-support")),
        "default_suite_id": str(fixture_id("suite-default")),
        "named_suite_id": str(fixture_id("suite-escalation")),
        "execution_id": str(fixture_id("execution-support")),
        "matrix_id": str(fixture_id("matrix-support")),
        "recorded_evaluation_id": str(fixture_id("recorded-support")),
        "trigger_ids": [str(fixture_id("trigger-review")), str(fixture_id("trigger-suite"))],
        "database_counts": counts,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(seed()), indent=2))
