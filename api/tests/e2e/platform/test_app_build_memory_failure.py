"""A build subprocess memory failure reaches durable status and notifications.

Cost: one PlatformJob/install. The Vite config emits a deterministic allocator
diagnostic and exits; it does not exhaust the test container's actual memory.
Unit tests cover other entry points and memory signals without extra builds.
"""

import io
import json
from uuid import uuid4
import zipfile

import pytest

from shared.app_build_errors import app_build_memory_message
from tests.e2e.conftest import poll_until


@pytest.mark.e2e
def test_app_build_oom_reaches_job_error_and_notification(e2e_client, platform_admin, request):
    headers = platform_admin.headers
    suffix = uuid4().hex[:8]
    created = e2e_client.post(
        "/api/applications", headers=headers,
        json={"name": f"Build OOM {suffix}", "slug": f"build-oom-{suffix}", "app_model": "standalone_v2"},
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    def cleanup():
        deleted = e2e_client.delete(f"/api/applications/{app_id}", headers=headers)
        assert deleted.status_code == 204, deleted.text

    request.addfinalizer(cleanup)
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("package.json", json.dumps({
            "name": "build-memory-failure", "private": True,
            "devDependencies": {"vite": "^6.2.2"},
        }))
        archive.writestr("index.html", '<div id="root"></div>')
        archive.writestr(
            "vite.config.js",
            "console.error('FATAL ERROR: JavaScript heap out of memory'); process.exit(134);\n",
        )
    upload_headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
    accepted = e2e_client.post(
        f"/api/applications/{app_id}/deploy", headers=upload_headers,
        files={"source": ("source.zip", source.getvalue(), "application/zip")},
    )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]

    def terminal_job():
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        return job if job["status"] in {"failed", "succeeded", "cancelled"} else None

    job = poll_until(terminal_job, max_wait=120, interval=0.1)
    assert job is not None
    assert job["status"] == "failed", job
    assert job["error"]["code"] == "memory_pressure"
    assert job["error"]["message"] == app_build_memory_message()
    assert job["notification_id"] is not None

    def failed_notification():
        response = e2e_client.get(f"/api/notifications/{job['notification_id']}", headers=headers)
        assert response.status_code == 200, response.text
        notification = response.json()
        return notification if notification["status"] == "failed" else None

    notification = poll_until(failed_notification, max_wait=10, interval=0.1)
    assert notification is not None
    assert notification["error"] == app_build_memory_message()
