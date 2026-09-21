"""Agent findings model + contract validation (no HTTP)."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models.contracts.agent_findings import (
    FindingCreate,
    FindingPublic,
    FindingUpdate,
)
from src.models.orm.agent_findings import AgentFinding
from src.routers.agent_findings import _lock_run_source_finding


def _finding(**overrides):
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid4(),
        agent_id=uuid4(),
        org_id=None,
        status="open",
        description="Observed wrong routing.",
        expected_behavior="Ask a clarifying question first.",
        source_kind="manual",
        source_run_id=None,
        source_sequence=None,
        external_ref=None,
        created_by=None,
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return AgentFinding(**base)


@pytest.mark.asyncio
async def test_finding_persists_with_defaults(db_session, seed_agent):
    finding = _finding(agent_id=seed_agent.id)
    db_session.add(finding)
    await db_session.flush()
    row = (
        await db_session.execute(
            select(AgentFinding).where(AgentFinding.id == finding.id)
        )
    ).scalar_one()
    assert row.status == "open"
    assert row.source_kind == "manual"
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_finding_status_check_constraint(db_session, seed_agent):
    db_session.add(_finding(agent_id=seed_agent.id, status="bogus"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_finding_source_kind_check_constraint(db_session, seed_agent):
    db_session.add(_finding(agent_id=seed_agent.id, source_kind="hunch"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_finding_create_minimal_defaults_to_manual():
    body = FindingCreate(agent_id=uuid4(), description="Wrong answer.")
    assert body.source_kind == "manual"
    assert body.source_run_id is None
    assert body.expected_behavior is None


def test_finding_create_rejects_blank_description_and_unknown_fields():
    with pytest.raises(ValidationError):
        FindingCreate(agent_id=uuid4(), description="")
    with pytest.raises(ValidationError):
        FindingCreate(
            agent_id=uuid4(), description="x", unknown_field="y"  # type: ignore[call-arg]
        )


def test_finding_update_allows_dismissal_only():
    body = FindingUpdate(status="dismissed")
    assert body.status == "dismissed"
    assert body.description is None


def test_finding_public_carries_empty_link_list_by_default():
    public = FindingPublic(
        id=uuid4(),
        agent_id=uuid4(),
        description="Wrong answer.",
    )
    assert public.status == "open"
    assert public.linked_case_ids == []


@pytest.mark.asyncio
async def test_run_sourced_create_uses_transaction_advisory_lock():
    class FakeSession:
        def __init__(self):
            self.calls = []

        async def execute(self, statement, params=None):
            self.calls.append((str(statement), params))

    run_id = uuid4()
    session = FakeSession()
    await _lock_run_source_finding(session, run_id)  # type: ignore[arg-type]

    assert session.calls == [
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))",
            {"key": str(run_id)},
        )
    ]
