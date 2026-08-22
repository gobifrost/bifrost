"""End-to-end coverage for artifact retention settings and cleanup jobs."""

import time


def test_artifact_retention_settings_enqueue_durable_cleanup(
    e2e_client,
    platform_admin,
):
    initial = e2e_client.get(
        "/api/maintenance/artifact-retention/settings",
        headers=platform_admin.headers,
    )
    assert initial.status_code == 200, initial.text
    assert initial.json() == {"enabled": False, "retention_days": 90}

    updated = e2e_client.put(
        "/api/maintenance/artifact-retention/settings",
        json={"enabled": True, "retention_days": 30},
        headers=platform_admin.headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == {"enabled": True, "retention_days": 30}

    accepted = e2e_client.post(
        "/api/maintenance/artifact-retention/cleanup",
        headers=platform_admin.headers,
    )
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert accepted.headers["location"] == f"/api/platform-jobs/{body['job_id']}"

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = e2e_client.get(
            f"/api/platform-jobs/{body['job_id']}",
            headers=platform_admin.headers,
        )
        assert job.status_code == 200, job.text
        if job.json()["status"] == "succeeded":
            assert job.json()["result"] == {
                "enabled": True,
                "retention_days": 30,
                "deleted_count": 0,
                "failed_count": 0,
            }
            return
        assert job.json()["status"] not in {"failed", "cancelled"}, job.text
        time.sleep(0.25)

    raise AssertionError("Artifact retention cleanup did not finish within 15 seconds")
