"""Static safety contracts for the shared dev-image CI topology.

These tests deliberately inspect workflow source rather than simulating GitHub.
They protect the repository-controlled half of the invariant: the complete suite
runs for an exact merge candidate, while dev images can only be published after
that candidate reaches ``main``. The live required-check settings are audited
separately because they are GitHub repository configuration, not source code.
"""

from pathlib import Path
from typing import Any

import yaml


def _repository_root() -> Path:
    # Host tests see <repo>/api/tests; the Dockerized test runner mounts the API
    # at /app and workflow sources at /app/.github.
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".github" / "workflows" / "ci.yml").is_file():
            return candidate
    raise RuntimeError("could not locate repository workflow sources")


REPO_ROOT = _repository_root()
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CI_NOOP_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci-noop.yml"


def _load_workflow(path: Path) -> dict[str, Any]:
    # BaseLoader keeps the key ``on`` as a string instead of YAML 1.1's boolean.
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def _normalized(expression: str) -> str:
    return " ".join(expression.split())


def test_full_suite_gates_exact_merge_candidate_before_dev_image() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert {"pull_request", "merge_group", "push"} <= triggers.keys()

    full_e2e_condition = _normalized(jobs["test-e2e"]["if"])
    full_e2e_gate_condition = _normalized(jobs["test-e2e-gate"]["if"])
    for condition in (full_e2e_condition, full_e2e_gate_condition):
        assert "github.event_name == 'merge_group'" in condition
        assert "github.event_name == 'workflow_dispatch'" in condition
        assert "startsWith(github.ref, 'refs/tags/v')" in condition
        assert "pull_request" not in condition

    assert set(jobs["test-e2e-gate"]["needs"]) == {
        "test-e2e",
        "test-client-unit",
    }
    assert jobs["test-e2e-gate"]["name"] == "E2E Tests"

    for job_name in ("lint", "test-unit", "test-client-unit"):
        condition = _normalized(jobs[job_name]["if"])
        assert "github.event_name != 'push'" in condition
        assert "github.ref != 'refs/heads/main'" in condition

    build_dev = jobs["build-dev"]
    assert _normalized(build_dev["if"]) == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )
    assert "needs" not in build_dev
    assert jobs["deploy-dev"]["needs"] == ["build-dev"]


def test_pull_request_reports_required_e2e_context_without_running_full_suite() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]

    pr_gate = jobs["test-e2e-pr"]
    assert _normalized(pr_gate["if"]) == (
        "always() && github.event_name == 'pull_request'"
    )
    assert pr_gate["needs"] == ["test-client-unit"]
    assert pr_gate["name"] == "E2E Tests"

    # The ordinary PR and exact-candidate jobs share the required check name but
    # their event conditions are mutually exclusive.
    assert jobs["test-e2e-gate"]["name"] == pr_gate["name"]
    assert "pull_request" not in _normalized(jobs["test-e2e-gate"]["if"])


def test_docs_only_inverse_workflow_covers_the_same_paths_and_check_names() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    noop = _load_workflow(CI_NOOP_WORKFLOW)

    assert workflow["on"]["pull_request"]["paths-ignore"] == noop["on"][
        "pull_request"
    ]["paths"]

    required_names = {"Lint & Type Check", "Unit Tests", "E2E Tests"}
    workflow_names = {job["name"] for job in workflow["jobs"].values()}
    noop_names = {job["name"] for job in noop["jobs"].values()}
    assert required_names <= workflow_names
    assert required_names <= noop_names


def test_release_tags_still_require_the_complete_suite() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    release_prerequisites = {"test-unit", "test-e2e-gate", "lint"}

    assert set(jobs["build-api"]["needs"]) == release_prerequisites
    assert set(jobs["build-client"]["needs"]) == release_prerequisites
    assert set(jobs["create-release"]["needs"]) == {"build-api", "build-client"}
