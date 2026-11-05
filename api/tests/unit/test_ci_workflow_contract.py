"""Static safety contracts for the shared dev-image CI topology.

These tests deliberately inspect workflow source rather than simulating GitHub.
They protect the repository-controlled half of the invariant: the complete suite
runs for an exact merge candidate, while dev images can only be published after
that candidate reaches ``main``. The live required-check settings are audited
separately because they are GitHub repository configuration, not source code.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.verify_merge_candidate import REQUIRED_JOBS


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


def test_promotion_verifier_requires_every_merge_candidate_job() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    shard_job = jobs["test-e2e"]
    shards = shard_job["strategy"]["matrix"]["shard"]
    name_template = shard_job["name"]
    assert "${{ matrix.shard }}" in name_template
    assert name_template.endswith(f"/{len(shards)})")

    expected = {
        jobs[job_id]["name"]
        for job_id in (
            "lint",
            "test-unit",
            "test-client-unit",
            "test-client-smoke",
            "build-dev-api-candidate",
            "build-dev-client-candidate",
            "test-e2e-gate",
        )
    }
    expected.update(
        name_template.replace("${{ matrix.shard }}", shard) for shard in shards
    )

    assert REQUIRED_JOBS == expected


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
    assert "pull_request" not in full_e2e_condition
    assert "github.event_name == 'pull_request'" in full_e2e_gate_condition

    assert set(jobs["test-e2e-gate"]["needs"]) == {
        "test-e2e",
        "test-client-unit",
        "test-client-smoke",
        "windows-credentials",
        "build-dev-api-candidate",
        "build-dev-client-candidate",
    }
    gate_source = "\n".join(step.get("run", "") for step in jobs["test-e2e-gate"]["steps"])
    assert "needs.build-dev-api-candidate.result" in gate_source
    assert "needs.build-dev-client-candidate.result" in gate_source
    assert jobs["test-e2e-gate"]["name"] == "E2E Tests"
    assert [
        name for name, job in jobs.items() if job.get("name") == "E2E Tests"
    ] == ["test-e2e-gate"]

    for job_name in ("lint", "test-unit", "test-client-unit"):
        condition = _normalized(jobs[job_name]["if"])
        assert "github.event_name != 'push'" in condition
        assert "github.ref != 'refs/heads/main'" in condition

    build_dev = jobs["build-dev"]
    assert _normalized(build_dev["if"]) == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )
    assert "needs" not in build_dev
    verification = next(
        step
        for step in build_dev["steps"]
        if step.get("name") == "Verify exact merge candidate gate"
    )
    assert "verify_merge_candidate.py" in verification["run"]
    assert '--sha "$GITHUB_SHA"' in verification["run"]


def test_critical_browser_smoke_strengthens_only_exact_candidate_events() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    smoke = jobs["test-client-smoke"]
    condition = _normalized(smoke["if"])

    assert "github.event_name == 'merge_group'" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "startsWith(github.ref, 'refs/tags/v')" in condition
    assert "pull_request" not in condition
    assert smoke["needs"] == ["build-dev-client-candidate"]

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
    assert client["image"] == "bifrost-test-client-e2e:${BIFROST_TEST_IMAGE_TAG:-latest}"
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


def test_ci_jobs_use_command_owned_stacks() -> None:
    """Each CI test invocation owns startup, diagnostics and teardown."""
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
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        assert "./test.sh stack up" not in commands
        assert "./test.sh" in commands
        assert all(
            "BIFROST_TEST_USE_CLEAN_BOOT" not in step.get("env", {})
            for step in job["steps"]
        )


def test_dev_artifact_is_built_on_merge_candidate_and_promoted_without_rebuild() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    api_candidate = jobs["build-dev-api-candidate"]
    client_candidate = jobs["build-dev-client-candidate"]
    promotion = jobs["build-dev"]

    for candidate in (api_candidate, client_candidate):
        assert _normalized(candidate["if"]) == "github.event_name == 'merge_group'"
        assert candidate["permissions"]["packages"] == "write"
        assert candidate["permissions"]["id-token"] == "write"
        assert candidate["permissions"]["attestations"] == "write"
        assert "needs" not in candidate
    assert api_candidate["name"] == "Build API Dev Candidate"
    assert client_candidate["name"] == "Build Client Dev Candidate"

    candidate_steps = api_candidate["steps"] + client_candidate["steps"]
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
    for candidate in (api_candidate, client_candidate):
        candidate_steps = candidate["steps"]
        assert any(step.get("name", "").startswith("Sign ") for step in candidate_steps)
        assert any(step.get("name", "").startswith("Attest ") for step in candidate_steps)

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
    assert promotion["outputs"]["version"] == "${{ steps.version.outputs.version }}"


def test_ci_publication_does_not_depend_on_an_operator_cluster() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    assert "deploy-dev" not in jobs
    assert "deploy-dry-run" not in jobs
    source = CI_WORKFLOW.read_text()
    assert "DIGITALOCEAN_ACCESS_TOKEN" not in source
    assert "DO_CLUSTER_NAME" not in source
    assert "kubectl" not in source
    for job in jobs.values():
        assert "deploy-dev" not in job.get("needs", [])
        assert "deploy-dry-run" not in job.get("needs", [])


def test_pull_request_reports_required_e2e_context_without_running_full_suite() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]

    gate = jobs["test-e2e-gate"]
    assert gate["name"] == "E2E Tests"
    assert "github.event_name == 'pull_request'" in _normalized(gate["if"])
    source = "\n".join(step.get("run", "") for step in gate["steps"])
    assert 'github.event_name }}" = "pull_request"' in source
    assert "needs.test-client-unit.result" in source
    assert "Full E2E is deferred" in source


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

    for job_name in ("build-api", "build-client"):
        condition = _normalized(jobs[job_name]["if"])
        assert "always()" in condition
        assert "startsWith(github.ref, 'refs/tags/v')" in condition
        for prerequisite in release_prerequisites:
            assert f"needs.{prerequisite}.result == 'success'" in condition

    release_condition = _normalized(jobs["create-release"]["if"])
    assert "always()" in release_condition
    assert "startsWith(github.ref, 'refs/tags/v')" in release_condition
    assert "needs.build-api.result == 'success'" in release_condition
    assert "needs.build-client.result == 'success'" in release_condition


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


def test_windows_storage_runs_native_checks_and_blocks_the_shared_gate() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    windows = jobs["windows-credentials"]
    assert windows["runs-on"] == "windows-2025"
    assert windows["if"] == "github.event_name != 'push' || github.ref != 'refs/heads/main'"
    commands = "\n".join(step.get("run", "") for step in windows["steps"])
    assert "--require-hashes -r requirements-windows-credentials.lock" in commands
    assert "test_windows_credentials.py" in commands
    assert "python scripts/windows_credential_smoke.py" in commands
    source = "\n".join(step.get("run", "") for step in jobs["test-e2e-gate"]["steps"])
    # Native checks must gate even the fast PR path, before its early exit.
    assert source.index("needs.windows-credentials.result") < source.index("pull_request")
    assert 'needs.windows-credentials.result }}" != "success"' in source


SIGN_IMAGE_ACTION = REPO_ROOT / ".github" / "actions" / "sign-image" / "action.yml"


def test_all_image_publishers_use_the_verified_signer() -> None:
    consumers = []
    for path in (CI_WORKFLOW, REPO_ROOT / ".github/workflows/dev-worker-image.yml"):
        for job_id, job in _load_workflow(path)["jobs"].items():
            for step in job["steps"]:
                # Image signing shares one implementation; release-asset blob
                # signing retains its separate existing bundle contract.
                assert "cosign sign --" not in step.get("run", "")
                if step.get("uses") == "./.github/actions/sign-image":
                    consumers.append(job_id)
                    assert "@${{ steps." in step["with"]["image"]
                    assert job["permissions"]["id-token"] == "write"
    assert sorted(consumers) == sorted([
        "build-dev-api-candidate", "build-dev-client-candidate", "build-api", "build-client", "publish",
    ])
    installer = _load_workflow(SIGN_IMAGE_ACTION)["runs"]["steps"][0]
    assert installer["with"]["cosign-release"] == "v3.1.4"


@pytest.mark.parametrize("sign_status,verify_status", [(0, 0), (17, 0), (0, 23)])
def test_image_signing_requires_a_valid_signature_from_this_workflow(
    tmp_path: Path, sign_status: int, verify_status: int
) -> None:
    """Execute the real action shell: either signing or verification failure stops CI."""
    calls_file = tmp_path / "calls.jsonl"
    cosign = tmp_path / "cosign"
    cosign.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['CALLS_FILE'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(int(os.environ['SIGN_STATUS' if sys.argv[1] == 'sign' else 'VERIFY_STATUS']))\n"
    )
    cosign.chmod(0o755)
    image = "ghcr.io/gobifrost/bifrost-api@sha256:" + "a" * 64
    workflow = "gobifrost/bifrost/.github/workflows/ci.yml@refs/heads/gh-readonly-queue/main/pr-1023"
    action = _load_workflow(SIGN_IMAGE_ACTION)
    command = action["runs"]["steps"][1]["run"]
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "IMAGE": image,
             "GITHUB_WORKFLOW_REF": workflow, "CALLS_FILE": str(calls_file),
             "SIGN_STATUS": str(sign_status), "VERIFY_STATUS": str(verify_status)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == (sign_status or verify_status), result.stderr
    calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
    expected = [["sign", "--yes", image]]
    if sign_status == 0:
        expected.append([
            "verify", "--certificate-identity", f"https://github.com/{workflow}",
            "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com", image,
        ])
    assert calls == expected
