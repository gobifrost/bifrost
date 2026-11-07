"""The shipped examples return correlated payloads without provider writes."""

from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize("function,event,action,payload,expected", [
    ("github_pr_triage", "pull_request", "opened", {"pull_request": {"number": 42, "title": "Fix", "html_url": "https://github.com/fixture/repo/pull/42"}}, {"summary": "PR #42: Fix"}),
    ("github_failed_check", "check_run", "completed", {"check_run": {"name": "Unit", "conclusion": "failure", "html_url": "https://github.com/fixture/repo/actions/runs/1"}}, {"notify": True, "summary": "Unit: failure"}),
    ("github_release_notice", "release", "published", {"release": {"tag_name": "v1.0", "html_url": "https://github.com/fixture/repo/releases/v1.0"}}, {"summary": "Release v1.0"}),
])
def test_read_only_examples_preserve_delivery_and_repository_correlation(function, event, action, payload, expected):
    example = runpy.run_path(str(Path("/app/examples/github-app/workflows/github_events.py")))[function]
    result = example({"event": event, "action": action, "payload": payload,
                      "delivery_id": "stable-id", "repository_id": 42})
    assert result["delivery_id"] == "stable-id"
    assert result["repository_id"] == 42
    assert all(result[key] == value for key, value in expected.items())
