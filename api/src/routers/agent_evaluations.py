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
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.agent_evaluations import (
    CandidateCreate,
    CandidatePublic,
    EvaluationCaseCreate,
    EvaluationCasePublic,
    EvaluationCaseUpdate,
    EvaluationExecutionCreate,
    EvaluationExecutionPublic,
    EvaluationResultPublic,
    EvaluationSuiteCreate,
    EvaluationSuitePublic,
    EvaluationSuiteUpdate,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.models.orm.agent_evaluations import (
    AgentCandidateSnapshot,
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.agents import Agent

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
    # Org users see their org's records plus global (NULL-org) records.
    if org_id is not None and org_id != user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        )


# -----------------------------------------------------------------------------
# Suites
# -----------------------------------------------------------------------------


@router.post("/suites", response_model=EvaluationSuitePublic)
async def create_suite(
    body: EvaluationSuiteCreate, db: DbSession, user: CurrentActiveUser
) -> EvaluationSuitePublic:
    org_id = _org_id_for(user, body.organization_id)
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
    suite = await db.get(AgentEvaluationSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    _scope_check(user, suite.org_id)
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
    if body.name is not None:
        suite.name = body.name
    if body.description is not None:
        suite.description = body.description
    if body.agent_id is not None:
        suite.agent_id = body.agent_id
    await db.commit()
    await db.refresh(suite)
    return EvaluationSuitePublic.model_validate(suite)


@router.post("/suites/{suite_id}/publish", response_model=EvaluationSuitePublic)
async def publish_suite(
    suite_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationSuitePublic:
    suite = await db.get(AgentEvaluationSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    _scope_check(user, suite.org_id)
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
        input=case.input,
        fixture=case.fixture or {},
        simulator_policy=case.simulator_policy or {},
        assertions=list(case.assertions or []),
        expected_tools=list(case.expected_tools or []),
        forbidden_tools=list(case.forbidden_tools or []),
        output_schema=case.output_schema,
        repetitions=case.repetitions,
        scoring_policy=case.scoring_policy or {},
        provenance=case.provenance,
        provenance_run_ids=[str(r) for r in (case.provenance_run_ids or [])],
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


@router.post("/suites/{suite_id}/cases", response_model=EvaluationCasePublic)
async def create_case(
    suite_id: UUID,
    body: EvaluationCaseCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> EvaluationCasePublic:
    from src.services.agent_evaluations.assertions import (
        AssertionDefinitionError,
        validate_assertions,
    )
    from src.services.agent_evaluations.simulator_models import (
        FixtureError,
        validate_fixture,
    )

    suite = await _suite_or_404(db, user, suite_id)
    if suite.status == "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published suites are immutable; accept drafts as new versions.",
        )
    try:
        validate_fixture({"version": 1, **body.fixture})
        validate_assertions([a.model_dump() for a in body.assertions])
    except (FixtureError, AssertionDefinitionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    case = AgentEvaluationCase(
        suite_id=suite.id,
        name=body.name,
        position=body.position,
        enabled=body.enabled,
        version=1,
        input=body.input,
        fixture=dict(body.fixture),
        simulator_policy=dict(body.simulator_policy),
        assertions=[a.model_dump() for a in body.assertions],
        expected_tools=list(body.expected_tools),
        forbidden_tools=list(body.forbidden_tools),
        output_schema=body.output_schema,
        repetitions=body.repetitions,
        scoring_policy=dict(body.scoring_policy),
        provenance=body.provenance,
        provenance_run_ids=[str(r) for r in body.provenance_run_ids],
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
    suite = await _suite_or_404(db, user, suite_id)
    case = await db.get(AgentEvaluationCase, case_id)
    if case is None or case.suite_id != suite.id:
        raise HTTPException(status_code=404, detail="Case not found.")
    if suite.status == "published" or not case.accepted:
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
    for field in (
        "name", "position", "enabled", "input", "fixture", "simulator_policy",
        "assertions", "expected_tools", "forbidden_tools", "output_schema",
        "repetitions", "scoring_policy", "tags",
    ):
        value = getattr(body, field)
        if value is not None:
            if field == "assertions":
                value = [a.model_dump() for a in value]
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
            .options(selectinload(Agent.tools), selectinload(Agent.delegated_agents))
            .where(Agent.id == body.base_agent_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Base agent not found.")
    if (
        not user.is_superuser
        and agent.organization_id is not None
        and agent.organization_id != org_id
    ):
        raise HTTPException(status_code=404, detail="Base agent not found.")
    if not agent.is_active:
        raise HTTPException(
            status_code=422, detail="Base agent is paused."
        )
    resolvable_tools = {str(t.id) for t in (agent.tools or [])}
    if body.overlays.tool_ids:
        from sqlalchemy import select as _select
        from src.models.orm.workflows import Workflow

        rows = (
            await db.execute(
                _select(Workflow).where(
                    Workflow.id.in_(list(body.overlays.tool_ids))
                )
            )
        ).scalars().all()
        resolvable_tools |= {str(w.id) for w in rows}
    resolvable_delegates = {str(d.id) for d in (agent.delegated_agents or [])}
    overlay_tools = None
    if body.overlays.tool_ids:
        from sqlalchemy import select as _select2
        from src.models.orm.workflows import Workflow as _Workflow

        rows = (
            await db.execute(
                _select2(_Workflow).where(
                    _Workflow.id.in_(list(body.overlays.tool_ids))
                )
            )
        ).scalars().all()
        overlay_tools = [{"name": w.name, "target_id": str(w.id)} for w in rows]
    try:
        candidate = await create_candidate(
            db,
            base_agent=agent,
            overlays=body.overlays,
            resolvable_tool_ids=resolvable_tools,
            resolvable_delegate_ids=resolvable_delegates,
            overlay_tool_definitions=overlay_tools,
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
    _scope_check(user, candidate.org_id)
    return CandidatePublic.model_validate(candidate)


# -----------------------------------------------------------------------------
# Designer drafts and acceptance
# -----------------------------------------------------------------------------


@router.post("/suites/{suite_id}/designer/drafts")
async def designer_drafts(
    suite_id: UUID,
    body: dict,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Validate designer output and persist drafts (never auto-approve).

    Accepts the caller-owned designer output object plus the tool schemas
    and allowed historical run IDs used for provenance. Returns drafts with
    coverage labels; drafts never run until explicitly accepted.
    """
    from src.services.agent_evaluations.test_designer import (
        DesignerError,
        deduplicate_proposals,
        redact_history,
        validate_designer_output,
    )

    suite = await _suite_or_404(db, user, suite_id)
    tool_schemas = body.get("tool_schemas") or {}
    allowed_runs = set(body.get("allowed_run_ids") or [])
    history = redact_history(body.get("historical_runs") or [], allowed_run_ids=allowed_runs)
    try:
        proposals = validate_designer_output(
            body.get("designer_output") or {}, tool_schemas=tool_schemas
        )
    except DesignerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = (
        await db.execute(
            select(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id == suite.id
            )
        )
    ).scalars().all()
    proposals = deduplicate_proposals(
        proposals,
        [
            {
                "assertions": list(c.assertions or []),
                "input": c.input,
                "coverage": (c.tags or ["edge"])[0],
            }
            for c in existing
        ],
    )
    drafts = []
    for position, proposal in enumerate(proposals):
        max_version = (
            await db.execute(
                select(func.max(AgentEvaluationCase.version)).where(
                    AgentEvaluationCase.suite_id == suite.id,
                    AgentEvaluationCase.name == proposal["name"],
                )
            )
        ).scalar() or 0
        draft = AgentEvaluationCase(
            suite_id=suite.id,
            name=proposal["name"],
            position=position,
            enabled=False,
            version=max_version + 1,
            input=proposal.get("input"),
            fixture=proposal.get("fixture", {}),
            simulator_policy=proposal.get("simulator_policy", {}),
            assertions=proposal.get("assertions", []),
            expected_tools=proposal.get("expected_tools", []),
            forbidden_tools=proposal.get("forbidden_tools", []),
            output_schema=proposal.get("output_schema"),
            repetitions=1,
            scoring_policy={},
            provenance="generated",
            provenance_run_ids=[
                str(r.get("run_id")) for r in history if r.get("run_id")
            ],
            tags=[proposal.get("coverage", "edge")],
            accepted=False,
        )
        db.add(draft)
        drafts.append(draft)
    await db.commit()
    for draft in drafts:
        await db.refresh(draft)
    return {
        "drafts": [_case_to_public(d).model_dump(mode="json") for d in drafts],
        "coverage": [d.tags[0] if d.tags else "edge" for d in drafts],
    }


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
    from src.services.agent_evaluations.test_designer import accept_proposal

    suite = await _suite_or_404(db, user, suite_id)
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
        provenance_run_ids=[],
    )
    case.version = max_version + 1
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
    from src.jobs.platform.agent_evaluation import (
        AGENT_EVALUATION_SUITE_DEFINITION,
        AgentEvaluationSuitePayload,
    )
    from src.services.agent_evaluations.executions import (
        build_dedupe_key,
        create_execution_objects,
    )
    from src.services.platform_jobs import (
        enqueue_platform_job,
        publish_platform_job_update,
    )

    suite = await _suite_or_404(db, user, body.suite_id)
    if suite.status != "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only published suites can execute; publish first.",
        )
    candidate_id = body.candidate_id
    if candidate_id is not None:
        candidate = await db.get(AgentCandidateSnapshot, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found.")
        _scope_check(user, candidate.org_id)
    cases = (
        await db.execute(
            select(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id == suite.id,
                AgentEvaluationCase.accepted.is_(True),
                AgentEvaluationCase.enabled.is_(True),
            )
        )
    ).scalars().all()
    if not cases:
        raise HTTPException(
            status_code=422, detail="Suite has no accepted enabled cases."
        )
    dedupe_key = build_dedupe_key(suite.id, suite.version, candidate_id)
    existing = (
        await db.execute(
            select(AgentEvaluationExecution).where(
                AgentEvaluationExecution.dedupe_key == dedupe_key,
                AgentEvaluationExecution.status.in_(("queued", "running", "waiting")),
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.platform_job_id is not None:
        from src.models.orm.platform_jobs import PlatformJob

        job = await db.get(PlatformJob, existing.platform_job_id)
        if job is not None:
            response.headers["Location"] = f"/api/platform-jobs/{job.id}"
            response.headers["X-Evaluation-Execution-Id"] = str(existing.id)
            return PlatformJobAccepted(
                job_id=job.id, status=job.status, reused=True,
                notification_id=job.notification_id,
            )
    execution, results, _ = create_execution_objects(
        suite=suite,
        candidate_id=candidate_id,
        baseline_agent_id=suite.agent_id,
        cases=list(cases),
        include_candidate=candidate_id is not None,
        repetitions_override=body.repetitions_override,
        created_by=user.email,
    )
    db.add(execution)
    db.add_all(results)
    await db.flush()
    job, reused = await enqueue_platform_job(
        db,
        AGENT_EVALUATION_SUITE_DEFINITION,
        AgentEvaluationSuitePayload(execution_id=execution.id),
        dedupe_key=dedupe_key,
        organization_id=suite.org_id,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="agent_evaluation",
        resource_id=str(execution.id),
        title=f"Evaluating suite {suite.name}",
        action_url=f"/agent-evaluations/executions/{execution.id}",
    )
    if reused and job.requested_by_user_id != str(user.user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A suite execution is already in progress.",
        )
    execution.platform_job_id = job.id
    execution.status = "queued"
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
    execution = await db.get(AgentEvaluationExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    suite = await db.get(AgentEvaluationSuite, execution.suite_id)
    if suite is not None:
        _scope_check(user, suite.org_id)
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
    execution = await db.get(AgentEvaluationExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    suite = await db.get(AgentEvaluationSuite, execution.suite_id)
    if suite is not None:
        _scope_check(user, suite.org_id)
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


@router.post(
    "/executions/{execution_id}/cancel", response_model=EvaluationExecutionPublic
)
async def cancel_execution(
    execution_id: UUID, db: DbSession, user: CurrentActiveUser
) -> EvaluationExecutionPublic:
    from src.jobs.platform.agent_evaluation import cancel_evaluation_execution

    execution = await db.get(AgentEvaluationExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    suite = await db.get(AgentEvaluationSuite, execution.suite_id)
    if suite is not None:
        _scope_check(user, suite.org_id)
    await cancel_evaluation_execution(execution_id)
    if execution.platform_job_id is not None:
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
