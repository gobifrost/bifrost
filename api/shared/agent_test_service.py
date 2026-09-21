"""Agent-wide test CRUD over the default collection (Phase 4b).

Creates land as accepted v1 rows with fresh logical ids in the agent's
default suite. Edits never mutate: they insert the next accepted version
under the same logical id into the default suite (copy-to-default when the
current version lives in a published named suite). Reads return the current
accepted version per logical id with origin metadata. Callers own commit.
"""

from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.agent_test_collection import get_or_create_default_suite
from shared.models import AgentTestCreate, AgentTestUpdate
from src.core.principal import UserPrincipal
from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationSuite,
)
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agents import Agent


class TestServiceError(Exception):
    def __init__(
        self,
        code: str,
        public_detail: str = "Test not found.",
        http_status: int = 404,
    ) -> None:
        super().__init__(public_detail)
        self.code = code
        self.public_detail = public_detail
        self.http_status = http_status


def _service_error(code: str, detail: str, http_status: int) -> NoReturn:
    raise TestServiceError(code, public_detail=detail, http_status=http_status)


async def _agent_or_404(
    db: AsyncSession, user: UserPrincipal, agent_id: UUID
) -> Agent:
    from shared.evaluation_matrix_admission import _entity_access_allowed

    # Eager roles: the access gate reads agent.roles (async relationship).
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


def _default_org_id(user: UserPrincipal, agent: Agent) -> UUID:
    org_id = agent.organization_id or user.organization_id
    if org_id is None:
        _service_error("org_required", "Tests require an organization scope.", 422)
    if (
        not user.is_superuser
        and agent.organization_id is not None
        and agent.organization_id != user.organization_id
    ):
        _service_error("agent_not_found", "Agent not found.", 404)
    return org_id


async def _finding_id_or_422(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    suite_agent_id: UUID | None,
    finding_id: UUID | None,
) -> UUID | None:
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
        _service_error("finding_not_found", "Finding not found.", 404)
    if not user.is_superuser and finding.org_id != user.organization_id:
        _service_error("finding_not_found", "Finding not found.", 404)
    if suite_agent_id is not None and finding.agent_id != suite_agent_id:
        _service_error(
            "finding_wrong_agent",
            "Finding belongs to a different agent than the test.",
            422,
        )
    return finding.id


async def _frozen_case_content(
    db: AsyncSession, user: UserPrincipal, *, fixture: dict, assertions: list
) -> tuple[dict, list]:
    from src.services.agent_evaluations.assertions import (
        AssertionDefinitionError,
        freeze_semantic_judges,
    )
    from src.services.agent_evaluations.simulator_models import (
        FixtureError,
        redact_value,
        validate_fixture,
    )

    try:
        validate_fixture({"version": 1, **fixture})
        frozen = await freeze_semantic_judges(
            db, list(assertions), is_superuser=user.is_superuser
        )
    except (FixtureError, AssertionDefinitionError) as exc:
        _service_error("invalid_case_content", str(exc), 422)
    return redact_value(dict(fixture)), frozen


async def create_agent_test(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    body: AgentTestCreate,
) -> tuple[AgentEvaluationCase, AgentEvaluationSuite]:
    """Insert an accepted v1 test into the agent's default collection."""
    from src.services.agent_evaluations import quotas as eval_quotas

    agent = await _agent_or_404(db, user, agent_id)
    org_id = _default_org_id(user, agent)
    suite = await get_or_create_default_suite(
        db, agent_id=agent.id, org_id=org_id, created_by=user.email
    )
    # Lock the suite across count/check/insert so concurrent creates cannot
    # jointly overshoot the per-suite quota.
    suite = await _locked_default_suite(db, suite_id=suite.id)
    try:
        eval_quotas.check_fixture_size(dict(body.fixture))
    except eval_quotas.QuotaExceeded as exc:
        _service_error("quota_exceeded", str(exc), exc.status_code)
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
        _service_error("quota_exceeded", str(exc), exc.status_code)
    frozen_fixture, frozen_assertions = await _frozen_case_content(
        db, user, fixture=dict(body.fixture), assertions=list(body.assertions)
    )
    case = AgentEvaluationCase(
        id=uuid4(),
        suite_id=suite.id,
        logical_test_id=uuid4(),
        name=body.name,
        position=body.position,
        enabled=body.enabled,
        version=1,
        input=body.input,
        fixture=frozen_fixture,
        simulator_policy=dict(body.simulator_policy),
        assertions=frozen_assertions,
        expected_tools=list(body.expected_tools),
        forbidden_tools=list(body.forbidden_tools),
        output_schema=body.output_schema,
        repetitions=body.repetitions,
        scoring_policy=dict(body.scoring_policy),
        provenance="finding" if body.finding_id is not None else body.provenance,
        provenance_run_ids=[str(r) for r in body.provenance_run_ids],
        finding_id=await _finding_id_or_422(
            db, user, suite_agent_id=agent.id, finding_id=body.finding_id
        ),
        tags=list(body.tags),
        accepted=True,
    )
    db.add(case)
    await db.flush()
    return case, suite


async def current_test_versions(
    db: AsyncSession, *, agent_id: UUID, org_id: UUID | None
) -> list[tuple[AgentEvaluationCase, AgentEvaluationSuite]]:
    """Latest accepted version per logical id across the agent's suites."""
    suite_rows = (
        (
            await db.execute(
                select(AgentEvaluationSuite).where(
                    AgentEvaluationSuite.agent_id == agent_id,
                    AgentEvaluationSuite.org_id.is_not_distinct_from(org_id),
                )
            )
        )
        .scalars()
        .all()
    )
    if not suite_rows:
        return []
    suite_ids = [row.id for row in suite_rows]
    by_suite = {row.id: row for row in suite_rows}
    case_rows = (
        (
            await db.execute(
                select(AgentEvaluationCase)
                .where(
                    AgentEvaluationCase.suite_id.in_(suite_ids),
                    AgentEvaluationCase.accepted.is_(True),
                )
                .order_by(
                    AgentEvaluationCase.logical_test_id,
                    AgentEvaluationCase.version.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    latest: dict[UUID, AgentEvaluationCase] = {}
    for row in case_rows:
        latest.setdefault(row.logical_test_id, row)
    return [(row, by_suite[row.suite_id]) for row in latest.values()]


async def list_agent_tests(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[tuple[AgentEvaluationCase, AgentEvaluationSuite]], int]:
    agent = await _agent_or_404(db, user, agent_id)
    org_id = agent.organization_id or user.organization_id
    items = await current_test_versions(db, agent_id=agent.id, org_id=org_id)
    items.sort(key=lambda pair: (pair[0].name, str(pair[0].logical_test_id)))
    total = len(items)
    return items[offset : offset + limit], total


async def get_agent_test(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    logical_test_id: UUID,
) -> tuple[AgentEvaluationCase, AgentEvaluationSuite]:
    agent = await _agent_or_404(db, user, agent_id)
    org_id = agent.organization_id or user.organization_id
    for row, suite in await current_test_versions(
        db, agent_id=agent.id, org_id=org_id
    ):
        if row.logical_test_id == logical_test_id:
            return row, suite
    _service_error("test_not_found", "Test not found.", 404)


async def _locked_default_suite(
    db: AsyncSession, *, suite_id: UUID
):
    """Re-select the default suite locked for check-and-insert serialization."""
    from src.models.orm.agent_evaluations import AgentEvaluationSuite as Suite

    return (
        await db.execute(
            select(Suite)
            .where(Suite.id == suite_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def edit_agent_test(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    logical_test_id: UUID,
    body: AgentTestUpdate,
) -> tuple[AgentEvaluationCase, AgentEvaluationSuite]:
    """Insert the next accepted version into the default collection."""
    from src.services.agent_evaluations import quotas as eval_quotas

    agent = await _agent_or_404(db, user, agent_id)
    org_id = _default_org_id(user, agent)
    suite = await get_or_create_default_suite(
        db, agent_id=agent.id, org_id=org_id, created_by=user.email
    )
    # Serialize concurrent edits behind the default suite row lock, then
    # re-read the current version inside the lock: a pre-lock read could
    # match a version a concurrent edit has since superseded.
    suite = await _locked_default_suite(db, suite_id=suite.id)
    current, _origin = await get_agent_test(
        db, user, agent_id=agent.id, logical_test_id=logical_test_id
    )
    if (
        body.expected_version is not None
        and body.expected_version != current.version
    ):
        _service_error(
            "stale_version", "Test version changed; reload and retry.", 409
        )
    from src.services.agent_evaluations.simulator_models import (
        redact_value,
        validate_fixture,
    )

    fields = body.model_fields_set
    if body.fixture is not None:
        try:
            validate_fixture({"version": 1, **body.fixture})
        except Exception as exc:
            _service_error("invalid_case_content", str(exc), 422)
        fixture_value = redact_value(dict(body.fixture))
    else:
        fixture_value = dict(current.fixture or {})
    if body.assertions is not None:
        # Freeze only freshly supplied assertions. Omitted assertions are
        # copied byte-identical: re-freezing already-frozen judge snapshots
        # would spuriously reject valid edits.
        _, assertions_value = await _frozen_case_content(
            db, user, fixture={}, assertions=list(body.assertions)
        )
    else:
        assertions_value = list(current.assertions or [])
    try:
        eval_quotas.check_fixture_size(dict(fixture_value))
    except eval_quotas.QuotaExceeded as exc:
        _service_error("quota_exceeded", str(exc), exc.status_code)
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
        _service_error("quota_exceeded", str(exc), exc.status_code)
    values: dict[str, Any] = {
        "name": body.name if body.name is not None else current.name,
        "position": body.position if body.position is not None else current.position,
        "enabled": body.enabled if body.enabled is not None else current.enabled,
        "input": body.input if "input" in fields else current.input,
        "fixture": fixture_value,
        "simulator_policy": (
            dict(body.simulator_policy)
            if body.simulator_policy is not None
            else dict(current.simulator_policy or {})
        ),
        "assertions": assertions_value,
        "expected_tools": (
            list(body.expected_tools)
            if body.expected_tools is not None
            else list(current.expected_tools or [])
        ),
        "forbidden_tools": (
            list(body.forbidden_tools)
            if body.forbidden_tools is not None
            else list(current.forbidden_tools or [])
        ),
        "output_schema": (
            body.output_schema if "output_schema" in fields else current.output_schema
        ),
        "repetitions": (
            body.repetitions if body.repetitions is not None else current.repetitions
        ),
        "scoring_policy": (
            dict(body.scoring_policy)
            if body.scoring_policy is not None
            else dict(current.scoring_policy or {})
        ),
        "tags": list(body.tags) if body.tags is not None else list(current.tags or []),
    }
    case = AgentEvaluationCase(
        id=uuid4(),
        suite_id=suite.id,
        logical_test_id=current.logical_test_id,
        version=current.version + 1,
        provenance=current.provenance,
        provenance_run_ids=list(current.provenance_run_ids or []),
        finding_id=current.finding_id,
        accepted=True,
        **values,
    )
    db.add(case)
    await db.flush()
    return case, suite
