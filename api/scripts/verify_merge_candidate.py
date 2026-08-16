#!/usr/bin/env python3
"""Fail closed unless an exact SHA passed Bifrost's merge-candidate gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


REQUIRED_JOBS = {
    "Lint & Type Check",
    "Unit Tests",
    "Client Unit Tests",
    "E2E Tests (shard 1/2)",
    "E2E Tests (shard 2/2)",
    "Critical Browser Smoke",
    "Build Dev Candidate",
    "E2E Tests",
}
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"


def _github_json(url: str) -> dict[str, Any]:
    if not url.startswith("https://api.github.com/"):
        raise ValueError(f"refusing non-GitHub API URL: {url}")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "bifrost-merge-candidate-verifier",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned a non-object response")
    return payload


def verify_merge_candidate(
    repository: str,
    sha: str,
    fetch_json: Callable[[str], dict[str, Any]],
) -> list[str]:
    """Return invariant violations for the merge-group run at ``sha``."""
    query = urlencode(
        {
            "event": "merge_group",
            "head_sha": sha,
            "per_page": 100,
        }
    )
    runs_url = (
        "https://api.github.com/repos/"
        f"{quote(repository, safe='/')}/actions/runs?{query}"
    )
    runs = fetch_json(runs_url).get("workflow_runs", [])
    candidates = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and run.get("event") == "merge_group"
        and run.get("path") == CI_WORKFLOW_PATH
    ]
    if len(candidates) != 1:
        return [
            f"expected exactly one {CI_WORKFLOW_PATH} merge_group run for {sha}, "
            f"found {len(candidates)}"
        ]

    run = candidates[0]
    jobs_url = run.get("jobs_url")
    if not isinstance(jobs_url, str):
        return ["merge-group run did not provide a jobs URL"]
    separator = "&" if "?" in jobs_url else "?"
    jobs = fetch_json(f"{jobs_url}{separator}per_page=100").get("jobs", [])

    conclusions: dict[str, list[str | None]] = {}
    for job in jobs:
        name = job.get("name")
        if isinstance(name, str) and name in REQUIRED_JOBS:
            conclusions.setdefault(name, []).append(job.get("conclusion"))

    violations: list[str] = []
    for name in sorted(REQUIRED_JOBS):
        observed = conclusions.get(name, [])
        if observed != ["success"]:
            violations.append(
                f"required exact-candidate job {name!r} must appear once and pass; "
                f"observed {observed or 'missing'}"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the exact SHA passed every merge-candidate gate job."
    )
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--sha", required=True, help="Exact candidate commit SHA")
    args = parser.parse_args(argv)

    violations = verify_merge_candidate(
        args.repository,
        args.sha,
        _github_json,
    )
    if not violations:
        print(f"Exact merge candidate {args.sha} passed every required gate job.")
        return 0

    print("Refusing dev-image promotion:", file=sys.stderr)
    for violation in violations:
        print(f"  - {violation}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
