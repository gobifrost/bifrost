"""Agent Evaluation Studio router.

Tenant-authorized CRUD/version endpoints for suites, cases, candidates,
designer drafts, executions, and results. Suite execution enqueues one
canonical ``agent.evaluation_suite`` PlatformJob and returns
``202 PlatformJobAccepted`` with the shared ``Location`` header; the CLI
and future UI observe the shared PlatformJob contract (no Studio polling
transports). Published/frozen versions are immutable; mutable drafts use
optimistic version checks.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession, ReadSnapshotDbSession
from src.core.principal import UserPrincipal
from src.models.contracts.agent_evaluations import (
    CandidateCreate,
    CandidatePublic,
    EvaluationBatchResult,
    EvaluationCaseCreate,
    EvaluationCasePublic,
    EvaluationCaseUpdate,
    EvaluationExecutionBatchCreate,
    EvaluationExecutionCreate,
    EvaluationExecutionPublic,
    EvaluationMatrixPublic,
    EvaluationResultPublic,
    EvaluationSuiteCreate,
    EvaluationSuitePublic,
    EvaluationSuiteUpdate,
    DesignerDraftAccepted,
    DesignerDraftRequest,
    MatrixCellPublic,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from shared.models import (
    AgentTestCreate,
    AgentTestLatestPage,
    AgentTestPage,
    AgentTestPublic,
    AgentTestUpdate,
    AgentTestsGenerateCreate,
    AgentTestsRunCreate,
    QualityUsageBreakdownResponse,
)
from shared.quality_usage_reporting import (
    UsageReportPagination,
    summarize_quality_operation_usage,
)
from src.models.orm.agent_evaluations import (
    AgentCandidateSnapshot,
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationMatrix,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.workflows import Workflow
from src.services.agent_evaluations.simulator_models import redact_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-evaluations", tags=["agent-evaluations"])


# -----------------------------------------------------------------------------
# Tenancy helpers
# -----------------------------------------------------------------------------


def _org_id_for(user: CurrentActiveUser, requested: UUID | None) -> UUID | None:
    if user.is_superuser:
        return requested
    return user.organization_id


def _scope_check(user: CurrentActiveUser, org_id: UUID | None) -> None:
    if user.is_superuser:
        return
    # Studio records are tenant-owned. Unlike reusable global platform
    # resources, a NULL-org suite/candidate/execution must never be readable or
    # mutable by tenant users, including invalid non-admin/null-org principals.
    if org_id is None or org_id != user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        )


async def _entity_access_allowed(entity, user: CurrentActiveUser, db: DbSession) -> bool:
    """Apply the same user-facing access level/role gate to Studio refs."""
    if user.is_superuser:
        return True
    if entity.organization_id not in (None, user.organization_id):
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


async def _authorized_agent(
    db: DbSession, user: CurrentActiveUser, agent_id: UUID, *, org_id: UUID | None
) -> Agent | None:
    agent = (
        await db.execute(
            select(Agent)
            .options(selectinload(Agent.roles))
            .where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    if agent is None or (
        not user.is_superuser and agent.organization_id not in (None, org_id)
    ):
        return None
    return agent if await _entity_access_allowed(agent, user, db) else None


async def _assert_execution_accessible(
    db: DbSession,
    user: CurrentActiveUser,
    *,
    execution: AgentEvaluationExecution,
    suite: AgentEvaluationSuite,
    not_found_detail: str = "Execution not found.",
) -> None:
    _scope_check(user, suite.org_id)
    if user.is_superuser:
        return
    if execution.baseline_agent_id is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    baseline_agent = await _authorized_agent(
        db, user, execution.baseline_agent_id, org_id=suite.org_id
    )
    if baseline_agent is None or not baseline_agent.is_active:
        raise HTTPException(status_code=404, detail=not_found_detail)


async def _execution_and_suite_or_404(
    db: DbSession, user: CurrentActiveUser, execution_id: UUID
) -> tuple[AgentEvaluationExecution, AgentEvaluationSuite]:
    execution = await db.get(AgentEvaluationExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    suite = await db.get(AgentEvaluationSuite, execution.suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    await _assert_execution_accessible(db, user, execution=execution, suite=suite)
    return execution, suite


def _with_historical_runtime_coverage_unknown(report: dict) -> dict:
    """Flag operation reports that may include legacy runtime usage rows.

    Synthetic source runs and designer runs still use the runtime AIUsage ledger.
    A zero-row operation report therefore means no observed rows, not proven
    complete zero spend.
    """
    coverage = dict(report.get("coverage") or {})
    coverage["legacy_coverage_unknown"] = True
    coverage.setdefault(
        "legacy_call_count",
        report.get("overall", {}).get("legacy_call_count", 0) or 0,
    )
    return {**report, "coverage": coverage}


def _not_found(detail: str = "Not found.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _run_org_matches_suite(run, suite: AgentEvaluationSuite) -> bool:
    return run.org_id == suite.org_id


async def _designer_run_and_suite_or_404(
    db: DbSession, user: CurrentActiveUser, run_id: UUID
) -> tuple[AgentRun, AgentEvaluationSuite]:
    from src.services.execution.agent_run_access import agent_run_visibility_conditions

    run = await db.get(AgentRun, run_id)
    if run is None:
        raise _not_found("Designer run not found.")
    if not isinstance(run.correlation, dict):
        raise _not_found("Designer run not found.")
    correlation = run.correlation
    if (
        run.trigger_type != "evaluation_synthetic"
        or correlation.get("evaluation_designer") is not True
        or run.parent_run_id is not None
        or (run.root_run_id is not None and run.root_run_id != run.id)
    ):
        raise _not_found("Designer run not found.")
    try:
        suite_id = UUID(str(correlation["designer_suite_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise _not_found("Designer run not found.") from exc
    suite = await db.get(AgentEvaluationSuite, suite_id)
    if suite is None or not _run_org_matches_suite(run, suite):
        raise _not_found("Designer run not found.")
    _scope_check(user, suite.org_id)
    if suite.agent_id is not None:
        current_agent = await _authorized_agent(
            db, user, suite.agent_id, org_id=suite.org_id
        )
        if current_agent is None or not current_agent.is_active:
            raise _not_found("Designer run not found.")
    visible_run = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.id == run.id, *agent_run_visibility_conditions(user)
            )
        )
    ).scalar_one_or_none()
    if visible_run is None:
        raise _not_found("Designer run not found.")
    if run.agent_id is None:
        if not user.is_superuser:
            raise _not_found("Designer run not found.")
    else:
        original_agent = await _authorized_agent(
            db, user, run.agent_id, org_id=suite.org_id
        )
        if original_agent is None or (
            not original_agent.is_active and not user.is_superuser
        ):
            raise _not_found("Designer run not found.")
    return run, suite


async def _assert_matrix_executions_accessible(
    db: DbSession,
    user: CurrentActiveUser,
    *,
    suite: AgentEvaluationSuite,
    executions: list[AgentEvaluationExecution],
) -> None:
    for execution in executions:
        await _assert_execution_accessible(
            db,
            user,
            execution=execution,
            suite=suite,
            not_found_detail="Matrix not found.",
        )


# -----------------------------------------------------------------------------
# Suites
# -----------------------------------------------------------------------------


@router.post("/suites", response_model=EvaluationSuitePublic)
async def create_suite(
    body: EvaluationSuiteCreate, db: DbSession, user: CurrentActiveUser
) -> EvaluationSuitePublic:
    org_id = _org_id_for(user, body.organization_id)
    if body.agent_id is not None:
        agent = await _authorized_agent(db, user, body.agent_id, org_id=org_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Baseline agent not found.")
    suite = AgentEvaluationSuite(
        org_id=org_id,
        agent_id=body.agent_id,
        name=body.name,
        description=body.description,
        status="draft",
        version=1,
        created_by=user.email,
    )
    db.add(suite)
    await db.commit()
    await db.refresh(suite)
    return EvaluationSuitePublic.model_validate(suite)


@router.get("/suites", response_model=list[EvaluationSuitePublic])
async def list_suites(
    db: DbSession,
    user: CurrentActiveUser,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[EvaluationSuitePublic]:
    query = select(AgentEvaluationSuite).order_by(
        AgentEvaluationSuite.updated_at.desc()
    )
    if not user.is_superuser:
        query = query.where(AgentEvaluationSuite.org_id == user.organization_id)
    if status_filter:
        query = query.where(AgentEvaluationSuite.status == status_filter)
    rows = (await db.execute(query.offset(offset).limit(limit))).scalars().all()
    return [EvaluationSuitePublic.model_validate(row) for row in rows]


@router.get("/suites/{suite_id}", response_model=EvaluationSuitePublic)
async def get_suite(
    suite_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationSuitePublic:
    suite = await db.get(AgentEvaluationSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    _scope_check(user, suite.org_id)
    return EvaluationSuitePublic.model_validate(suite)


@router.put("/suites/{suite_id}", response_model=EvaluationSuitePublic)
async def update_suite(
    suite_id: UUID,
    body: EvaluationSuiteUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> EvaluationSuitePublic:
    suite = await _locked_suite_or_404(db, user, suite_id)
    if suite.status == "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published suites are immutable.",
        )
    if body.expected_version is not None and body.expected_version != suite.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Suite version changed; reload and retry.",
        )
    changed = False
    if body.name is not None:
        suite.name = body.name
        changed = True
    if body.description is not None:
        suite.description = body.description
        changed = True
    if body.agent_id is not None:
        agent = await _authorized_agent(db, user, body.agent_id, org_id=suite.org_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Baseline agent not found.")
        suite.agent_id = agent.id
        changed = True
    if changed:
        suite.version += 1
    await db.commit()
    await db.refresh(suite)
    return EvaluationSuitePublic.model_validate(suite)


@router.post("/suites/{suite_id}/publish", response_model=EvaluationSuitePublic)
async def publish_suite(
    suite_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationSuitePublic:
    suite = await _locked_suite_or_404(db, user, suite_id)
    if suite.status == "published":
        return EvaluationSuitePublic.model_validate(suite)
    suite.status = "published"
    await db.commit()
    await db.refresh(suite)
    return EvaluationSuitePublic.model_validate(suite)


# -----------------------------------------------------------------------------
# Cases
# -----------------------------------------------------------------------------


def _case_to_public(case: AgentEvaluationCase) -> EvaluationCasePublic:
    return EvaluationCasePublic(
        id=case.id,
        suite_id=case.suite_id,
        name=case.name,
        position=case.position,
        enabled=case.enabled,
        version=case.version,
        input=redact_value(case.input),
        fixture=redact_value(case.fixture or {}),
        simulator_policy=case.simulator_policy or {},
        assertions=list(case.assertions or []),
        expected_tools=list(case.expected_tools or []),
        forbidden_tools=list(case.forbidden_tools or []),
        output_schema=case.output_schema,
        repetitions=case.repetitions,
        scoring_policy=case.scoring_policy or {},
        provenance=case.provenance,
        provenance_run_ids=[str(r) for r in (case.provenance_run_ids or [])],
        finding_id=case.finding_id,
        tags=list(case.tags or []),
        accepted=case.accepted,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


async def _suite_or_404(
    db: DbSession, user: CurrentActiveUser, suite_id: UUID
) -> AgentEvaluationSuite:
    suite = await db.get(AgentEvaluationSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    _scope_check(user, suite.org_id)
    return suite


async def _locked_suite_or_404(
    db: DbSession, user: CurrentActiveUser, suite_id: UUID
) -> AgentEvaluationSuite:
    """Load the current suite state while serializing a suite mutation."""
    suite = await db.scalar(
        select(AgentEvaluationSuite)
        .where(AgentEvaluationSuite.id == suite_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    _scope_check(user, suite.org_id)
    return suite


async def _finding_id_or_422(
    db: DbSession,
    user: CurrentActiveUser,
    *,
    suite_agent_id: UUID | None,
    finding_id: UUID | None,
) -> UUID | None:
    """Validate a finding link for a new case.

    The finding must exist and belong to the suite's agent (when the suite
    targets one). Returns the id for storage; ``None`` links nothing.
    """
    if finding_id is None:
        return None
    from shared.agent_finding_visibility import visible_agent_finding_condition

    finding = await db.scalar(
        select(AgentFinding).where(
            AgentFinding.id == finding_id,
            visible_agent_finding_condition(user),
        )
    )
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found.")
    # Findings are caller-tenant scoped: linking across tenants is denied
    # even when the suite targets a shared global agent.
    if not user.is_superuser and finding.org_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Finding not found.")
    if suite_agent_id is not None and finding.agent_id != suite_agent_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Finding belongs to a different agent than the suite.",
        )
    return finding.id


@router.post("/suites/{suite_id}/cases", response_model=EvaluationCasePublic)
async def create_case(
    suite_id: UUID,
    body: EvaluationCaseCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> EvaluationCasePublic:
    from src.services.agent_evaluations import quotas as eval_quotas
    from src.services.agent_evaluations.assertions import (
        AssertionDefinitionError,
        freeze_semantic_judges,
    )
    from src.services.agent_evaluations.simulator_models import (
        FixtureError,
        validate_fixture,
    )

    suite = await _locked_suite_or_404(db, user, suite_id)
    if suite.status == "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published suites are immutable; accept drafts as new versions.",
        )
    try:
        eval_quotas.check_fixture_size(dict(body.fixture))
    except eval_quotas.QuotaExceeded as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=str(exc)
        ) from exc
    existing_count = (
        await db.execute(
            select(func.count())
            .select_from(AgentEvaluationCase)
            .where(AgentEvaluationCase.suite_id == suite.id)
        )
    ).scalar() or 0
    try:
        eval_quotas.check_cases_per_suite(int(existing_count))
    except eval_quotas.QuotaExceeded as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=str(exc)
        ) from exc
    try:
        validate_fixture({"version": 1, **body.fixture})
        assertions = await freeze_semantic_judges(
            db,
            [a.model_dump() for a in body.assertions],
            is_superuser=user.is_superuser,
        )
    except (FixtureError, AssertionDefinitionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    case = AgentEvaluationCase(
        suite_id=suite.id,
        name=body.name,
        position=body.position,
        enabled=body.enabled,
        version=1,
        input=body.input,
        fixture=redact_value(dict(body.fixture)),
        simulator_policy=dict(body.simulator_policy),
        assertions=assertions,
        expected_tools=list(body.expected_tools),
        forbidden_tools=list(body.forbidden_tools),
        output_schema=body.output_schema,
        repetitions=body.repetitions,
        scoring_policy=dict(body.scoring_policy),
        provenance="finding" if body.finding_id is not None else body.provenance,
        provenance_run_ids=[str(r) for r in body.provenance_run_ids],
        finding_id=await _finding_id_or_422(
            db, user, suite_agent_id=suite.agent_id, finding_id=body.finding_id
        ),
        tags=list(body.tags),
        accepted=True,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return _case_to_public(case)


@router.get(
    "/suites/{suite_id}/cases", response_model=list[EvaluationCasePublic]
)
async def list_cases(
    suite_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[EvaluationCasePublic]:
    await _suite_or_404(db, user, suite_id)
    rows = (
        await db.execute(
            select(AgentEvaluationCase)
            .where(AgentEvaluationCase.suite_id == suite_id)
            .order_by(AgentEvaluationCase.position, AgentEvaluationCase.name)
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return [_case_to_public(row) for row in rows]


@router.put(
    "/suites/{suite_id}/cases/{case_id}", response_model=EvaluationCasePublic
)
async def update_case(
    suite_id: UUID,
    case_id: UUID,
    body: EvaluationCaseUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> EvaluationCasePublic:
    suite = await _locked_suite_or_404(db, user, suite_id)
    case = await db.get(AgentEvaluationCase, case_id)
    if case is None or case.suite_id != suite.id:
        raise HTTPException(status_code=404, detail="Case not found.")
    if suite.status == "published" or case.accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only accepted draft-suite cases are editable; "
            "accepted versions freeze as new versions.",
        )
    if body.expected_version is not None and body.expected_version != case.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Case version changed; reload and retry.",
        )
    from src.services.agent_evaluations.assertions import (
        freeze_semantic_judges,
    )
    from src.services.agent_evaluations.simulator_models import validate_fixture
    if body.fixture is not None:
        try:
            validate_fixture({"version": 1, **body.fixture})
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.assertions is not None:
        try:
            frozen_assertions = await freeze_semantic_judges(
                db,
                [item.model_dump() for item in body.assertions],
                is_superuser=user.is_superuser,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    for field in (
        "name", "position", "enabled", "input", "fixture", "simulator_policy",
        "assertions", "expected_tools", "forbidden_tools", "output_schema",
        "repetitions", "scoring_policy", "tags",
    ):
        value = getattr(body, field)
        if value is not None:
            if field == "assertions":
                value = frozen_assertions
            elif field == "fixture":
                value = redact_value(dict(value))
            setattr(case, field, value)
    await db.commit()
    await db.refresh(case)
    return _case_to_public(case)


# -----------------------------------------------------------------------------
# Candidates
# -----------------------------------------------------------------------------


@router.post("/candidates", response_model=CandidatePublic)
async def create_candidate_endpoint(
    body: CandidateCreate, db: DbSession, user: CurrentActiveUser
) -> CandidatePublic:
    from src.services.agent_evaluations.candidates import (
        CandidateError,
        create_candidate,
    )

    org_id = _org_id_for(user, body.organization_id)
    agent = (
        await db.execute(
            select(Agent)
            .options(
                selectinload(Agent.roles),
                selectinload(Agent.tools).selectinload(Workflow.roles),
                selectinload(Agent.delegated_agents).selectinload(Agent.roles),
            )
            .where(Agent.id == body.base_agent_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Base agent not found.")
    if (
        (not user.is_superuser and agent.organization_id not in (None, org_id))
        or not await _entity_access_allowed(agent, user, db)
    ):
        raise HTTPException(status_code=404, detail="Base agent not found.")
    if not agent.is_active:
        raise HTTPException(
            status_code=422, detail="Base agent is paused."
        )
    overlay_model = None
    if body.overlays.llm_profile_id is not None:
        from src.models.orm.ai_models import AIModelProfile
        profile = await db.get(AIModelProfile, body.overlays.llm_profile_id)
        if profile is None:
            raise HTTPException(status_code=422, detail="Model profile not found.")
        if (
            not user.is_superuser
            and profile.id != agent.llm_profile_id
            and not profile.enabled_for_chat
        ):
            raise HTTPException(status_code=403, detail="Model profile is unavailable to this caller.")
        from src.services.llm.factory import get_llm_config
        model_config = await get_llm_config(db, profile_id=profile.id)
        overlay_model = {
            "profile_id": str(profile.id),
            "provider": model_config.provider,
            "model": model_config.model,
            "endpoint": model_config.endpoint,
            "openai_transport": model_config.openai_transport,
            "anthropic_prompt_cache_supported": model_config.anthropic_prompt_cache_supported,
            "default_max_tokens": model_config.default_max_tokens,
            "extra_params": dict(model_config.extra_params or {}),
        }
    if body.overlays.system_tools is not None:
        from src.routers.tools import get_system_tool_ids
        unknown_system_tools = set(body.overlays.system_tools) - set(get_system_tool_ids())
        if unknown_system_tools:
            raise HTTPException(status_code=422, detail="Unknown system-tool grant.")
        ungranted_system_tools = set(body.overlays.system_tools) - set(agent.system_tools or [])
        if ungranted_system_tools:
            raise HTTPException(
                status_code=403,
                detail="Candidate cannot add system tools not granted to its base agent.",
            )
    inaccessible_base_tools = [
        tool.name for tool in (agent.tools or []) if not await _entity_access_allowed(tool, user, db)
    ]
    inaccessible_base_delegates = [
        delegate.name
        for delegate in (agent.delegated_agents or [])
        if not await _entity_access_allowed(delegate, user, db)
    ]
    if inaccessible_base_tools or inaccessible_base_delegates:
        raise HTTPException(
            status_code=403,
            detail="Base agent references resources unavailable to this caller.",
        )
    resolvable_tools = {str(t.id) for t in (agent.tools or [])}
    from src.services.tool_registry import ToolRegistry
    base_tool_definitions = [
        {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters,
            "target_id": str(definition.id),
        }
        for definition in await ToolRegistry(db).get_tool_definitions(
            [tool.id for tool in (agent.tools or [])]
        )
    ]
    from src.services.agent_runtime.execution_snapshot import snapshot_agent
    base_execution_snapshot = await snapshot_agent(
        db, agent, caller_user_id=user.user_id
    )
    overlay_tools = None
    if body.overlays.tool_ids is not None:
        rows = (
            await db.execute(
                select(Workflow).options(selectinload(Workflow.roles)).where(
                    Workflow.id.in_(list(body.overlays.tool_ids)),
                    Workflow.type == "tool",
                    Workflow.is_active.is_(True),
                    or_(Workflow.organization_id.is_(None), Workflow.organization_id == org_id),
                )
            )
        ).scalars().all()
        rows = [workflow for workflow in rows if await _entity_access_allowed(workflow, user, db)]
        resolvable_tools |= {str(w.id) for w in rows}
        definitions = await ToolRegistry(db).get_tool_definitions([w.id for w in rows])
        overlay_tools = [
            {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.parameters,
                "target_id": str(definition.id),
            }
            for definition in definitions
        ]
    resolvable_delegates = {str(d.id) for d in (agent.delegated_agents or [])}
    overlay_delegates = None
    if body.overlays.delegated_agent_ids is not None:
        delegates = (
            await db.execute(
                select(Agent).options(selectinload(Agent.roles)).where(
                    Agent.id.in_(list(body.overlays.delegated_agent_ids)),
                    Agent.is_active.is_(True),
                    or_(Agent.organization_id.is_(None), Agent.organization_id == org_id),
                )
            )
        ).scalars().all()
        delegates = [delegate for delegate in delegates if await _entity_access_allowed(delegate, user, db)]
        resolvable_delegates |= {str(delegate.id) for delegate in delegates}
        overlay_delegates = [{"id": str(d.id), "name": d.name} for d in delegates]
    try:
        candidate = await create_candidate(
            db,
            base_agent=agent,
            overlays=body.overlays,
            resolvable_tool_ids=resolvable_tools,
            resolvable_delegate_ids=resolvable_delegates,
            overlay_tool_definitions=overlay_tools,
            base_tool_definitions=base_tool_definitions,
            overlay_delegated_agents=overlay_delegates,
            base_execution_snapshot=base_execution_snapshot,
            overlay_model=overlay_model,
            owner_org_id=org_id,
            name=body.name,
            created_by=user.email,
        )
    except CandidateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(candidate)
    return CandidatePublic.model_validate(candidate)


@router.get("/candidates/{candidate_id}", response_model=CandidatePublic)
async def get_candidate(
    candidate_id: UUID, db: DbSession, user: CurrentActiveUser
) -> CandidatePublic:
    candidate = await db.get(AgentCandidateSnapshot, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if not user.is_superuser and candidate.org_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return CandidatePublic.model_validate(candidate)


# -----------------------------------------------------------------------------
# Designer drafts and acceptance
# -----------------------------------------------------------------------------


@router.post("/suites/{suite_id}/designer/drafts")
async def designer_drafts(
    suite_id: UUID,
    body: DesignerDraftRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> DesignerDraftAccepted:
    """Start a real, server-authorized Test Designer AgentRun."""
    from src.services.agent_evaluations import quotas as eval_quotas
    from src.services.agent_evaluations.test_designer import (
        DesignerError,
        TESTING_ASSIGNMENT_HELP,
        TESTING_ASSIGNMENT_KEY,
        build_designer_input,
        build_designer_snapshot,
        designer_testing_model,
        load_designer_history,
    )

    suite = await _locked_suite_or_404(db, user, suite_id)
    if suite.status == "published":
        raise HTTPException(status_code=409, detail="Published suites are immutable.")
    try:
        eval_quotas.check_designer_proposal_count(body.requested_count)
    except eval_quotas.QuotaExceeded as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=str(exc)
        ) from exc
    from src.models.orm.agent_runs import AgentRun
    if len(body.historical_run_ids) != len(set(body.historical_run_ids)):
        raise HTTPException(status_code=422, detail="Historical run IDs must be unique.")
    from src.services.execution.agent_run_access import agent_run_visibility_conditions

    source_runs = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.id.in_(body.historical_run_ids),
                *agent_run_visibility_conditions(user),
                AgentRun.org_id == suite.org_id,
            )
        )
    ).scalars().all()
    if len(source_runs) != len(set(body.historical_run_ids)):
        raise HTTPException(status_code=404, detail="Historical run not found.")
    agent = (
        await db.execute(
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.roles),
            )
            .where(Agent.id == suite.agent_id)
        )
    ).scalar_one_or_none()
    if agent is None or not await _entity_access_allowed(agent, user, db):
        raise HTTPException(status_code=422, detail="Suite baseline agent is unavailable.")
    from src.services.agent_runtime.execution_snapshot import snapshot_agent
    target_snapshot = await snapshot_agent(db, agent, caller_user_id=user.user_id)
    tool_schemas = {
        tool["name"]: dict(tool.get("parameters") or {})
        for tool in target_snapshot.get("tools", [])
    }
    # Resolve the configured testing profile before admitting the run and
    # freeze its exact profile id/settings into the Designer snapshot. The
    # runtime re-resolves only credentials from the live profile; a missing
    # assignment is an actionable configuration error, never a silent
    # substitution for another assignment.
    from src.models.orm.ai_models import AIModelAssignment
    testing_assignment = await db.scalar(
        select(AIModelAssignment).where(
            AIModelAssignment.assignment_key == TESTING_ASSIGNMENT_KEY
        )
    )
    if testing_assignment is None:
        raise HTTPException(status_code=422, detail=TESTING_ASSIGNMENT_HELP)
    from src.services.ai_model_service import AIModelService
    try:
        testing_config = await AIModelService(db).resolve_config(
            profile_id=testing_assignment.profile_id
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    designer_model = designer_testing_model(
        profile_id=testing_assignment.profile_id, config=testing_config
    )
    try:
        history = await load_designer_history(db, list(source_runs))
    except DesignerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        designer_input = build_designer_input(
            agent_snapshot=target_snapshot, tool_schemas=tool_schemas,
            suite_goal=body.suite_goal, requested_count=body.requested_count,
            historical_examples=history,
        )
    except DesignerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from src.jobs.rabbitmq import publish_message
    from src.models.orm.agent_evaluations import AgentSimulationSession
    from src.services.agent_evaluations.runner import admit_synthetic_run, build_synthetic_correlation
    from src.services.agent_evaluations.simulator_models import canonical_hash, fresh_state
    designer_snapshot = build_designer_snapshot(model=designer_model)
    run = await admit_synthetic_run(
        db, candidate_snapshot=designer_snapshot, case_input=designer_input,
        output_schema=designer_snapshot["output_schema"], agent_id=agent.id,
        org_id=suite.org_id,
        correlation={
            **build_synthetic_correlation(suite_id=suite.id, case_id=uuid4(), execution_id=uuid4(), side="baseline"),
            "evaluation_designer": True, "designer_suite_id": str(suite.id),
            "designer_tool_schemas": tool_schemas,
            "designer_history_ids": [str(run.id) for run in source_runs],
        },
    )
    fixture = {"version": 1, "entities": {}, "allowed_tools": [], "rules": []}
    db.add(AgentSimulationSession(
        case_id=None, case_version=1, run_id=run.id, root_run_id=run.id,
        fixture=fixture, tool_schemas={}, state=fresh_state(fixture),
        initial_state_hash=canonical_hash({}), side="designer",
    ))
    await db.commit()
    await publish_message("agent-runs", {"run_id": str(run.id)})
    return DesignerDraftAccepted(run_id=run.id)


@router.post(
    "/suites/{suite_id}/cases/accept", response_model=EvaluationCasePublic
)
async def accept_case(
    suite_id: UUID,
    body: dict,
    db: DbSession,
    user: CurrentActiveUser,
) -> EvaluationCasePublic:
    """Explicitly accept a draft: freeze a new accepted case version."""
    from src.services.agent_evaluations.assertions import (
        AssertionDefinitionError,
        validate_assertions,
    )
    from src.services.agent_evaluations.simulator_models import (
        FixtureError,
        validate_fixture,
    )
    from src.services.agent_evaluations.test_designer import accept_proposal

    suite = await _locked_suite_or_404(db, user, suite_id)
    if suite.status == "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published suites are immutable.",
        )
    draft_id = body.get("draft_id")
    if not draft_id:
        raise HTTPException(status_code=422, detail="draft_id is required.")
    draft = await db.get(AgentEvaluationCase, UUID(str(draft_id)))
    if draft is None or draft.suite_id != suite.id:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Case version is already accepted; reruns never regenerate it.",
        )
    try:
        validate_fixture({"version": 1, **(draft.fixture or {})})
        validate_assertions(list(draft.assertions or []))
    except (FixtureError, AssertionDefinitionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    siblings = (
        await db.execute(
            select(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id == suite.id,
                AgentEvaluationCase.name == draft.name,
                AgentEvaluationCase.accepted.is_(True),
            )
        )
    ).scalars().all()
    if any((s.fixture or {}) == (draft.fixture or {}) for s in siblings):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This draft content is already accepted; reruns never regenerate it.",
        )
    max_version = (
        await db.execute(
            select(func.max(AgentEvaluationCase.version)).where(
                AgentEvaluationCase.suite_id == suite.id,
                AgentEvaluationCase.name == draft.name,
            )
        )
    ).scalar() or 0
    proposal = {
        "name": draft.name,
        "input": draft.input,
        "fixture": draft.fixture or {},
        "simulator_policy": draft.simulator_policy or {},
        "assertions": list(draft.assertions or []),
        "expected_tools": list(draft.expected_tools or []),
        "forbidden_tools": list(draft.forbidden_tools or []),
        "output_schema": draft.output_schema,
        "coverage": (draft.tags or ["edge"])[0],
    }
    case = accept_proposal(
        proposal, suite_id=suite.id, position=draft.position,
        provenance_run_ids=list(draft.provenance_run_ids or []),
    )
    case.version = max_version + 1
    case.provenance = draft.provenance
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return _case_to_public(case)


# -----------------------------------------------------------------------------
# Executions (PlatformJob-backed)
# -----------------------------------------------------------------------------


@router.post(
    "/executions",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_execution(
    body: EvaluationExecutionCreate,
    db: DbSession,
    user: CurrentActiveUser,
    response: Response,
) -> PlatformJobAccepted:
    from shared.evaluation_matrix_admission import (
        MatrixAdmissionError,
        admit_single_execution,
    )
    from src.services.platform_jobs import (
        publish_platform_job_update,
    )
    from src.core.principal import UserPrincipal

    principal = UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
    )
    try:
        execution, job, reused, _ = await admit_single_execution(
            db,
            principal,
            suite_id=body.suite_id,
            candidate_id=body.candidate_id,
            repetitions_override=body.repetitions_override,
        )
    except MatrixAdmissionError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail=exc.public_detail
        ) from exc
    await db.commit()
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    response.headers["X-Evaluation-Execution-Id"] = str(execution.id)
    return PlatformJobAccepted(
        job_id=job.id, status=job.status, reused=reused,
        notification_id=job.notification_id,
    )


@router.get("/executions/{execution_id}", response_model=EvaluationExecutionPublic)
async def get_execution(
    execution_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationExecutionPublic:
    execution, _suite = await _execution_and_suite_or_404(db, user, execution_id)
    return EvaluationExecutionPublic.model_validate(execution)


@router.get(
    "/executions/{execution_id}/results",
    response_model=list[EvaluationResultPublic],
)
async def list_results(
    execution_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[EvaluationResultPublic]:
    execution, _suite = await _execution_and_suite_or_404(db, user, execution_id)
    rows = (
        await db.execute(
            select(AgentEvaluationResult)
            .where(AgentEvaluationResult.execution_id == execution_id)
            .order_by(
                AgentEvaluationResult.case_id,
                AgentEvaluationResult.repetition_index,
            )
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    # Result detail links baseline/candidate debugger run IDs and returns
    # assertion/comparison summaries, never duplicated full journals.
    return [EvaluationResultPublic.model_validate(row) for row in rows]


@router.get(
    "/executions/{execution_id}/usage",
    response_model=QualityUsageBreakdownResponse,
)
async def get_execution_usage(
    execution_id: UUID,
    db: ReadSnapshotDbSession,
    user: CurrentActiveUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> QualityUsageBreakdownResponse:
    execution, suite = await _execution_and_suite_or_404(db, user, execution_id)
    report = await summarize_quality_operation_usage(
        db,
        scope="organization" if suite.org_id else "platform",
        organization_id=suite.org_id,
        operation_type="synthetic_evaluation",
        operation_id=execution.id,
        pagination=UsageReportPagination(limit=limit, offset=offset),
    )
    return QualityUsageBreakdownResponse.model_validate(
        _with_historical_runtime_coverage_unknown(report)
    )


@router.get(
    "/designer-runs/{run_id}/usage",
    response_model=QualityUsageBreakdownResponse,
)
async def get_designer_run_usage(
    run_id: UUID,
    db: ReadSnapshotDbSession,
    user: CurrentActiveUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> QualityUsageBreakdownResponse:
    run, suite = await _designer_run_and_suite_or_404(db, user, run_id)
    report = await summarize_quality_operation_usage(
        db,
        scope="organization" if suite.org_id else "platform",
        organization_id=suite.org_id,
        operation_type="quality_designer",
        operation_id=run.id,
        pagination=UsageReportPagination(limit=limit, offset=offset),
    )
    return QualityUsageBreakdownResponse.model_validate(
        _with_historical_runtime_coverage_unknown(report)
    )


@router.post(
    "/executions/{execution_id}/cancel", response_model=EvaluationExecutionPublic
)
async def cancel_execution(
    execution_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationExecutionPublic:
    from src.jobs.platform.agent_evaluation import cancel_evaluation_execution

    execution, _suite = await _execution_and_suite_or_404(db, user, execution_id)
    await cancel_evaluation_execution(execution_id)
    await db.refresh(execution)
    if execution.status == "cancelled" and execution.platform_job_id is not None:
        from src.models.orm.platform_jobs import PlatformJob
        from src.services.platform_jobs import request_platform_job_cancel

        try:
            job = await db.get(PlatformJob, execution.platform_job_id)
            if job is not None:
                await request_platform_job_cancel(db, job)
        except Exception:
            logger.warning(
                "Evaluation execution cancelled without platform-job cancel",
                extra={"execution_id": str(execution_id)},
                exc_info=True,
            )
    await db.commit()
    await db.refresh(execution)
    return EvaluationExecutionPublic.model_validate(execution)


def _matrix_cells_aggregate(matrix, executions) -> EvaluationBatchResult:
    """Compute the matrix aggregate live from its cells (no stored status)."""
    ordered = sorted(
        executions,
        key=lambda e: (str(e.profile_id or ""), str(e.candidate_id or "")),
    )
    cells = [
        MatrixCellPublic(
            execution_id=e.id,
            candidate_id=e.candidate_id,
            profile_id=e.profile_id,
            status=e.status,
            total_cases=e.total_cases or 0,
            completed_cases=e.completed_cases or 0,
            passed_cases=e.passed_cases or 0,
            failed_cases=e.failed_cases or 0,
            platform_job_id=e.platform_job_id,
            reused=False,
        )
        for e in ordered
    ]
    return EvaluationBatchResult(
        matrix=EvaluationMatrixPublic.model_validate(matrix),
        cells=cells,
        planned_cells=len(cells),
        planned_runs=sum(
            c.total_cases * (2 if c.candidate_id is not None else 1)
            for c in cells
        ),
        cost_estimate_usd=None,
        cost_note=(
            "Model spend is metered per synthetic run and reported on each "
            "cell result; this admission returns planned run counts only, "
            "not a spend estimate or cap. Concurrency and per-org execution "
            "quotas are enforced at admission."
        ),
    )


@router.post(
    "/executions/batch",
    response_model=EvaluationBatchResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_execution_batch(
    body: EvaluationExecutionBatchCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> EvaluationBatchResult:
    """Admit a saved multi-profile matrix: one atomic execution per cell.

    Cells whose inputs are already running are explicitly reused (no
    reparenting: the older execution keeps its own matrix) and recorded
    in this matrix's durable membership, so reads and cancellation see
    every reported cell. Quota counts only newly admitted work.
    """
    from shared.evaluation_matrix_admission import (
        MatrixAdmissionError,
        admit_matrix_batch,
    )
    from src.services.platform_jobs import publish_platform_job_update
    from src.core.principal import UserPrincipal

    principal = UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
    )
    try:
        matrix, admitted, published_jobs, total_runs = await admit_matrix_batch(
            db,
            principal,
            suite_id=body.suite_id,
            candidate_ids=list(body.candidate_ids),
            profile_ids=list(body.profile_ids),
            repetitions_override=body.repetitions_override,
        )
    except MatrixAdmissionError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail=exc.public_detail
        ) from exc
    matrix.cell_execution_ids = [str(execution.id) for execution, _, _ in admitted]
    await db.commit()
    for job in published_jobs:
        await publish_platform_job_update(job)
    result = _matrix_cells_aggregate(
        matrix, [execution for execution, _, _ in admitted]
    )
    # The aggregate sorts cells; map reuse flags by execution id so mixed
    # reused/new batches never misattribute them.
    reused_by_id = {
        str(execution.id): reused for execution, _, reused in admitted
    }
    for cell in result.cells:
        cell.reused = reused_by_id.get(str(cell.execution_id), False)
    result.planned_runs = total_runs
    return result


async def _matrix_member_executions(db, matrix) -> list:
    """Load a matrix's cells from its durable membership list.

    Reused cells keep their original matrix_id (never reparented), so
    membership — recorded at admission — is the only coherent selector.
    """
    ids = [UUID(value) for value in (matrix.cell_execution_ids or [])]
    if not ids:
        return []
    rows = list(
        (
            await db.execute(
                select(AgentEvaluationExecution).where(
                    AgentEvaluationExecution.id.in_(ids)
                )
            )
        ).scalars().all()
    )
    if {row.id for row in rows} != set(ids):
        raise HTTPException(status_code=404, detail="Matrix not found.")
    return rows


@router.get(
    "/executions/batch/{matrix_id}", response_model=EvaluationBatchResult
)
async def get_execution_batch(
    matrix_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationBatchResult:
    """Read the live matrix aggregate: per-cell status, never stored."""
    matrix = await db.get(AgentEvaluationMatrix, matrix_id)
    if matrix is None:
        raise HTTPException(status_code=404, detail="Matrix not found.")
    suite = await db.get(AgentEvaluationSuite, matrix.suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Matrix not found.")
    _scope_check(user, suite.org_id)
    executions = await _matrix_member_executions(db, matrix)
    await _assert_matrix_executions_accessible(
        db, user, suite=suite, executions=executions
    )
    return _matrix_cells_aggregate(matrix, list(executions))


@router.post(
    "/executions/batch/{matrix_id}/cancel", response_model=EvaluationBatchResult
)
async def cancel_execution_batch(
    matrix_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationBatchResult:
    """Cancel every active cell through the shared execution/job path."""
    from src.jobs.platform.agent_evaluation import cancel_evaluation_execution
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.platform_jobs import request_platform_job_cancel

    matrix = await db.get(AgentEvaluationMatrix, matrix_id)
    if matrix is None:
        raise HTTPException(status_code=404, detail="Matrix not found.")
    suite = await db.get(AgentEvaluationSuite, matrix.suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Matrix not found.")
    _scope_check(user, suite.org_id)
    executions = await _matrix_member_executions(db, matrix)
    await _assert_matrix_executions_accessible(
        db, user, suite=suite, executions=executions
    )
    for execution in executions:
        if execution.status in ("queued", "running", "waiting"):
            await cancel_evaluation_execution(execution.id)
            await db.refresh(execution)
            if (
                execution.status == "cancelled"
                and execution.platform_job_id is not None
            ):
                job = await db.get(PlatformJob, execution.platform_job_id)
                if job is not None:
                    try:
                        await request_platform_job_cancel(db, job)
                    except Exception:
                        logger.warning(
                            "Matrix cell cancelled without platform-job cancel",
                            extra={"execution_id": str(execution.id)},
                            exc_info=True,
                        )
    await db.commit()
    executions = await _matrix_member_executions(db, matrix)
    await _assert_matrix_executions_accessible(
        db, user, suite=suite, executions=executions
    )
    return _matrix_cells_aggregate(matrix, list(executions))


# -----------------------------------------------------------------------------
# Agent-wide tests (default collection, logical identity)
# -----------------------------------------------------------------------------


def _test_to_public(case, suite) -> AgentTestPublic:
    return AgentTestPublic(
        logical_test_id=case.logical_test_id,
        version=case.version,
        origin_suite_id=suite.id,
        origin_suite_name=suite.name,
        origin_is_default=bool(suite.is_default),
        case_id=case.id,
        name=case.name,
        position=case.position,
        enabled=case.enabled,
        input=redact_value(case.input),
        fixture=redact_value(case.fixture or {}),
        simulator_policy=case.simulator_policy or {},
        assertions=list(case.assertions or []),
        expected_tools=list(case.expected_tools or []),
        forbidden_tools=list(case.forbidden_tools or []),
        output_schema=case.output_schema,
        repetitions=case.repetitions,
        scoring_policy=case.scoring_policy or {},
        provenance=case.provenance,
        provenance_run_ids=[str(r) for r in (case.provenance_run_ids or [])],
        finding_id=case.finding_id,
        tags=list(case.tags or []),
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def _test_principal(user: CurrentActiveUser) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
    )


async def _test_domain(call):
    from shared.agent_test_service import TestServiceError

    try:
        return await call
    except TestServiceError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail=exc.public_detail
        ) from exc


@router.post(
    "/agents/{agent_id}/tests",
    response_model=AgentTestPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_test_route(
    agent_id: UUID,
    body: AgentTestCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentTestPublic:
    from shared.agent_test_service import create_agent_test

    case, suite = await _test_domain(
        create_agent_test(db, _test_principal(user), agent_id=agent_id, body=body)
    )
    await db.commit()
    await db.refresh(case)
    return _test_to_public(case, suite)


@router.get("/agents/{agent_id}/tests", response_model=AgentTestPage)
async def list_agent_tests_route(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AgentTestPage:
    from shared.agent_test_service import list_agent_tests

    items, total = await _test_domain(
        list_agent_tests(
            db, _test_principal(user), agent_id=agent_id, limit=limit, offset=offset
        )
    )
    return AgentTestPage(
        items=[_test_to_public(case, suite) for case, suite in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/agents/{agent_id}/tests/latest", response_model=AgentTestLatestPage)
async def list_agent_tests_latest_route(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AgentTestLatestPage:
    from shared.agent_test_results import latest_results_for_agent
    from shared.models import (
        AgentTestLatestPublic,
        RecordedLatestPublic,
        SimulationLatestPublic,
    )

    def _sim(raw: dict | None):
        if raw is None:
            return None
        return SimulationLatestPublic(**raw)

    def _rec(raw: dict | None):
        if raw is None:
            return None
        return RecordedLatestPublic(**raw)

    items, total = await _test_domain(
        latest_results_for_agent(
            db, _test_principal(user), agent_id=agent_id, limit=limit, offset=offset
        )
    )
    return AgentTestLatestPage(
        items=[
            AgentTestLatestPublic(
                logical_test_id=item["logical_test_id"],
                version=item["version"],
                origin_suite_name=item["origin_suite_name"],
                simulation=_sim(item["simulation"]),
                recorded=_rec(item["recorded"]),
            )
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/agents/{agent_id}/tests/run",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_agent_tests_subset(
    agent_id: UUID,
    body: AgentTestsRunCreate,
    db: DbSession,
    user: CurrentActiveUser,
    response: Response,
) -> PlatformJobAccepted:
    """Run explicitly selected accepted test versions (single suite).

    Selections carry exact versions so reruns cannot silently pick a newer
    edited version. Cross-suite selections partition across calls.
    """
    from shared.evaluation_matrix_admission import (
        MatrixAdmissionError,
        admit_subset_execution,
    )

    try:
        execution, job, reused = await admit_subset_execution(
            db,
            _test_principal(user),
            agent_id=agent_id,
            selections=[
                (item.case_id, item.case_version) for item in body.selections
            ],
            candidate_id=body.candidate_id,
            profile_id=body.profile_id,
            repetitions_override=body.repetitions_override,
        )
    except MatrixAdmissionError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail=exc.public_detail
        ) from exc
    await db.commit()
    from src.services.platform_jobs import publish_platform_job_update

    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    response.headers["X-Evaluation-Execution-Id"] = str(execution.id)
    return PlatformJobAccepted(
        job_id=job.id, status=job.status, reused=reused,
        notification_id=job.notification_id,
    )

@router.post(
    "/agents/{agent_id}/tests/generate",
    response_model=DesignerDraftAccepted,
    status_code=status.HTTP_200_OK,
)
async def generate_agent_tests_from_findings(
    agent_id: UUID,
    body: AgentTestsGenerateCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> DesignerDraftAccepted:
    """Generate tests from explicitly selected findings (selection is approval).

    Dismissed findings stay selectable. Every finding and source run is
    reauthorized; revoked sources fail closed. Drafts materialize disabled
    into the default collection for explicit acceptance.
    """
    from src.jobs.rabbitmq import publish_message
    from shared.agent_test_generation import (
        GenerationError,
        admit_finding_generation,
    )

    try:
        run, _suite = await admit_finding_generation(
            db, _test_principal(user), agent_id=agent_id, body=body
        )
    except GenerationError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail=exc.public_detail
        ) from exc
    await db.commit()
    await publish_message("agent-runs", {"run_id": str(run.id)})
    return DesignerDraftAccepted(run_id=run.id)

@router.get(
    "/agents/{agent_id}/tests/{logical_test_id}", response_model=AgentTestPublic
)
async def get_agent_test_route(
    agent_id: UUID,
    logical_test_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentTestPublic:
    from shared.agent_test_service import get_agent_test

    case, suite = await _test_domain(
        get_agent_test(
            db,
            _test_principal(user),
            agent_id=agent_id,
            logical_test_id=logical_test_id,
        )
    )
    return _test_to_public(case, suite)


@router.patch(
    "/agents/{agent_id}/tests/{logical_test_id}", response_model=AgentTestPublic
)
async def edit_agent_test_route(
    agent_id: UUID,
    logical_test_id: UUID,
    body: AgentTestUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentTestPublic:
    from shared.agent_test_service import edit_agent_test

    case, suite = await _test_domain(
        edit_agent_test(
            db,
            _test_principal(user),
            agent_id=agent_id,
            logical_test_id=logical_test_id,
            body=body,
        )
    )
    await db.commit()
    await db.refresh(case)
    return _test_to_public(case, suite)
