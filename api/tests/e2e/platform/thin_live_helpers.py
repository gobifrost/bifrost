"""Observation helper for the explicit thin-worker live-handler pass."""

import json
from datetime import datetime
from pathlib import Path

from tests.e2e.conftest import poll_until


def wait_for_job(client, headers, job_id, expected_type, max_wait=120):
    def terminal():
        response = client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        return job if job["status"] in {"succeeded", "failed", "cancelled", "requires_action"} else None

    job = poll_until(terminal, max_wait=max_wait, interval=0.1)
    assert job is not None, f"{expected_type} job {job_id} did not finish"
    assert job["job_type"] == expected_type, job
    assert job["status"] == "succeeded", job
    assert job["started_at"] and job["completed_at"], job
    started = datetime.fromisoformat(job["started_at"])
    finished = datetime.fromisoformat(job["completed_at"])
    print("JOB_LIVE_EVIDENCE " + json.dumps({
        "job_type": expected_type, "id": str(job_id), "status": job["status"],
        "started_at": job["started_at"], "completed_at": job["completed_at"],
        "handler_seconds": (finished - started).total_seconds(), "attempt": job["attempt"],
        "memory_start_bytes": job["memory_start_bytes"],
        "memory_peak_bytes": job["memory_peak_bytes"],
    }, sort_keys=True))
    return job


def app_build_package_files() -> dict[str, bytes]:
    """Use a frozen build graph so native packages cannot race npm publication.

    Refresh the fixture's lockfile deliberately with the API Node/npm toolchain.
    Both the successful SDK build and allocator-failure journey use this graph.
    """
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "app_build"
    return {name: (fixture / name).read_bytes() for name in ("package.json", "package-lock.json")}
