"""Header authentication at ingress, persistence, and workflow dispatch."""
import base64
import uuid

import pytest

from src.models.orm.executions import Execution
from tests.e2e.conftest import poll_until, write_and_register


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("header,token", [("X-Gitlab-Token", "synthetic-gitlab-token"), ("Authorization", "Bearer synthetic-grafana-token")])
async def test_header_token_ingress_and_delivery(e2e_client, platform_admin, db_session, header, token):
    response = e2e_client.post("/api/events/sources", headers=platform_admin.headers, json={
        "name": f"Header ingress {uuid.uuid4().hex[:8]}", "source_type": "webhook", "organization_id": None,
        "webhook": {"adapter_name": "generic", "config": {"header_auth": True, "auth_header": header, "secret": token}},
    })
    assert response.status_code == 201, response.text
    source = response.json()
    assert token not in response.text and source["webhook"]["secret_set"]
    path = f"header_auth_{uuid.uuid4().hex[:8]}.py"
    workflow = write_and_register(e2e_client, platform_admin.headers, path,
        'from bifrost import workflow\n@workflow\nasync def receipt() -> dict:\n    return {"ok": True}\n',
        "receipt", organization_id=None)
    try:
        subscription = e2e_client.post(f"/api/events/sources/{source['id']}/subscriptions", headers=platform_admin.headers,
            json={"workflow_id": workflow["id"]})
        assert subscription.status_code == 201, subscription.text
        hook = f"/api/hooks/{source['id']}"
        body = b'{ "event": "test" }'
        for headers in ({}, {header: "wrong"}):
            assert e2e_client.post(hook, content=body, headers=headers).status_code == 401
        listing = e2e_client.get(f"/api/events/sources/{source['id']}/events", headers=platform_admin.headers)
        assert listing.json()["items"] == []
        headers = {"Authorization": "Bearer synthetic", "Cookie": "session=synthetic", "X-Safe": "retained", "X-Signature-256": "signature", header: token}
        assert e2e_client.post(hook, content=body, headers=headers).status_code == 202
        def find_delivery():
            events = e2e_client.get(f"/api/events/sources/{source['id']}/events", headers=platform_admin.headers).json()["items"]
            if not events:
                return None
            deliveries = e2e_client.get(f"/api/events/{events[0]['id']}/deliveries", headers=platform_admin.headers).json()["items"]
            return (events[0], deliveries[0]) if deliveries and deliveries[0].get("execution_id") else None
        result = poll_until(find_delivery, max_wait=10.0)
        assert result is not None
        event, delivery = result
        assert token not in str(event)
        assert "Bearer synthetic" not in str(event)
        assert "session=synthetic" not in str(event)
        await db_session.rollback()
        execution = await db_session.get(Execution, uuid.UUID(delivery["execution_id"]))
        assert execution is not None
        metadata = execution.parameters["_event"]
        assert metadata["raw_body_base64"] == base64.b64encode(body).decode()
        assert metadata["headers"]["x-safe"] == "retained"
        assert metadata["headers"]["x-signature-256"] == "signature"
        assert not {"x-gitlab-token", "authorization", "cookie"} & metadata["headers"].keys()
        rotated = e2e_client.post(f"/api/events/sources/{source['id']}/rotate-secret", headers=platform_admin.headers, json={})
        assert rotated.status_code == 422 and token not in rotated.text
        replacement = "synthetic-replacement"
        config = {"header_auth": True, "auth_header": header}
        updated = e2e_client.patch(f"/api/events/sources/{source['id']}", headers=platform_admin.headers,
                                   json={"webhook": {"config": {**config, "secret": replacement}}})
        assert updated.status_code == 200 and replacement not in updated.text
        assert e2e_client.post(hook, content=body, headers={header: token}).status_code == 401
        assert e2e_client.post(hook, content=body, headers={header: replacement}).status_code == 202
        cleared = e2e_client.patch(f"/api/events/sources/{source['id']}", headers=platform_admin.headers,
                                   json={"webhook": {"config": {**config, "secret": ""}}})
        assert cleared.status_code == 200 and not cleared.json()["webhook"]["secret_set"]
        assert e2e_client.post(hook, content=body, headers={header: replacement}).status_code == 503
    finally:
        e2e_client.delete(f"/api/events/sources/{source['id']}", headers=platform_admin.headers)
        e2e_client.delete("/api/files/editor", params={"path": path}, headers=platform_admin.headers)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_header_token_can_be_initialized_and_replaced(e2e_client, platform_admin, db_session):
    # Event-only reconciliation: zero builds, installs, or worker jobs. Proves
    # a fresh managed source can receive its instance-only credential via HTTP.
    from sqlalchemy import delete
    from src.models.orm.events import EventSource
    from src.models.orm.solutions import Solution
    from src.services.solutions.deploy import SolutionBundle, SolutionDeployer, solution_entity_id

    solution = Solution(id=uuid.uuid4(), slug=f"header-custody-{uuid.uuid4().hex[:8]}", name="Header custody", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    template_id = uuid.uuid4()
    source_id = solution_entity_id(solution.id, template_id)
    bundle = SolutionBundle(solution=solution, version="0.1.0", events=[{
        "id": str(template_id), "name": "Managed header webhook", "source_type": "webhook",
        "adapter_name": "generic", "webhook_config": {"header_auth": True, "auth_header": "X-Gitlab-Token"},
        "is_active": True, "subscriptions": [],
    }])
    await SolutionDeployer(db_session).deploy(bundle, force=True)
    await db_session.commit()
    base = f"/api/events/sources/{source_id}"
    hook = f"/api/hooks/{source_id}"
    try:
        assert e2e_client.post(hook, json={}, headers={"X-Gitlab-Token": "before"}).status_code == 503
        locked = e2e_client.patch(base, headers=platform_admin.headers, json={"webhook": {"config": {"secret": "forbidden"}}})
        assert locked.status_code == 409
        for value in ("first-synthetic-token", "replacement-synthetic-token"):
            changed = e2e_client.post(f"{base}/rotate-secret", headers=platform_admin.headers, json={"secret": value})
            assert changed.status_code == 200, changed.text
            assert value not in changed.text and changed.json()["webhook"]["secret_set"]
            assert e2e_client.post(hook, json={}, headers={"X-Gitlab-Token": value}).status_code == 202
        assert e2e_client.post(hook, json={}, headers={"X-Gitlab-Token": "first-synthetic-token"}).status_code == 401
    finally:
        await db_session.rollback()
        await db_session.execute(delete(EventSource).where(EventSource.id == source_id))
        await db_session.execute(delete(Solution).where(Solution.id == solution.id))
        await db_session.commit()
