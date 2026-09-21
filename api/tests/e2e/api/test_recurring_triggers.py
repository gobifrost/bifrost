"""Recurring trigger CRUD API contracts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.recurring_triggers import (
    RecurringPlatformJobTrigger,
    RecurringTriggerFire,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sched_api_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Sched API Agent {uuid4().hex[:8]}",
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


@pytest_asyncio.fixture
async def sched_api_review(e2e_client, platform_admin, sched_api_agent, db_session: AsyncSession) -> AsyncGenerator[dict, None]:
    from src.models.orm.agent_reviews import (
        AgentReviewDefinition,
        AgentReviewVersion,
    )

    resp = e2e_client.post(
        "/api/agent-reviews",
        json={
            "agent_id": sched_api_agent["id"],
            "name": f"Sched API Review {uuid4().hex[:8]}",
            "review_statement": "Find problems.",
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    review = resp.json()
    yield review
    await db_session.rollback()
    await db_session.execute(
        delete(AgentReviewVersion).where(
            AgentReviewVersion.review_id == UUID(review["id"])
        )
    )
    await db_session.execute(
        delete(AgentReviewDefinition).where(
            AgentReviewDefinition.id == UUID(review["id"])
        )
    )
    await db_session.commit()


async def _cleanup_triggers(db_session: AsyncSession, ids: list[UUID]) -> None:
    if not ids:
        return
    await db_session.rollback()
    await db_session.execute(
        delete(RecurringTriggerFire).where(RecurringTriggerFire.trigger_id.in_(ids))
    )
    await db_session.execute(
        delete(RecurringPlatformJobTrigger).where(
            RecurringPlatformJobTrigger.id.in_(ids)
        )
    )
    await db_session.commit()


def _review_body(review: dict, **overrides) -> dict:
    body = {
        "operation_type": "agent_review",
        "operation_id": review["id"],
        "operation_params": {"review_id": review["id"], "lookback_days": 7},
        "cron_expression": "*/15 * * * *",
        "timezone": "UTC",
    }
    body.update(overrides)
    return body


async def test_trigger_crud_journey(
    e2e_client, org1_user, platform_admin, sched_api_review, db_session: AsyncSession
):
    created = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(sched_api_review),
        headers=org1_user.headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    trigger_id = body["id"]
    assert body["operation_type"] == "agent_review"
    assert body["org_id"] == str(org1_user.organization_id)
    assert body["enabled"] is True
    assert body["overlap_policy"] == "skip"
    assert body["requested_by_user_id"] == str(org1_user.user_id)
    assert body["operation_params"]["lookback_days"] == 7
    try:
        fetched = e2e_client.get(
            f"/api/recurring-triggers/{trigger_id}", headers=org1_user.headers
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["cron_expression"] == "*/15 * * * *"

        listed = e2e_client.get(
            "/api/recurring-triggers",
            params={"operation_type": "agent_review"},
            headers=org1_user.headers,
        )
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == trigger_id for item in listed.json()["items"])

        updated = e2e_client.patch(
            f"/api/recurring-triggers/{trigger_id}",
            json={"cron_expression": "0 * * * *", "enabled": False},
            headers=org1_user.headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["cron_expression"] == "0 * * * *"
        assert updated.json()["enabled"] is False

        fires = e2e_client.get(
            f"/api/recurring-triggers/{trigger_id}/fires", headers=org1_user.headers
        )
        assert fires.status_code == 200, fires.text
        assert fires.json()["total"] == 0
        assert fires.json()["items"] == []

        # No delete surface: disable preserves fence history.
        assert (
            e2e_client.delete(
                f"/api/recurring-triggers/{trigger_id}", headers=org1_user.headers
            ).status_code
            in (404, 405)
        )
    finally:
        await _cleanup_triggers(db_session, [UUID(trigger_id)])


async def test_trigger_update_rebinds_requester(
    e2e_client, org1_user, platform_admin, sched_api_review, db_session: AsyncSession
):
    """Whoever last edits a schedule owns its future fires."""
    created = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(sched_api_review),
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    trigger_id = created.json()["id"]
    assert created.json()["requested_by_email"] == platform_admin.email
    try:
        updated = e2e_client.patch(
            f"/api/recurring-triggers/{trigger_id}",
            json={"cron_expression": "0 * * * *"},
            headers=org1_user.headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["requested_by_user_id"] == str(org1_user.user_id)
        assert updated.json()["requested_by_email"] == org1_user.email
    finally:
        await _cleanup_triggers(db_session, [UUID(trigger_id)])


async def test_trigger_tenant_isolation(
    e2e_client, org1_user, org2_user, platform_admin, sched_api_review, db_session: AsyncSession
):
    created = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(sched_api_review),
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text
    trigger_id = created.json()["id"]
    try:
        assert (
            e2e_client.get(
                f"/api/recurring-triggers/{trigger_id}", headers=org2_user.headers
            ).status_code
            == 404
        )
        listed = e2e_client.get("/api/recurring-triggers", headers=org2_user.headers)
        assert listed.status_code == 200, listed.text
        assert all(item["id"] != trigger_id for item in listed.json()["items"])

        denied = e2e_client.post(
            "/api/recurring-triggers",
            json=_review_body(sched_api_review),
            headers=org2_user.headers,
        )
        assert denied.status_code == 404, denied.text

        forbidden = e2e_client.get(
            "/api/recurring-triggers",
            params={"organization_id": str(org1_user.organization_id)},
            headers=org2_user.headers,
        )
        assert forbidden.status_code == 403, forbidden.text
    finally:
        await _cleanup_triggers(db_session, [UUID(trigger_id)])


async def test_trigger_validation_rejections(
    e2e_client, org1_user, sched_api_review, db_session: AsyncSession
):
    # Bad cron.
    bad_cron = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(sched_api_review, cron_expression="not cron"),
        headers=org1_user.headers,
    )
    assert bad_cron.status_code == 422, bad_cron.text

    # queue/replace rejected (DTO allows, registry forbids).
    queued = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(sched_api_review, overlap_policy="queue"),
        headers=org1_user.headers,
    )
    assert queued.status_code == 422, queued.text

    # Params must reference the operation target.
    mismatch = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(
            sched_api_review,
            operation_params={"review_id": str(uuid4()), "lookback_days": 7},
        ),
        headers=org1_user.headers,
    )
    assert mismatch.status_code == 422, mismatch.text

    # Unknown target reads as not found.
    missing = e2e_client.post(
        "/api/recurring-triggers",
        json={
            "operation_type": "agent_review",
            "operation_id": str(uuid4()),
            "operation_params": {"review_id": str(uuid4())},
            "cron_expression": "* * * * *",
        },
        headers=org1_user.headers,
    )
    assert missing.status_code == 404, missing.text

    # Unknown operation rejected by schema.
    unknown = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(sched_api_review, operation_type="agent_tuning"),
        headers=org1_user.headers,
    )
    assert unknown.status_code == 422, unknown.text


async def test_trigger_fires_visible_after_claim(
    e2e_client, org1_user, sched_api_review, db_session: AsyncSession
):
    created = e2e_client.post(
        "/api/recurring-triggers",
        json=_review_body(sched_api_review),
        headers=org1_user.headers,
    )
    assert created.status_code == 201, created.text
    trigger_id = UUID(created.json()["id"])
    try:
        fire = RecurringTriggerFire(
            id=uuid4(),
            trigger_id=trigger_id,
            scheduled_for=datetime.now(timezone.utc),
            status="skipped",
            reason="zero_source_runs",
        )
        db_session.add(fire)
        await db_session.commit()
        fires = e2e_client.get(
            f"/api/recurring-triggers/{trigger_id}/fires", headers=org1_user.headers
        )
        assert fires.status_code == 200, fires.text
        page = fires.json()
        assert page["total"] == 1
        assert page["items"][0]["status"] == "skipped"
        assert page["items"][0]["reason"] == "zero_source_runs"
    finally:
        await _cleanup_triggers(db_session, [trigger_id])
