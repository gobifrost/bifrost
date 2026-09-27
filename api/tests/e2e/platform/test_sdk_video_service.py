"""HTTP E2E happy path for SDK video enqueue and status observation.

Exercises the thin adapter (``POST /api/sdk/artifacts/video`` → 202
with ``Location``) and the shared status contract
(``GET /api/platform-jobs/{id}`` → 200 with the same job id and the
``sdk.video_generation`` type), plus the requester-visibility rule (a
different org user reads the job as missing).
"""


def test_sdk_video_enqueue_and_status_happy_path(
    e2e_client, org1_user, org2_user
):
    enqueued = e2e_client.post(
        "/api/sdk/artifacts/video",
        headers=org1_user.headers,
        json={"filename": "launch.mp4", "prompt": "A launch video"},
    )
    assert enqueued.status_code == 202, enqueued.text
    body = enqueued.json()
    assert enqueued.headers["location"] == f"/api/platform-jobs/{body['job_id']}"
    assert body["status"] == "queued"
    assert body["reused"] is False

    status = e2e_client.get(
        f"/api/platform-jobs/{body['job_id']}",
        headers=org1_user.headers,
    )
    assert status.status_code == 200, status.text
    job = status.json()
    assert job["id"] == body["job_id"]
    assert job["job_type"] == "sdk.video_generation"
    assert job["resource_type"] == "artifact"
    assert job["resource_id"] == "launch.mp4"

    hidden = e2e_client.get(
        f"/api/platform-jobs/{body['job_id']}",
        headers=org2_user.headers,
    )
    assert hidden.status_code == 404, hidden.text
