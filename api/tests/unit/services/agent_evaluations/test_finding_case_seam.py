"""Finding-to-frozen-case seam: provenance finding + finding_id FK."""
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models.contracts.agent_evaluations import EvaluationCaseCreate
from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite
from src.models.orm.agent_findings import AgentFinding


@pytest.mark.asyncio
async def test_case_links_finding_with_finding_provenance(
    db_session, seed_agent, seed_user
):
    suite = AgentEvaluationSuite(
        id=uuid4(), org_id=None, agent_id=seed_agent.id, name="seam-suite"
    )
    db_session.add(suite)
    await db_session.flush()
    finding = AgentFinding(
        id=uuid4(),
        agent_id=seed_agent.id,
        description="Wrong tool call.",
        expected_behavior="Look up first.",
        source_kind="manual",
    )
    db_session.add(finding)
    await db_session.flush()

    case = AgentEvaluationCase(
        suite_id=suite.id,
        name="repro",
        provenance="finding",
        finding_id=finding.id,
    )
    db_session.add(case)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(AgentEvaluationCase).where(AgentEvaluationCase.id == case.id)
        )
    ).scalar_one()
    assert row.finding_id == finding.id
    assert row.provenance == "finding"


@pytest.mark.asyncio
async def test_finding_delete_preserves_case_with_null_link(
    db_session, seed_agent, seed_user
):
    suite = AgentEvaluationSuite(
        id=uuid4(), org_id=None, agent_id=seed_agent.id, name="seam-suite-2"
    )
    db_session.add(suite)
    await db_session.flush()
    finding = AgentFinding(
        id=uuid4(), agent_id=seed_agent.id, description="Stale."
    )
    db_session.add(finding)
    await db_session.flush()
    case = AgentEvaluationCase(
        suite_id=suite.id,
        name="repro",
        provenance="finding",
        finding_id=finding.id,
    )
    db_session.add(case)
    await db_session.commit()

    await db_session.delete(finding)
    await db_session.commit()
    # expire_on_commit=False in tests: refresh past the identity map to see
    # the server-side ON DELETE SET NULL.
    await db_session.refresh(case)

    row = (
        await db_session.execute(
            select(AgentEvaluationCase).where(AgentEvaluationCase.id == case.id)
        )
    ).scalar_one()
    assert row.finding_id is None


@pytest.mark.asyncio
async def test_provenance_check_still_rejects_unknown(db_session, seed_agent):
    suite = AgentEvaluationSuite(
        id=uuid4(), org_id=None, agent_id=seed_agent.id, name="seam-suite-3"
    )
    db_session.add(suite)
    await db_session.flush()
    db_session.add(
        AgentEvaluationCase(
            suite_id=suite.id, name="repro", provenance="hunch"
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


def test_case_create_accepts_finding_link():
    body = EvaluationCaseCreate(name="repro", finding_id=uuid4())
    assert body.finding_id is not None
    assert body.provenance == "manual"
