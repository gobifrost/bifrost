from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

pytestmark = pytest.mark.e2e


def _create_app(
    e2e_client,
    headers,
    slug: str,
    *,
    organization_id: str | None = None,
) -> dict:
    body = {
        "name": slug,
        "slug": slug,
        "app_model": "inline_v1",
    }
    if organization_id is not None:
        body["organization_id"] = organization_id
    response = e2e_client.post(
        "/api/applications",
        headers=headers,
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _poll(e2e_client, headers, job_id: str) -> dict:
    for _ in range(120):
        response = e2e_client.get(
            f"/api/platform-jobs/{job_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.25)
    raise AssertionError(f"publish job {job_id} did not finish")


def _poll_notification(
    e2e_client,
    headers,
    notification_id: str,
    status: str,
) -> dict:
    for _ in range(120):
        response = e2e_client.get(
            f"/api/notifications/{notification_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] == status:
            return body
        time.sleep(0.25)
    raise AssertionError(
        f"notification {notification_id} did not reach status {status}"
    )


def test_publish_success_deduplication_and_requester_visibility(
    e2e_client, platform_admin, second_platform_admin, org1_user, provider_org_user
):
    # Global scope (explicit organization_id=None) so the provider-org
    # member can read it — their REST read access is global-or-own-org —
    # while still being refused the publish itself.
    create_response = e2e_client.post(
        "/api/applications",
        headers=platform_admin.headers,
        json={
            "name": f"async-publish-{uuid.uuid4().hex[:8]}",
            "slug": f"async-publish-{uuid.uuid4().hex[:8]}",
            "app_model": "inline_v1",
            "organization_id": None,
        },
    )
    assert create_response.status_code == 201, create_response.text
    app = create_response.json()
    barrier = Barrier(2)

    def enqueue():
        barrier.wait()
        return e2e_client.post(
            f"/api/applications/{app['id']}/publish",
            headers=platform_admin.headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: enqueue(), range(2)))

    assert all(response.status_code == 202 for response in responses), [
        response.text for response in responses
    ]
    bodies = [response.json() for response in responses]
    assert len({body["job_id"] for body in bodies}) == 1
    assert len({body["notification_id"] for body in bodies}) == 1
    assert sorted(body["reused"] for body in bodies) == [False, True]
    accepted = bodies[0]
    assert all(
        response.headers["location"].endswith(f"/{accepted['job_id']}")
        for response in responses
    )
    assert accepted["notification_id"]
    # A different platform admin (not the one who enqueued the job) hits the
    # same dedupe path. Publishing is platform-admin only: a provider-org
    # member is refused (404) before reaching the in-progress check.
    refused = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=provider_org_user.headers,
    )
    assert refused.status_code == 404, refused.text
    duplicate = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=second_platform_admin.headers,
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"] == (
        "An application publish is already in progress"
    )
    visible = e2e_client.get(
        f"/api/platform-jobs/{accepted['job_id']}",
        headers=platform_admin.headers,
    )
    assert visible.status_code == 200, visible.text
    assert "payload" not in visible.json()
    assert visible.json()["job_type"] == "application.publish"
    hidden = e2e_client.get(
        f"/api/platform-jobs/{accepted['job_id']}",
        headers=org1_user.headers,
    )
    assert hidden.status_code == 404
    body = _poll(
        e2e_client,
        platform_admin.headers,
        accepted["job_id"],
    )
    assert body["status"] == "succeeded", body
    assert body["result"]["files_published"] >= 2
    assert body["completed_at"] is not None
    application = e2e_client.get(
        f"/api/applications/{app['slug']}",
        headers=platform_admin.headers,
    )
    assert application.status_code == 200
    assert application.json()["is_published"] is True
    notification = _poll_notification(
        e2e_client,
        platform_admin.headers,
        accepted["notification_id"],
        "completed",
    )
    assert notification["percent"] == 100


def test_bundle_failure_is_persisted_and_does_not_publish(
    e2e_client,
    platform_admin,
):
    app = _create_app(
        e2e_client,
        platform_admin.headers,
        f"failed-publish-{uuid.uuid4().hex[:8]}",
    )
    update = e2e_client.put(
        f"/api/applications/{app['id']}/files/pages/index.tsx",
        headers=platform_admin.headers,
        json={"source": "export default function Index( {"},
    )
    assert update.status_code == 200, update.text

    response = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=platform_admin.headers,
    )
    assert response.status_code == 202, response.text
    body = _poll(
        e2e_client,
        platform_admin.headers,
        response.json()["job_id"],
    )

    assert body["status"] == "failed", body
    assert "Bundle build failed" in body["error"]["message"]
    notification = _poll_notification(
        e2e_client,
        platform_admin.headers,
        response.json()["notification_id"],
        "failed",
    )
    assert "Bundle build failed" in notification["error"]
    app_response = e2e_client.get(
        f"/api/applications/{app['slug']}",
        headers=platform_admin.headers,
    )
    assert app_response.status_code == 200
    assert app_response.json()["is_published"] is False
