from __future__ import annotations

from typing import Any

from scripts.verify_merge_candidate import (
    CI_WORKFLOW_PATH,
    REQUIRED_JOBS,
    verify_merge_candidate,
)


SHA = "a" * 40


def _fetcher(
    conclusions: dict[str, str],
):
    def fetch(url: str) -> dict[str, Any]:
        if url.endswith("/jobs?per_page=100"):
            return {
                "jobs": [
                    {"name": name, "conclusion": conclusion}
                    for name, conclusion in conclusions.items()
                ]
            }
        return {
            "workflow_runs": [
                {
                    "head_sha": SHA,
                    "event": "merge_group",
                    "path": CI_WORKFLOW_PATH,
                    "jobs_url": "https://api.github.com/runs/1/jobs",
                }
            ]
        }

    return fetch


def test_accepts_exact_candidate_only_when_every_gate_job_passed() -> None:
    conclusions = {name: "success" for name in REQUIRED_JOBS}

    assert verify_merge_candidate("gobifrost/bifrost", SHA, _fetcher(conclusions)) == []


def test_rejects_failed_or_missing_gate_jobs() -> None:
    conclusions = {name: "success" for name in REQUIRED_JOBS}
    conclusions["Critical Browser Smoke"] = "failure"
    del conclusions["E2E Tests (shard 2/2)"]

    violations = verify_merge_candidate(
        "gobifrost/bifrost", SHA, _fetcher(conclusions)
    )

    assert any("Critical Browser Smoke" in violation for violation in violations)
    assert any("E2E Tests (shard 2/2)" in violation for violation in violations)


def test_rejects_ambiguous_duplicate_required_context() -> None:
    conclusions = {name: "success" for name in REQUIRED_JOBS}
    fetch = _fetcher(conclusions)

    def with_duplicate(url: str) -> dict[str, Any]:
        payload = fetch(url)
        if "jobs?" in url:
            payload["jobs"].append(
                {"name": "E2E Tests", "conclusion": "skipped"}
            )
        return payload

    violations = verify_merge_candidate("gobifrost/bifrost", SHA, with_duplicate)

    assert any("E2E Tests" in violation and "twice" not in violation for violation in violations)
