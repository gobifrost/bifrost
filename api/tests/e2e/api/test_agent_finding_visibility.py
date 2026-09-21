"""Visibility rules for review-derived agent findings."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_finding_visibility import visible_agent_finding_condition
from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_reviews import AgentReviewDefinition, AgentReviewRun, AgentReviewVersion
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.users import Role, UserRole

pytestmark = pytest.mark.asyncio


def _principal(user, *, is_external: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name,
        is_superuser=user.is_superuser,
        is_external=is_external,
    )


async def _visible_page(
    db: AsyncSession, user, *, ids: list[UUID], limit: int, offset: int
) -> tuple[int, list[UUID]]:
    principal = _principal(user)
    predicate = (
        AgentFinding.id.in_(ids),
        visible_agent_finding_condition(principal),
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(AgentFinding)
            .where(*predicate)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(AgentFinding.id)
            .where(*predicate)
            .order_by(AgentFinding.created_at, AgentFinding.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return total, list(rows)


def _ref(run: AgentRun) -> dict[str, str | None]:
    return {
        "run_id": str(run.id),
        "agent_id": str(run.agent_id) if run.agent_id else None,
        "org_id": str(run.org_id) if run.org_id else None,
        "root_run_id": str(run.root_run_id) if run.root_run_id else None,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "trigger_type": run.trigger_type,
    }


async def _cleanup_visibility_fixture(db: AsyncSession, fixture: SimpleNamespace) -> None:
    await db.rollback()
    await db.execute(delete(AgentFinding).where(AgentFinding.id.in_(fixture.finding_ids)))
    await db.execute(delete(AgentReviewRun).where(AgentReviewRun.id == fixture.review_run_id))
    await db.execute(delete(AgentReviewVersion).where(AgentReviewVersion.id == fixture.version_id))
    await db.execute(delete(AgentReviewDefinition).where(AgentReviewDefinition.id == fixture.review_id))
    await db.execute(delete(AgentRun).where(AgentRun.id.in_(fixture.run_ids)))
    await db.commit()


async def _review_finding_fixture(
    e2e_client,
    platform_admin,
    org1_user,
    db_session: AsyncSession,
    *,
    source_refs: list[dict] | None = None,
    finding_refs: list[dict] | None = None,
) -> SimpleNamespace:
    agent_resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Review Finding Visibility {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": str(org1_user.organization_id),
        },
        headers=platform_admin.headers,
    )
    assert agent_resp.status_code == 201, agent_resp.text
    agent_id = UUID(agent_resp.json()["id"])
    root_run = AgentRun(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        trigger_type="manual",
        status="completed",
        caller_user_id=str(org1_user.user_id),
        iterations_used=1,
        tokens_used=10,
        input={"message": "review me"},
        output={"text": "ok"},
        completed_at=datetime.now(timezone.utc),
    )
    child_run = AgentRun(
        id=uuid4(),
        agent_id=None,
        org_id=org1_user.organization_id,
        root_run_id=root_run.id,
        parent_run_id=root_run.id,
        trigger_type="delegation",
        status="completed",
        caller_user_id=str(org1_user.user_id),
        iterations_used=1,
        tokens_used=5,
        input={"message": "delegated"},
        output={"text": "child ok"},
        completed_at=datetime.now(timezone.utc),
    )
    review = AgentReviewDefinition(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        name="Visibility review",
        latest_version=1,
        created_by=org1_user.user_id,
    )
    version = AgentReviewVersion(
        id=uuid4(),
        review_id=review.id,
        version=1,
        review_statement="Find problems.",
        created_by=org1_user.user_id,
    )
    refs = source_refs if source_refs is not None else [_ref(root_run), _ref(child_run)]
    review_run = AgentReviewRun(
        id=uuid4(),
        review_id=review.id,
        review_version_id=version.id,
        review_version=1,
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        requested_by_user_id=org1_user.user_id,
        requested_run_ids=[root_run.id],
        selected_run_ids=[root_run.id],
        source_evidence={"selected_runs": [{"run_id": str(root_run.id)}]},
        source_refs=refs,
        profile_snapshot={"provider": "test"},
        profile_fingerprint="profile-fingerprint",
        request_fingerprint="request-fingerprint",
        input_bytes=10,
    )
    finding = AgentFinding(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org1_user.organization_id,
        status="open",
        description="Reviewed issue.",
        expected_behavior="Do the right thing.",
        source_kind="run",
        source_run_id=root_run.id,
        finding_kind="problem",
        source_review_id=review.id,
        source_review_version_id=version.id,
        source_review_run_id=review_run.id,
        source_review_version=1,
        source_run_refs=finding_refs if finding_refs is not None else [_ref(root_run)],
        source_ordinal=0,
        created_by=org1_user.user_id,
    )
    db_session.add_all([root_run, child_run])
    await db_session.flush()
    db_session.add(review)
    await db_session.flush()
    db_session.add(version)
    await db_session.flush()
    db_session.add(review_run)
    await db_session.flush()
    db_session.add(finding)
    await db_session.commit()
    return SimpleNamespace(
        agent_id=agent_id,
        root_run_id=root_run.id,
        child_run_id=child_run.id,
        review_id=review.id,
        version_id=version.id,
        review_run_id=review_run.id,
        finding_id=finding.id,
        run_ids=[root_run.id, child_run.id],
        finding_ids=[finding.id],
    )


async def test_review_derived_finding_visible_when_all_contributors_readable(
    e2e_client, platform_admin, org1_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        listed = e2e_client.get(
            "/api/agent-findings",
            params={"agent_id": str(fixture.agent_id)},
            headers=org1_user.headers,
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [str(fixture.finding_id)]

        fetched = e2e_client.get(
            f"/api/agent-findings/{fixture.finding_id}", headers=org1_user.headers
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["description"] == "Reviewed issue."
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


async def test_review_derived_finding_hidden_when_uncited_contributor_revoked(
    e2e_client, platform_admin, org1_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == fixture.child_run_id))
        await db_session.commit()

        listed = e2e_client.get(
            "/api/agent-findings",
            params={"agent_id": str(fixture.agent_id)},
            headers=org1_user.headers,
        )
        assert listed.status_code == 200, listed.text
        assert listed.json() == []
        assert e2e_client.get(
            f"/api/agent-findings/{fixture.finding_id}", headers=org1_user.headers
        ).status_code == 404
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


@pytest.mark.parametrize(
    "bad_source_refs,bad_finding_refs",
    [
        ({"not": "an array"}, None),
        ([{"run_id": "not-a-uuid", "agent_id": None, "org_id": None, "root_run_id": None, "parent_run_id": None, "trigger_type": "manual"}], None),
        (None, [{"run_id": "not-a-uuid", "agent_id": None, "org_id": None, "root_run_id": None, "parent_run_id": None, "trigger_type": "manual"}]),
        (None, [{"run_id": str(uuid4()), "org_id": None, "root_run_id": None, "parent_run_id": None, "trigger_type": "manual"}]),
    ],
)
async def test_review_derived_finding_hidden_for_malformed_frozen_refs(
    e2e_client,
    platform_admin,
    org1_user,
    db_session: AsyncSession,
    bad_source_refs,
    bad_finding_refs,
):
    fixture = await _review_finding_fixture(
        e2e_client,
        platform_admin,
        org1_user,
        db_session,
        source_refs=bad_source_refs,
        finding_refs=bad_finding_refs,
    )
    try:
        listed = e2e_client.get(
            "/api/agent-findings",
            params={"agent_id": str(fixture.agent_id)},
            headers=org1_user.headers,
        )
        assert listed.status_code == 200, listed.text
        assert listed.json() == []
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


async def test_review_derived_finding_allows_nullable_deleted_descendant_agent_identity(
    e2e_client, platform_admin, org1_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        listed = e2e_client.get(
            "/api/agent-findings",
            params={"agent_id": str(fixture.agent_id)},
            headers=org1_user.headers,
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [str(fixture.finding_id)]
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


async def test_hidden_review_derived_finding_cannot_be_updated(
    e2e_client, platform_admin, org1_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == fixture.child_run_id))
        await db_session.commit()

        patched = e2e_client.patch(
            f"/api/agent-findings/{fixture.finding_id}",
            json={"status": "dismissed", "description": "mutated"},
            headers=org1_user.headers,
        )
        assert patched.status_code == 404, patched.text
        row = await db_session.get(AgentFinding, fixture.finding_id)
        assert row is not None
        assert row.status == "open"
        assert row.description == "Reviewed issue."
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


async def test_shared_visibility_predicate_filters_before_count_and_page(
    e2e_client, platform_admin, org1_user, db_session: AsyncSession
):
    visible = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    hidden = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == hidden.child_run_id))
        await db_session.commit()
        ids = [visible.finding_id, hidden.finding_id]

        total, page = await _visible_page(db_session, org1_user, ids=ids, limit=1, offset=0)
        assert total == 1
        assert page == [visible.finding_id]

        total, empty_page = await _visible_page(db_session, org1_user, ids=ids, limit=1, offset=1)
        assert total == 1
        assert empty_page == []
    finally:
        await _cleanup_visibility_fixture(db_session, visible)
        await _cleanup_visibility_fixture(db_session, hidden)
        e2e_client.delete(f"/api/agents/{visible.agent_id}", headers=platform_admin.headers)
        e2e_client.delete(f"/api/agents/{hidden.agent_id}", headers=platform_admin.headers)


async def test_shared_visibility_predicate_hides_foreign_tenant_finding(
    e2e_client, platform_admin, org1_user, org2_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        total, page = await _visible_page(
            db_session, org2_user, ids=[fixture.finding_id], limit=10, offset=0
        )
        assert total == 0
        assert page == []
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


async def test_shared_visibility_predicate_enforces_agent_private_owner(
    e2e_client, platform_admin, org1_user, bob_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        agent = await db_session.get(Agent, fixture.agent_id)
        assert agent is not None
        agent.access_level = AgentAccessLevel.PRIVATE
        agent.owner_user_id = bob_user.user_id
        await db_session.commit()

        total, page = await _visible_page(
            db_session, org1_user, ids=[fixture.finding_id], limit=10, offset=0
        )
        assert total == 0
        assert page == []

        total, page = await _visible_page(
            db_session, bob_user, ids=[fixture.finding_id], limit=10, offset=0
        )
        assert total == 1
        assert page == [fixture.finding_id]
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


async def test_shared_visibility_predicate_enforces_agent_role_grant(
    e2e_client, platform_admin, org1_user, bob_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    role_id = uuid4()
    role = Role(id=role_id, name=f"finding-role-{uuid4().hex[:8]}", created_by=str(platform_admin.user_id))
    try:
        agent = await db_session.get(Agent, fixture.agent_id)
        assert agent is not None
        agent.access_level = AgentAccessLevel.ROLE_BASED
        db_session.add(role)
        await db_session.flush()
        db_session.add(
            AgentRole(agent_id=fixture.agent_id, role_id=role.id, assigned_by=str(platform_admin.user_id))
        )
        await db_session.flush()
        await db_session.commit()

        total, page = await _visible_page(
            db_session, org1_user, ids=[fixture.finding_id], limit=10, offset=0
        )
        assert total == 0
        assert page == []

        db_session.add(
            UserRole(user_id=org1_user.user_id, role_id=role.id, assigned_by=str(platform_admin.user_id))
        )
        await db_session.commit()
        total, page = await _visible_page(
            db_session, org1_user, ids=[fixture.finding_id], limit=10, offset=0
        )
        assert total == 1
        assert page == [fixture.finding_id]
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(UserRole).where(UserRole.role_id == role_id)
        )
        await db_session.execute(
            delete(AgentRole).where(AgentRole.role_id == role_id)
        )
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)


async def test_review_derived_finding_hidden_for_wrong_org_contributor_ref(
    e2e_client, platform_admin, org1_user, org2_user, db_session: AsyncSession
):
    fixture = await _review_finding_fixture(e2e_client, platform_admin, org1_user, db_session)
    try:
        review_run = await db_session.get(AgentReviewRun, fixture.review_run_id)
        assert review_run is not None
        refs = [dict(item) for item in review_run.source_refs]
        refs[1]["org_id"] = str(org2_user.organization_id)
        review_run.source_refs = refs
        await db_session.commit()

        total, page = await _visible_page(
            db_session, platform_admin, ids=[fixture.finding_id], limit=10, offset=0
        )
        assert total == 0
        assert page == []
    finally:
        await _cleanup_visibility_fixture(db_session, fixture)
        e2e_client.delete(f"/api/agents/{fixture.agent_id}", headers=platform_admin.headers)
