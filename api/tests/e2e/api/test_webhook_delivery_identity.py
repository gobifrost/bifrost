"""Concurrent signed intake creates one Event, delivery and successful run.

One workflow job proves the cross-process fan-out contract. Signature and
identity validation, content conflicts and retention are covered below HTTP.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from tests.e2e.conftest import poll_until, write_and_register

pytestmark = pytest.mark.e2e


def test_signed_webhook_redelivery_reuses_the_persisted_event(e2e_client, platform_admin):
    """Opt-in provider delivery identity must precede workflow fan-out."""
    secret = "synthetic-webhook-" + uuid4().hex
    response = e2e_client.post("/api/events/sources", headers=platform_admin.headers, json={
        "name": f"Package Ingress {uuid4().hex[:8]}", "source_type": "webhook",
        "organization_id": None, "webhook": {"adapter_name": "generic", "config": {
            "secret": secret, "signature_header": "X-Hub-Signature-256",
            "event_type_header": "X-GitHub-Event", "delivery_id_header": "X-GitHub-Delivery",
        }},
    })
    assert response.status_code == 201, response.text
    source = response.json()
    body = b'{"action":"opened","installation":{"id":70001},"repository":{"id":80001}}'
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-GitHub-Event": "pull_request",
               "X-GitHub-Delivery": str(uuid4()), "X-Hub-Signature-256": signature}
    path = urlparse(source["webhook"]["callback_url"]).path
    workflow_path = f"package_identity_{uuid4().hex[:8]}.py"
    workflow = write_and_register(e2e_client, platform_admin.headers, workflow_path,
        'from bifrost import workflow\n@workflow\nasync def package_receipt() -> dict:\n    return {"accepted": True}\n',
        "package_receipt", organization_id=None)
    try:
        subscription = e2e_client.post(f"/api/events/sources/{source['id']}/subscriptions",
            headers=platform_admin.headers, json={"workflow_id": workflow["id"], "event_type": "pull_request"})
        assert subscription.status_code == 201, subscription.text
        # No previous POST: all callers race first acceptance of the same ID.
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda _: e2e_client.post(path, content=body, headers=headers).status_code, range(4)))
        assert statuses == [202] * 4
        def completed():
            events = e2e_client.get(f"/api/events/sources/{source['id']}/events", headers=platform_admin.headers)
            assert events.status_code == 200, events.text
            assert len(events.json()["items"]) == 1, "A repeated signed provider delivery created another Event"
            event = events.json()["items"][0]
            assert not event["failed_count"]
            return event if event["status"] == "completed" else None

        event = poll_until(completed, max_wait=30, interval=0.2)
        assert event["success_count"] == 1
        deliveries = e2e_client.get(f"/api/events/{event['id']}/deliveries", headers=platform_admin.headers)
        assert deliveries.status_code == 200, deliveries.text
        assert len(deliveries.json()["items"]) == 1
        delivery = deliveries.json()["items"][0]
        assert delivery["status"] == "success" and delivery["execution_id"]
        repeated = e2e_client.post(path, content=body, headers=headers)
        assert repeated.status_code == 202, repeated.text
        after = e2e_client.get(f"/api/events/{event['id']}/deliveries", headers=platform_admin.headers).json()
        assert [item["execution_id"] for item in after["items"]] == [delivery["execution_id"]]
    finally:
        deleted = e2e_client.delete(f"/api/events/sources/{source['id']}", headers=platform_admin.headers)
        assert deleted.status_code == 204, deleted.text
        e2e_client.delete("/api/files/editor", params={"path": workflow_path}, headers=platform_admin.headers)
