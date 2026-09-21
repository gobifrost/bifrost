"""Shared evaluation matrix admission (single + batch orchestration).

Moved out of ``api/src/routers/agent_evaluations.py`` so HTTP and scheduled
callers share cell authorization, snapshots, dedupe, quotas, and matrix
membership. Routers translate :class:`MatrixAdmissionError` to HTTP; the
scheduler records its code as a fire receipt reason. No model calls here
(config reads only); callers own commit/publish.
"""

from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.principal import UserPrincipal
from src.models.orm.agent_evaluations import (
    AgentCandidateSnapshot,
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationMatrix,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.agents import Agent


class MatrixAdmissionError(Exception):
    def __init__(
        self,
        code: str,
        public_detail: str = "Evaluation not found.",
        http_status: int = 404,
    ) -> None:
        super().__init__(public_detail)
        self.code = code
        self.public_detail = public_detail
        self.http_status = http_status


def _domain_error(code: str, detail: str, http_status: int) -> NoReturn:
    raise MatrixAdmissionError(code, public_detail=detail, http_status=http_status)


def _scope_check(user: UserPrincipal, org_id: UUID | None) -> None:
    if user.is_superuser:
        return
    if org_id is None or org_id != user.organization_id:
        _domain_error("suite_not_found", "Not found.", 404)


async def _entity_access_allowed(entity: Any, user: UserPrincipal, db: AsyncSession) -> bool:
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


async def lock_suite_or_404(
    db: AsyncSession, user: UserPrincipal, suite_id: UUID
) -> AgentEvaluationSuite:
    """Load the current suite state while serializing a suite mutation."""
    suite = await db.scalar(
        select(AgentEvaluationSuite)
        .where(AgentEvaluationSuite.id == suite_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if suite is None:
        _domain_error("suite_not_found", "Suite not found.", 404)
    _scope_check(user, suite.org_id)
    return suite


async def find_reusable_execution(db: AsyncSession, dedupe_key: str):
    """Return an active execution (plus job and planned runs) for a key.

    Shared by single admission and batch pre-checks so both paths reuse
    the same active work instead of double-admitting it.
    """
    from src.services.agent_evaluations.executions import plan_result_work_items

    existing = (
        await db.execute(
            select(AgentEvaluationExecution).where(
                AgentEvaluationExecution.dedupe_key == dedupe_key,
                AgentEvaluationExecution.status.in_(("queued", "running", "waiting")),
            )
        )
    ).scalar_one_or_none()
    if existing is None or existing.platform_job_id is None:
        return None
    from src.models.orm.platform_jobs import PlatformJob

    job = await db.get(PlatformJob, existing.platform_job_id)
    if job is None:
        return None
    existing_results = (
        await db.execute(
            select(AgentEvaluationResult).where(
                AgentEvaluationResult.execution_id == existing.id
            )
        )
    ).scalars().all()
    planned = plan_result_work_items(
        list(existing_results),
        include_candidate=existing.candidate_id is not None,
    )
    return existing, job, len(planned)


async def authorize_matrix_cell(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    suite: AgentEvaluationSuite,
    candidate_id: UUID | None,
    profile_id: UUID | None,
):
    """Authorize one cell's references and freeze its snapshots.

    Single source of truth for cell authorization, used identically for
    newly admitted AND reused cells: baseline visibility/activity, the
    profile caller gate with full config resolution, and candidate
    authorization. No model calls are spent here (config reads only).
    Returns ``(baseline_snapshot, candidate_snapshot | None)``; the stored
    candidate row is copied, never mutated.
    """
    from src.models.orm.ai_models import AIModelProfile
    from src.services.agent_evaluations.executions import apply_profile_override

    baseline_agent = None
    if suite.agent_id is not None:
        baseline_agent = (
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
    if (
        baseline_agent is None
        or not baseline_agent.is_active
        or not await _entity_access_allowed(baseline_agent, user, db)
    ):
        _domain_error(
            "baseline_unavailable", "Suite baseline agent is unavailable.", 422
        )
    from src.services.agent_runtime.execution_snapshot import snapshot_agent

    baseline_snapshot = await snapshot_agent(
        db, baseline_agent, caller_user_id=user.user_id
    )
    baseline_snapshot["evaluation"] = {
        "mode": "evaluation_synthetic",
        "evaluation_only": True,
    }
    selected_config = None
    if profile_id is not None:
        profile = await db.get(AIModelProfile, profile_id)
        if profile is None:
            _domain_error("profile_not_found", "Model profile not found.", 404)
        if (
            not user.is_superuser
            and profile.id != baseline_agent.llm_profile_id
            and not profile.enabled_for_chat
        ):
            _domain_error(
                "profile_forbidden",
                "Model profile is unavailable to this caller.",
                403,
            )
        from src.services.llm.factory import get_llm_config

        try:
            selected_config = await get_llm_config(db, profile_id=profile.id)
        except ValueError as exc:
            _domain_error(
                "profile_unresolvable",
                f"Selected model profile cannot be resolved: {exc}",
                422,
            )
        baseline_snapshot = apply_profile_override(
            baseline_snapshot, profile_id, model_config=selected_config
        )
    candidate_snapshot = None
    if candidate_id is not None:
        candidate = await db.get(AgentCandidateSnapshot, candidate_id)
        if candidate is None:
            _domain_error("candidate_not_found", "Candidate not found.", 404)
        if not user.is_superuser and candidate.org_id != user.organization_id:
            _domain_error("candidate_not_found", "Candidate not found.", 404)
        if candidate.base_agent_id != suite.agent_id:
            _domain_error(
                "candidate_mismatch",
                "Candidate must be derived from this suite's baseline agent.",
                422,
            )
        candidate_snapshot = dict(candidate.snapshot or {})
        if profile_id is not None:
            # The selected profile wins on both sides so the comparison
            # pairs baseline and candidate under the same model. The stored
            # candidate row is never mutated; only this frozen copy changes.
            assert selected_config is not None
            candidate_snapshot = apply_profile_override(
                candidate_snapshot, profile_id, model_config=selected_config
            )
    return baseline_snapshot, candidate_snapshot


async def admit_execution_cell(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    suite: AgentEvaluationSuite,
    candidate_id: UUID | None,
    profile_id: UUID | None,
    repetitions_override: int | None,
    matrix_id: UUID | None,
    _snapshots: tuple[dict, dict | None] | None = None,
):
    """Admit one execution cell through the single-execution path.

    Freezes baseline/candidate snapshots (both sides under ``profile_id``
    when given; stored candidate rows are copied, never mutated), dedupes
    against active executions, and enqueues the canonical PlatformJob.
    Returns ``(execution, job, reused, planned_runs)``; the caller commits
    and publishes. Repetition/quota checks stay with the caller.
    Pre-authorized ``_snapshots`` (batch path) skip re-authorization; the
    single path authorizes inline through the same function.
    """
    from src.jobs.platform.agent_evaluation import (
        AGENT_EVALUATION_SUITE_DEFINITION,
        AgentEvaluationSuitePayload,
    )
    from src.services.agent_evaluations.executions import (
        build_dedupe_key,
        create_execution_objects,
    )
    from src.services.platform_jobs import enqueue_platform_job

    if _snapshots is None:
        baseline_snapshot, candidate_snapshot = await authorize_matrix_cell(
            db, user, suite=suite, candidate_id=candidate_id, profile_id=profile_id
        )
    else:
        baseline_snapshot, candidate_snapshot = _snapshots
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
        _domain_error("no_cases", "Suite has no accepted enabled cases.", 422)
    dedupe_key = build_dedupe_key(
        suite.id, suite.version, candidate_id, profile_id, repetitions_override
    )
    reusable = await find_reusable_execution(db, dedupe_key)
    if reusable is not None:
        existing, job, planned_runs = reusable
        return existing, job, True, planned_runs
    execution, results, planned = create_execution_objects(
        suite=suite,
        candidate_id=candidate_id,
        baseline_agent_id=suite.agent_id,
        cases=list(cases),
        include_candidate=candidate_id is not None,
        repetitions_override=repetitions_override,
        created_by=user.email,
        baseline_snapshot=baseline_snapshot,
        candidate_snapshot=candidate_snapshot,
        matrix_id=matrix_id,
        profile_id=profile_id,
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
    if reused:
        _domain_error(
            "job_conflict",
            "A platform job for this suite cell is still active.",
            409,
        )
    execution.platform_job_id = job.id
    execution.status = "queued"
    await db.flush()
    return execution, job, reused, len(planned)


async def admit_single_execution(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    suite_id: UUID,
    candidate_id: UUID | None,
    repetitions_override: int | None,
):
    """Admit one baseline execution; caller commits and publishes."""
    from src.services.agent_evaluations import quotas as eval_quotas

    suite = await lock_suite_or_404(db, user, suite_id)
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(str(suite.org_id) if suite.org_id is not None else "global")
            )
        )
    )
    if suite.status != "published":
        _domain_error(
            "suite_not_published",
            "Only published suites can execute; publish first.",
            409,
        )
    try:
        eval_quotas.check_repetitions(repetitions_override)
    except eval_quotas.QuotaExceeded as exc:
        _domain_error("repetitions_invalid", str(exc), exc.status_code)
    active_count = (
        await db.execute(
            select(func.count())
            .select_from(AgentEvaluationExecution)
            .join(
                AgentEvaluationSuite,
                AgentEvaluationSuite.id == AgentEvaluationExecution.suite_id,
            )
            .where(
                AgentEvaluationSuite.org_id == suite.org_id,
                AgentEvaluationExecution.status.in_(("queued", "running", "waiting")),
            )
        )
    ).scalar() or 0
    try:
        eval_quotas.check_active_executions_per_org(int(active_count))
    except eval_quotas.QuotaExceeded as exc:
        _domain_error("quota_exceeded", str(exc), exc.status_code)
    return await admit_execution_cell(
        db,
        user,
        suite=suite,
        candidate_id=candidate_id,
        profile_id=None,
        repetitions_override=repetitions_override,
        matrix_id=None,
    )


async def admit_matrix_batch(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    suite_id: UUID,
    candidate_ids: list[UUID],
    profile_ids: list[UUID],
    repetitions_override: int | None,
):
    """Admit a saved multi-profile matrix: one atomic execution per cell.

    Cells whose inputs are already running are explicitly reused (no
    reparenting: the older execution keeps its own matrix) and recorded
    in this matrix's durable membership, so reads and cancellation see
    every reported cell. Quota counts only newly admitted work. Returns
    ``(matrix, admitted, published_jobs, total_runs)``; the caller commits
    and publishes.
    """
    from src.services.agent_evaluations import quotas as eval_quotas
    from src.services.agent_evaluations.executions import (
        build_dedupe_key,
        plan_matrix_cells,
    )

    suite = await lock_suite_or_404(db, user, suite_id)
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(str(suite.org_id) if suite.org_id is not None else "global")
            )
        )
    )
    if suite.status != "published":
        _domain_error(
            "suite_not_published",
            "Only published suites can execute; publish first.",
            409,
        )
    try:
        eval_quotas.check_repetitions(repetitions_override)
        cells = plan_matrix_cells(candidate_ids, profile_ids)
    except eval_quotas.QuotaExceeded as exc:
        _domain_error("quota_exceeded", str(exc), exc.status_code)
    active_count = (
        await db.execute(
            select(func.count())
            .select_from(AgentEvaluationExecution)
            .join(
                AgentEvaluationSuite,
                AgentEvaluationSuite.id == AgentEvaluationExecution.suite_id,
            )
            .where(
                AgentEvaluationSuite.org_id == suite.org_id,
                AgentEvaluationExecution.status.in_(("queued", "running", "waiting")),
            )
        )
    ).scalar() or 0
    matrix = AgentEvaluationMatrix(
        suite_id=suite.id,
        suite_version=suite.version,
        candidate_ids=[str(c) for c in candidate_ids],
        profile_ids=[str(p) for p in profile_ids],
        repetitions_override=repetitions_override,
        cell_execution_ids=[],
        org_id=suite.org_id,
        created_by=user.email,
    )
    db.add(matrix)
    await db.flush()
    admitted: list[tuple] = []
    member_ids: list[str] = []
    total_runs = 0
    published_jobs = []
    new_count = 0
    for candidate_id, profile_id in cells:
        # Identical authorization for reused and new cells: an active
        # execution is only reusable when this caller could admit it.
        snapshots = await authorize_matrix_cell(
            db, user, suite=suite, candidate_id=candidate_id, profile_id=profile_id
        )
        key = build_dedupe_key(
            suite.id,
            suite.version,
            candidate_id,
            profile_id,
            repetitions_override,
        )
        reusable = await find_reusable_execution(db, key)
        if reusable is not None:
            existing, job, planned_runs = reusable
            member_ids.append(str(existing.id))
            admitted.append((existing, job, True))
            total_runs += planned_runs
            continue
        if (
            int(active_count) + new_count + 1
            > eval_quotas.MAX_ACTIVE_EXECUTIONS_PER_ORG
        ):
            _domain_error(
                "quota_exceeded",
                (
                    "Matrix needs new work but the organization has "
                    f"{int(active_count) + new_count} active executions "
                    f"(limit {eval_quotas.MAX_ACTIVE_EXECUTIONS_PER_ORG}); "
                    "wait or cancel one. Already-running cells are reused "
                    "and never consume quota."
                ),
                429,
            )
        execution, job, reused, planned_runs = await admit_execution_cell(
            db,
            user,
            suite=suite,
            candidate_id=candidate_id,
            profile_id=profile_id,
            repetitions_override=repetitions_override,
            matrix_id=matrix.id,
            _snapshots=snapshots,
        )
        member_ids.append(str(execution.id))
        admitted.append((execution, job, reused))
        total_runs += planned_runs
        if not reused:
            new_count += 1
            published_jobs.append(job)
    matrix.cell_execution_ids = member_ids
    return matrix, admitted, published_jobs, total_runs


async def admit_subset_execution(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    selections: list[tuple[UUID, int]],
    candidate_id: UUID | None,
    profile_id: UUID | None,
    repetitions_override: int | None,
):
    """Admit one execution over explicit accepted (case_id, version) rows.

    Every selection must name an existing accepted row with an exactly
    matching version, in a published suite of the same agent. Selections
    must share one suite; cross-suite runs partition across calls. The
    subset fingerprint joins the active dedupe key so different subsets
    never collide. Returns ``(execution, job, reused)``; caller commits.
    """
    from src.jobs.platform.agent_evaluation import (
        AGENT_EVALUATION_SUITE_DEFINITION,
        AgentEvaluationSuitePayload,
    )
    from src.services.agent_evaluations import quotas as eval_quotas
    from src.services.agent_evaluations.executions import (
        create_execution_objects,
        subset_fingerprint,
    )
    from src.services.platform_jobs import enqueue_platform_job

    agent = (
        await db.execute(
            select(Agent)
            .options(selectinload(Agent.roles))
            .where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    if agent is None or not agent.is_active:
        _domain_error("agent_not_found", "Agent not found.", 404)
    if not await _entity_access_allowed(agent, user, db):
        _domain_error("agent_not_found", "Agent not found.", 404)
    if not selections:
        _domain_error("empty_selection", "Select at least one test version.", 422)
    wanted = {(case_id, version) for case_id, version in selections}
    rows = (
        (
            await db.execute(
                select(AgentEvaluationCase).where(
                    AgentEvaluationCase.id.in_([case_id for case_id, _ in wanted]),
                    AgentEvaluationCase.accepted.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in rows}
    selected: list = []
    suite_ids: set[UUID] = set()
    for case_id, version in sorted(wanted, key=lambda pair: str(pair[0])):
        row = by_id.get(case_id)
        if row is None:
            _domain_error("case_not_found", "Selected test not found.", 404)
        if row.version != version:
            _domain_error(
                "version_mismatch",
                "Selected test version does not match; reload and retry.",
                422,
            )
        selected.append(row)
        suite_ids.add(row.suite_id)
    if len(suite_ids) != 1:
        _domain_error(
            "multi_suite_selection",
            "Selections must share one suite; partition across calls.",
            422,
        )
    suite = await lock_suite_or_404(db, user, next(iter(suite_ids)))
    if suite.agent_id != agent.id:
        _domain_error("case_wrong_agent", "Selected tests belong to another agent.", 422)
    if suite.status != "published":
        _domain_error(
            "suite_not_published",
            "Only published suites can execute; publish first.",
            409,
        )
    for row in selected:
        if not row.enabled:
            _domain_error(
                "disabled_selected",
                "Selected tests must be enabled; disabled tests are never executed.",
                422,
            )
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(str(suite.org_id) if suite.org_id is not None else "global")
            )
        )
    )
    try:
        eval_quotas.check_repetitions(repetitions_override)
    except eval_quotas.QuotaExceeded as exc:
        _domain_error("repetitions_invalid", str(exc), exc.status_code)
    fingerprint = subset_fingerprint([(row.id, row.version) for row in selected])
    snapshots = await authorize_matrix_cell(
        db, user, suite=suite, candidate_id=candidate_id, profile_id=profile_id
    )
    baseline_snapshot, candidate_snapshot = snapshots
    from src.services.agent_evaluations.executions import build_dedupe_key as _key

    dedupe_key = _key(
        suite.id,
        suite.version,
        candidate_id,
        profile_id,
        repetitions_override,
        fingerprint,
    )
    reusable = await find_reusable_execution(db, dedupe_key)
    if reusable is not None:
        existing, job, _planned_runs = reusable
        return existing, job, True
    active_count = (
        await db.execute(
            select(func.count())
            .select_from(AgentEvaluationExecution)
            .join(
                AgentEvaluationSuite,
                AgentEvaluationSuite.id == AgentEvaluationExecution.suite_id,
            )
            .where(
                AgentEvaluationSuite.org_id == suite.org_id,
                AgentEvaluationExecution.status.in_(("queued", "running", "waiting")),
            )
        )
    ).scalar() or 0
    try:
        eval_quotas.check_active_executions_per_org(int(active_count))
    except eval_quotas.QuotaExceeded as exc:
        _domain_error("quota_exceeded", str(exc), exc.status_code)
    execution, results, _planned = create_execution_objects(
        suite=suite,
        candidate_id=candidate_id,
        baseline_agent_id=suite.agent_id,
        cases=selected,
        include_candidate=candidate_id is not None,
        repetitions_override=repetitions_override,
        created_by=user.email,
        baseline_snapshot=baseline_snapshot,
        candidate_snapshot=candidate_snapshot,
        matrix_id=None,
        profile_id=profile_id,
        subset_fingerprint=fingerprint,
    )
    db.add(execution)
    db.add_all(results)
    await db.flush()
    job, reused = await enqueue_platform_job(
        db,
        AGENT_EVALUATION_SUITE_DEFINITION,
        AgentEvaluationSuitePayload(execution_id=execution.id),
        dedupe_key=execution.dedupe_key,
        organization_id=suite.org_id,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="agent_evaluation",
        resource_id=str(execution.id),
        title=f"Evaluating selected tests in {suite.name}",
        action_url=f"/agent-evaluations/executions/{execution.id}",
    )
    if reused and job.requested_by_user_id != str(user.user_id):
        _domain_error(
            "job_conflict", "A suite execution is already in progress.", 409
        )
    execution.platform_job_id = job.id
    execution.status = "queued"
    await db.flush()
    return execution, job, reused
