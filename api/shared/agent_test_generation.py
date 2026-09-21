"""Generate tests from authorized findings/evidence (Phase 4f).

Explicit user selection of findings is the approval for generation: dismissed
findings stay selectable, and no approval workflow is added. Every finding,
source run, and evidence reference is reauthorized at generation time;
missing or revoked sources fail closed. Generated drafts are disabled and
unaccepted until explicit acceptance. Callers own commit/publish.
"""

from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_test_collection import get_or_create_default_suite
from shared.agent_finding_visibility import visible_agent_finding_condition
from shared.models import AgentTestsGenerateCreate
from src.core.principal import UserPrincipal
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent

_MAX_SOURCE_RUNS = 10


class GenerationError(Exception):
    def __init__(
        self,
        code: str,
        public_detail: str = "Generation not available.",
        http_status: int = 422,
    ) -> None:
        super().__init__(public_detail)
        self.code = code
        self.public_detail = public_detail
        self.http_status = http_status


def _generation_error(code: str, detail: str, http_status: int) -> NoReturn:
    raise GenerationError(code, public_detail=detail, http_status=http_status)


async def _agent_or_404(
    db: AsyncSession, user: UserPrincipal, agent_id: UUID
) -> Agent:
    from shared.evaluation_matrix_admission import _entity_access_allowed
    from sqlalchemy.orm import selectinload

    # Snapshot + access need tools, delegations, and roles eager.
    agent = (
        await db.execute(
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.roles),
            )
            .where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    if agent is None or not agent.is_active:
        _generation_error("agent_not_found", "Agent not found.", 404)
    if not await _entity_access_allowed(agent, user, db):
        _generation_error("agent_not_found", "Agent not found.", 404)
    return agent


async def collect_finding_evidence(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent: Agent,
    finding_ids: list[UUID],
) -> tuple[list[dict[str, Any]], list[AgentRun]]:
    """Authorize findings and gather bounded evidence + source runs."""
    from src.services.execution.agent_run_access import agent_run_visibility_conditions

    if not finding_ids or len(finding_ids) > 10:
        _generation_error(
            "invalid_selection", "Select 1-10 findings to generate from.", 422
        )
    if len(set(finding_ids)) != len(finding_ids):
        _generation_error(
            "invalid_selection", "Finding IDs must be unique.", 422
        )
    evidence: list[dict[str, Any]] = []
    wanted_run_ids: list[UUID] = []
    for finding_id in finding_ids:
        finding = (
            await db.execute(
                select(AgentFinding).where(
                    AgentFinding.id == finding_id,
                    visible_agent_finding_condition(user),
                )
            )
        ).scalar_one_or_none()
        if finding is None:
            _generation_error("finding_not_found", "Finding not found.", 404)
        if finding.agent_id != agent.id:
            _generation_error(
                "finding_wrong_agent",
                "Findings must belong to the tested agent.",
                422,
            )
        refs = finding.source_run_refs if isinstance(finding.source_run_refs, list) else []
        for ref in refs:
            if isinstance(ref, dict) and isinstance(ref.get("run_id"), str):
                try:
                    wanted_run_ids.append(UUID(str(ref["run_id"])))
                except ValueError:
                    _generation_error(
                        "finding_not_found", "Finding not found.", 404
                    )
        if finding.source_run_id is not None:
            wanted_run_ids.append(finding.source_run_id)
        evidence.append(
            {
                "finding_id": str(finding.id),
                "kind": finding.finding_kind or "problem",
                "status": finding.status,
                "description": finding.description,
                "expected_behavior": finding.expected_behavior,
                "evidence_markdown": finding.evidence_markdown,
            }
        )
    # Bounded, deduplicated source runs preserving selection order. Every
    # referenced run is authorized first; only the history handed to the
    # designer is capped, never the authorization set.
    ordered: list[UUID] = []
    for run_id in wanted_run_ids:
        if run_id not in ordered:
            ordered.append(run_id)
    runs: list[AgentRun] = []
    if ordered:
        rows = (
            (
                await db.execute(
                    select(AgentRun).where(
                        AgentRun.id.in_(ordered),
                        *agent_run_visibility_conditions(user),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        for run_id in ordered:
            row = by_id.get(run_id)
            if row is None:
                _generation_error("finding_not_found", "Finding not found.", 404)
            if row.org_id != agent.organization_id or (
                row.agent_id is not None and row.agent_id != agent.id
            ):
                _generation_error("finding_not_found", "Finding not found.", 404)
            runs.append(row)
    history_runs = runs[:_MAX_SOURCE_RUNS]
    return evidence, history_runs


async def admit_finding_generation(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    body: AgentTestsGenerateCreate,
):
    """Authorize selection and admit one designer run; caller commits/publishes."""
    from src.models.orm.ai_models import AIModelAssignment
    from src.services.agent_evaluations import quotas as eval_quotas
    from src.services.agent_evaluations.test_designer import (
        TESTING_ASSIGNMENT_HELP,
        TESTING_ASSIGNMENT_KEY,
        DesignerError,
        build_designer_input,
        build_designer_snapshot,
        designer_testing_model,
        load_designer_history,
    )
    from src.services.agent_runtime.execution_snapshot import snapshot_agent
    from src.services.agent_evaluations.runner import (
        admit_synthetic_run,
        build_synthetic_correlation,
    )
    from src.services.agent_evaluations.simulator_models import (
        canonical_hash,
        fresh_state,
    )
    from src.models.orm.agent_evaluations import AgentSimulationSession
    from src.services.ai_model_service import AIModelService

    agent = await _agent_or_404(db, user, agent_id)
    org_id = agent.organization_id or user.organization_id
    if org_id is None:
        _generation_error("org_required", "Generation requires an organization scope.", 422)
    suite = await get_or_create_default_suite(
        db, agent_id=agent.id, org_id=org_id, created_by=user.email
    )
    evidence, source_runs = await collect_finding_evidence(
        db, user, agent=agent, finding_ids=list(body.finding_ids)
    )
    try:
        eval_quotas.check_designer_proposal_count(body.requested_count)
    except eval_quotas.QuotaExceeded as exc:
        raise GenerationError("quota_exceeded", str(exc), exc.status_code) from exc
    target_snapshot = await snapshot_agent(db, agent, caller_user_id=user.user_id)
    tool_schemas = {
        tool["name"]: dict(tool.get("parameters") or {})
        for tool in target_snapshot.get("tools", [])
    }
    testing_assignment = await db.scalar(
        select(AIModelAssignment).where(
            AIModelAssignment.assignment_key == TESTING_ASSIGNMENT_KEY
        )
    )
    if testing_assignment is None:
        _generation_error("testing_unconfigured", TESTING_ASSIGNMENT_HELP, 422)
    try:
        testing_config = await AIModelService(db).resolve_config(
            profile_id=testing_assignment.profile_id
        )
    except (LookupError, ValueError) as exc:
        _generation_error("testing_unconfigured", str(exc), 422)
    designer_model = designer_testing_model(
        profile_id=testing_assignment.profile_id, config=testing_config
    )
    try:
        history = await load_designer_history(db, list(source_runs))
    except DesignerError as exc:
        raise GenerationError("history_unavailable", str(exc), 422) from exc
    goal = (
        f"Create regression tests for {len(evidence)} reviewed finding(s)."
        + (f" {body.suite_goal.strip()}" if body.suite_goal and body.suite_goal.strip() else "")
    )
    try:
        designer_input = build_designer_input(
            agent_snapshot=target_snapshot,
            tool_schemas=tool_schemas,
            suite_goal=goal,
            requested_count=body.requested_count,
            historical_examples=history,
            finding_evidence=evidence,
        )
    except DesignerError as exc:
        raise GenerationError("input_invalid", str(exc), 422) from exc
    designer_snapshot = build_designer_snapshot(model=designer_model)
    run = await admit_synthetic_run(
        db,
        candidate_snapshot=designer_snapshot,
        case_input=designer_input,
        output_schema=designer_snapshot["output_schema"],
        agent_id=agent.id,
        org_id=suite.org_id,
        correlation={
            **build_synthetic_correlation(
                suite_id=suite.id,
                case_id=uuid4(),
                execution_id=uuid4(),
                side="baseline",
            ),
            "evaluation_designer": True,
            "designer_suite_id": str(suite.id),
            "designer_tool_schemas": tool_schemas,
            "designer_history_ids": [str(run.id) for run in source_runs],
            "designer_finding_ids": [item["finding_id"] for item in evidence],
        },
    )
    fixture = {"version": 1, "entities": {}, "allowed_tools": [], "rules": []}
    db.add(
        AgentSimulationSession(
            case_id=None,
            case_version=1,
            run_id=run.id,
            root_run_id=run.id,
            fixture=fixture,
            tool_schemas={},
            state=fresh_state(fixture),
            initial_state_hash=canonical_hash({}),
            side="designer",
        )
    )
    return run, suite
