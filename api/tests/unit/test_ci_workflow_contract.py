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
NIGHTLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly.yml"
PLAYWRIGHT_CONFIG = REPO_ROOT / "client" / "playwright.config.ts"
TEST_SCRIPT = REPO_ROOT / "test.sh"
TEST_COMPOSE = REPO_ROOT / "docker-compose.test.yml"


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
        "test-client-smoke",
        "build-dev-candidate",
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


def test_critical_browser_smoke_strengthens_only_exact_candidate_events() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    smoke = jobs["test-client-smoke"]
    condition = _normalized(smoke["if"])

    assert "github.event_name == 'merge_group'" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "startsWith(github.ref, 'refs/tags/v')" in condition
    assert "pull_request" not in condition
    assert smoke["needs"] == ["build-dev-candidate"]

    run_steps = [step.get("run", "") for step in smoke["steps"]]
    assert any("./test.sh client smoke" in step for step in run_steps)

    client_build = next(
        step for step in smoke["steps"]
        if step.get("name") == "Build production client under test (cached)"
    )
    assert client_build["with"]["file"] == "./client/Dockerfile"
    assert client_build["with"]["target"] == "production"
    assert client_build["with"]["tags"] == "bifrost-test-client-e2e:latest"

    compose = yaml.safe_load(TEST_COMPOSE.read_text())
    client = compose["services"]["client"]
    assert client["image"] == "bifrost-test-client-e2e:latest"
    assert client["build"]["dockerfile"] == "Dockerfile"
    assert client["build"]["target"] == "production"
    assert "volumes" not in client

    playwright_config = PLAYWRIGHT_CONFIG.read_text()
    assert "retries: 0" in playwright_config
    assert "retain-on-failure" in playwright_config
    assert "on-first-retry" not in playwright_config

    smoke_specs = {
        "setup/global.setup.ts",
        "auth.unauth.spec.ts",
        "forms-public.unauth.spec.ts",
        "apps-preview.admin.spec.ts",
    }
    for relative_path in smoke_specs:
        source = (REPO_ROOT / "client" / "e2e" / relative_path).read_text()
        assert 'tag: "@smoke"' in source, relative_path


def test_fresh_ci_jobs_consume_clean_boot_state_once() -> None:
    """Fresh hosted jobs must opt into the one-shot redundant-reset bypass."""
    ci_jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    nightly_jobs = _load_workflow(NIGHTLY_WORKFLOW)["jobs"]
    selected = [
        ci_jobs["test-unit"],
        ci_jobs["test-e2e"],
        ci_jobs["test-client-smoke"],
        nightly_jobs["product-browser"],
        nightly_jobs["slow-unit-contracts"],
        nightly_jobs["backend-coverage"],
    ]

    for job in selected:
        run_step = next(
            step
            for step in job["steps"]
            if "./test.sh stack up" in step.get("run", "")
        )
        assert run_step["env"]["BIFROST_TEST_USE_CLEAN_BOOT"] == "1"
        commands = run_step["run"]
        assert commands.index("./test.sh stack up") < commands.rindex("./test.sh")


def test_dev_artifact_is_built_on_merge_candidate_and_promoted_without_rebuild() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    candidate = jobs["build-dev-candidate"]
    promotion = jobs["build-dev"]

    assert _normalized(candidate["if"]) == "github.event_name == 'merge_group'"
    assert candidate["name"] == "Build Dev Candidate"
    assert candidate["permissions"]["packages"] == "write"

    candidate_steps = candidate["steps"]
    candidate_builds = [
        step for step in candidate_steps
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(candidate_builds) == 2
    assert all(step["with"]["push"] == "true" for step in candidate_builds)
    assert {
        step["with"]["tags"] for step in candidate_builds
    } == {
        "ghcr.io/${{ env.API_IMAGE }}:candidate-${{ github.sha }}",
        "ghcr.io/${{ env.CLIENT_IMAGE }}:candidate-${{ github.sha }}",
    }
    candidate_source = "\n".join(
        step.get("run", "") for step in candidate_steps
    )
    assert "docker run --rm" in candidate_source
    assert "from src.main import app" in candidate_source
    assert "get_version() == os.environ['EXPECTED_VERSION']" in candidate_source

    assert promotion["name"] == "Promote Dev Images"
    assert not any(
        str(step.get("uses", "")).startswith("docker/build-push-action@")
        for step in promotion["steps"]
    )
    promotion_source = "\n".join(
        step.get("run", "") for step in promotion["steps"]
    )
    assert "candidate-${GITHUB_SHA}" in promotion_source
    assert "docker buildx imagetools create" in promotion_source
    assert "Digest mismatch" in promotion_source
    assert "fallback" not in promotion_source.lower()

    deploy = jobs["deploy-dev"]
    assert deploy["needs"] == ["build-dev"]
    deploy_source = "\n".join(step.get("run", "") for step in deploy["steps"])
    assert "http://127.0.0.1:18000/health/ready" in deploy_source
    assert "http://127.0.0.1:18080/" in deploy_source


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


def test_nightly_owns_full_browser_slow_and_clean_build_discovery() -> None:
    workflow = _load_workflow(NIGHTLY_WORKFLOW)
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert {"schedule", "workflow_dispatch"} <= triggers.keys()
    assert set(jobs) == {
        "product-browser",
        "slow-unit-contracts",
        "backend-coverage",
        "clean-production-build",
    }

    browser_steps = [
        step.get("run", "") for step in jobs["product-browser"]["steps"]
    ]
    assert any("./test.sh client nightly" in step for step in browser_steps)
    assert all("client docs" not in step for step in browser_steps)

    slow_steps = [
        step.get("run", "") for step in jobs["slow-unit-contracts"]["steps"]
    ]
    assert any("-m slow" in step for step in slow_steps)

    # Coverage is deliberately truthful and off the merge critical path. The
    # former PR upload pointed at a file pytest never generated and Codecov
    # reported success after finding zero reports.
    ci_jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    assert all(
        "codecov/codecov-action" not in step.get("uses", "")
        for step in ci_jobs["test-unit"]["steps"]
    )
    coverage_steps = jobs["backend-coverage"]["steps"]
    measure = next(
        step for step in coverage_steps if step.get("name") == "Measure backend unit coverage"
    )
    assert "--cov=src" in measure["run"]
    assert "--cov-report=xml:/tmp/bifrost/coverage.xml" in measure["run"]
    upload = next(
        step for step in coverage_steps if step.get("name") == "Upload measured coverage to Codecov"
    )
    assert upload["with"]["files"] == "./coverage.xml"
    assert upload["with"]["disable_search"] == "true"
    assert upload["with"]["fail_ci_if_error"] == "true"

    build_steps = jobs["clean-production-build"]["steps"]
    docker_builds = [step for step in build_steps if "with" in step]
    assert len(docker_builds) == 2
    assert all(step["with"]["no-cache"] == "true" for step in docker_builds)

    test_script = TEST_SCRIPT.read_text()
    assert "client_e2e --grep @smoke" in test_script
    assert "--project=docs" not in test_script.split("client_nightly()", 1)[1].split("}", 1)[0]
