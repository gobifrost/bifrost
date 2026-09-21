"""Phase 4a: logical test identity + default collection foundation."""

from __future__ import annotations

import logging
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_test_collection import (
    get_or_create_default_suite,
    is_default_suite,
)
from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationSuite,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def collection_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Collection Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    try:
        e2e_client.delete(f"/api/agents/{agent['id']}", headers=platform_admin.headers)
    except Exception as e:
        logger.debug(f"fixture cleanup error: {e}")


async def _cleanup_collection(
    db_session: AsyncSession, *, suite_ids: list[UUID], case_ids: list[UUID]
) -> None:
    await db_session.rollback()
    if case_ids:
        await db_session.execute(
            delete(AgentEvaluationCase).where(AgentEvaluationCase.id.in_(case_ids))
        )
    if suite_ids:
        await db_session.execute(
            delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id.in_(suite_ids))
        )
    await db_session.commit()


async def test_default_suite_idempotent_per_agent_org(
    collection_agent, org1_user, db_session: AsyncSession
):
    agent_id = UUID(collection_agent["id"])
    org_id = org1_user.organization_id
    suite_ids: list[UUID] = []
    try:
        first = await get_or_create_default_suite(
            db_session, agent_id=agent_id, org_id=org_id, created_by="test"
        )
        await db_session.commit()
        second = await get_or_create_default_suite(
            db_session, agent_id=agent_id, org_id=org_id, created_by="test"
        )
        await db_session.commit()
        assert first.id == second.id
        suite_ids.append(first.id)
        assert first.is_default is True
        assert first.status == "published"
        assert first.version == 1
        assert first.org_id == org_id
        assert first.agent_id == agent_id
        assert await is_default_suite(db_session, suite_id=first.id) is True
        assert await is_default_suite(db_session, suite_id=uuid4()) is False
    finally:
        await _cleanup_collection(db_session, suite_ids=suite_ids, case_ids=[])


async def test_default_suite_constraints(
    collection_agent, org1_user, db_session: AsyncSession
):
    agent_id = UUID(collection_agent["id"])
    org_id = org1_user.organization_id
    suite_ids: list[UUID] = []
    try:
        first = await get_or_create_default_suite(
            db_session, agent_id=agent_id, org_id=org_id
        )
        await db_session.commit()
        suite_ids.append(first.id)
        # Second default for the same (org, agent) violates the partial unique.
        db_session.add(
            AgentEvaluationSuite(
                id=uuid4(),
                org_id=org_id,
                agent_id=agent_id,
                name="Default Tests",
                status="published",
                version=1,
                is_default=True,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()
        # Default scope requires org and agent.
        db_session.add(
            AgentEvaluationSuite(
                id=uuid4(),
                org_id=None,
                agent_id=agent_id,
                name="Default Tests",
                status="published",
                version=1,
                is_default=True,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()
        # Ordinary suites are unaffected by the default fences.
        plain = AgentEvaluationSuite(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent_id,
            name=f"Plain Suite {uuid4().hex[:8]}",
            status="draft",
            version=1,
        )
        db_session.add(plain)
        await db_session.flush()
        suite_ids.append(plain.id)
        assert plain.is_default is False
        assert await is_default_suite(db_session, suite_id=plain.id) is False
        await db_session.commit()
    finally:
        await _cleanup_collection(db_session, suite_ids=suite_ids, case_ids=[])


async def test_case_logical_identity_versions(
    collection_agent, org1_user, db_session: AsyncSession
):
    agent_id = UUID(collection_agent["id"])
    org_id = org1_user.organization_id
    suite_ids: list[UUID] = []
    case_ids: list[UUID] = []
    try:
        suite = await get_or_create_default_suite(
            db_session, agent_id=agent_id, org_id=org_id
        )
        await db_session.commit()
        suite_ids.append(suite.id)
        first = AgentEvaluationCase(suite_id=suite.id, name="logical-case", version=1)
        db_session.add(first)
        await db_session.flush()
        case_ids.append(first.id)
        # ORM default assigns a logical id when omitted.
        assert first.logical_test_id is not None
        second = AgentEvaluationCase(
            suite_id=suite.id,
            name="logical-case",
            version=2,
            logical_test_id=first.logical_test_id,
        )
        db_session.add(second)
        await db_session.flush()
        case_ids.append(second.id)
        assert second.logical_test_id == first.logical_test_id
        sid = suite.id
        logical_id = first.logical_test_id
        await db_session.commit()
        # Same logical id + same version is rejected; the rollback below
        # discards only the duplicate attempt.
        db_session.add(
            AgentEvaluationCase(
                suite_id=sid,
                name="logical-case-dup",
                version=2,
                logical_test_id=logical_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()
        rows = (
            (
                await db_session.execute(
                    select(AgentEvaluationCase).where(
                        AgentEvaluationCase.logical_test_id == logical_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(row.version for row in rows) == [1, 2]
    finally:
        await _cleanup_collection(db_session, suite_ids=suite_ids, case_ids=case_ids)


async def test_default_suites_per_agent_share_reserved_name(
    e2e_client, platform_admin, org1, db_session: AsyncSession
):
    """Two agents in one org each get a default; ordinary same-name suites coexist."""
    agent_ids: list[UUID] = []
    suite_ids: list[UUID] = []
    case_ids: list[UUID] = []
    try:
        for i in range(2):
            resp = e2e_client.post(
                "/api/agents",
                json={
                    "name": f"Default Name Agent {i} {uuid4().hex[:6]}",
                    "system_prompt": "Reply only with ok.",
                    "channels": ["chat"],
                    "access_level": "authenticated",
                    "organization_id": org1["id"],
                },
                headers=platform_admin.headers,
            )
            assert resp.status_code == 201, resp.text
            agent_ids.append(UUID(resp.json()["id"]))
        org_id = UUID(org1["id"])
        first = await get_or_create_default_suite(
            db_session, agent_id=agent_ids[0], org_id=org_id
        )
        await db_session.commit()
        second = await get_or_create_default_suite(
            db_session, agent_id=agent_ids[1], org_id=org_id
        )
        await db_session.commit()
        suite_ids.extend([first.id, second.id])
        assert first.id != second.id
        assert first.name == second.name == "Default Tests"
        # An ordinary suite may reuse the reserved name/version beside defaults.
        plain = AgentEvaluationSuite(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent_ids[0],
            name="Default Tests",
            status="draft",
            version=1,
        )
        db_session.add(plain)
        await db_session.commit()
        suite_ids.append(plain.id)
        # Conflict path: a directly inserted duplicate resolves to the winner.
        raced = await get_or_create_default_suite(
            db_session, agent_id=agent_ids[0], org_id=org_id
        )
        await db_session.commit()
        assert raced.id == first.id
    finally:
        await _cleanup_collection(db_session, suite_ids=suite_ids, case_ids=case_ids)
        for agent_id in agent_ids:
            try:
                e2e_client.delete(
                    f"/api/agents/{agent_id}", headers=platform_admin.headers
                )
            except Exception as e:
                logger.debug(f"agent cleanup error: {e}")


async def test_agent_delete_removes_default_suite_keeps_named(
    e2e_client, platform_admin, org1, org1_user, db_session: AsyncSession
):
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Delete Flow Agent {uuid4().hex[:6]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent_id = UUID(resp.json()["id"])
    org_id = UUID(org1["id"])
    try:
        default = await get_or_create_default_suite(
            db_session, agent_id=agent_id, org_id=org_id
        )
        await db_session.commit()
        default_id = default.id
        named = AgentEvaluationSuite(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent_id,
            name=f"Named Survivor {uuid4().hex[:6]}",
            status="draft",
            version=1,
        )
        db_session.add(named)
        await db_session.commit()
        named_id = named.id

        deleted = e2e_client.delete(
            f"/api/agents/{agent_id}", headers=platform_admin.headers
        )
        assert deleted.status_code == 204, deleted.text

        # Expire the session cache: the delete ran in the API's session and
        # this fixture uses expire_on_commit=False, so the identity map
        # would otherwise serve the stale row without SQL.
        await db_session.rollback()
        default_row = (
            await db_session.execute(
                select(AgentEvaluationSuite)
                .where(AgentEvaluationSuite.id == default_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        assert default_row is None
        survivor = (
            await db_session.execute(
                select(AgentEvaluationSuite)
                .where(AgentEvaluationSuite.id == named_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        assert survivor is not None
        assert survivor.agent_id is None
        await db_session.execute(
            delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == named_id)
        )
        await db_session.commit()
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id == default_id
            )
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id.in_([default_id, named_id])
            )
        )
        await db_session.commit()
        try:
            e2e_client.delete(
                f"/api/agents/{agent_id}", headers=platform_admin.headers
            )
        except Exception as e:
            logger.debug(f"agent cleanup error: {e}")


async def test_concurrent_create_collapses_onto_winner(
    collection_agent, org1_user, db_session: AsyncSession
):
    """A lost insert race reselects the winner instead of raising."""
    from unittest.mock import patch

    from shared import agent_test_collection as collection_module

    agent_id = UUID(collection_agent["id"])
    org_id = org1_user.organization_id
    suite_ids: list[UUID] = []
    try:
        winner = AgentEvaluationSuite(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent_id,
            name="Default Tests",
            status="published",
            version=1,
            is_default=True,
        )
        db_session.add(winner)
        await db_session.commit()
        suite_ids.append(winner.id)

        real_find = collection_module._find_default_suite
        calls = 0

        async def _blind_first_lookup(db, *, agent_id, org_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            return await real_find(db, agent_id=agent_id, org_id=org_id)

        with patch.object(
            collection_module, "_find_default_suite", side_effect=_blind_first_lookup
        ):
            resolved = await get_or_create_default_suite(
                db_session, agent_id=agent_id, org_id=org_id
            )
        await db_session.commit()
        assert calls == 2
        assert resolved.id == winner.id
    finally:
        await _cleanup_collection(db_session, suite_ids=suite_ids, case_ids=[])


async def test_raw_agent_delete_clears_defaults_for_any_path(
    org1, db_session: AsyncSession
):
    """Non-route deletions (sync sweep, indexers) hit the same trigger."""
    from src.models.orm.agents import Agent

    org_id = UUID(org1["id"])
    agent_id = uuid4()
    db_session.add(
        Agent(
            id=agent_id,
            name=f"Raw Delete Agent {uuid4().hex[:6]}",
            system_prompt="Reply only with ok.",
            channels=["chat"],
            access_level="authenticated",
            organization_id=org_id,
            created_by="test",
        )
    )
    await db_session.commit()
    try:
        default = await get_or_create_default_suite(
            db_session, agent_id=agent_id, org_id=org_id
        )
        await db_session.commit()
        default_id = default.id
        named = AgentEvaluationSuite(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent_id,
            name=f"Raw Named {uuid4().hex[:6]}",
            status="draft",
            version=1,
        )
        db_session.add(named)
        await db_session.commit()
        named_id = named.id

        # Same statement shape as sync/indexer paths: no route involved.
        await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.commit()

        gone = (
            await db_session.execute(
                select(AgentEvaluationSuite)
                .where(AgentEvaluationSuite.id == default_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        assert gone is None
        survivor = (
            await db_session.execute(
                select(AgentEvaluationSuite)
                .where(AgentEvaluationSuite.id == named_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        assert survivor is not None
        assert survivor.agent_id is None
    finally:
        await _cleanup_collection(
            db_session, suite_ids=[default_id, named_id], case_ids=[]
        )
        await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.commit()
