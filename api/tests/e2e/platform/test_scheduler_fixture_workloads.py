"""Deterministic real-work proof for every central scheduler job shape."""

import json
import subprocess
import sys

import pytest


@pytest.mark.e2e
def test_scheduler_fixture_suite_executes_real_work_through_central_runner():
    completed = subprocess.run(
        [sys.executable, "-m", "src.dev.scheduler_fixtures"],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    result = json.loads(completed.stdout)

    assert result["checks"] == {
        "oauth_refreshed": True,
        "webhook_renewed": True,
        "solution_update_found": True,
        "file_index_repaired": True,
        "summary_parent_reconciled": True,
    }
    assert set(result["platform_jobs"]) == {
        "oauth",
        "webhook",
        "solution",
        "file_index",
    }
