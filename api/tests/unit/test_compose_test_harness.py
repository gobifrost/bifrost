"""Regression guard for the e2e test harness (docker-compose.test.yml).

Protects against re-introducing a class of bug where the `api` service — which
runs as **root** and whose entrypoint does ``chown -R bifrost:bifrost
/tmp/bifrost`` — bind-mounts the host LOG_DIR root over its ``/tmp/bifrost``.

When that happens the api container recursively chowns the **host** LOG_DIR
(and the files the harness writes there: ``test-runner.log``,
``test-results.xml``, the per-service ``*.log``) to uid 1000. On a CI runner
whose uid is NOT 1000, the host-side ``tee "$LOG_DIR/test-runner.log"`` in
``test.sh::run_pytest`` then fails with EPERM, which makes the e2e step exit 1
**even though every test passed** (`728 passed … ##[error] exit code 1`). It
hid from single-process local runs because a uid-1000 dev host is immune to the
chown (chowning to its own uid is a no-op).

The api may share ONLY the fixture subdir the install/preview-repo e2e tests
stage file:// git repos in; that subdir's files are created by the uid-1000
test-runner, so chowning them to 1000 is harmless and never touches LOG_DIR.
"""
from __future__ import annotations

import pathlib
import re

import yaml


def _find_compose() -> pathlib.Path:
    """Locate docker-compose.test.yml in-container (mounted at /app) or on host.

    The test-runner container mounts the file read-only at
    ``/app/docker-compose.test.yml`` (the repo root itself is not mounted), so
    prefer that; fall back to the repo-root path for host-side runs.
    """
    candidates = [
        pathlib.Path("/app/docker-compose.test.yml"),
        pathlib.Path(__file__).resolve().parents[3] / "docker-compose.test.yml",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "docker-compose.test.yml not found; if running in the test-runner "
        "container, ensure it is bind-mounted at /app/docker-compose.test.yml"
    )


_COMPOSE = _find_compose()


def _find_repo_file(path: str) -> pathlib.Path:
    """Locate a repo-root file in-container or on host."""
    candidates = [
        pathlib.Path("/app") / path,
        pathlib.Path(__file__).resolve().parents[3] / path,
    ]
    if path.startswith("api/"):
        candidates.insert(1, pathlib.Path("/app") / path.removeprefix("api/"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"{path} not found in test harness mounts")


def _api_bind_targets() -> list[str]:
    compose = yaml.safe_load(_COMPOSE.read_text())
    volumes = compose["services"]["api"].get("volumes", [])
    targets = []
    for v in volumes:
        # short syntax "source:target[:mode]". Source may contain a
        # ``${VAR:-default}`` expansion whose ``:-`` would confuse a naive
        # split, so collapse expansions to a placeholder before splitting.
        if isinstance(v, str):
            collapsed = re.sub(r"\$\{[^}]*\}", "VAR", v)
            parts = collapsed.split(":")
            if len(parts) >= 2:
                targets.append(parts[1])
    return targets


def test_api_does_not_bind_log_dir_root_over_tmp_bifrost():
    """The root-running api must never own the host LOG_DIR root.

    Binding ``${LOG_DIR}:/tmp/bifrost`` (mount target exactly ``/tmp/bifrost``)
    is the forbidden shape — its entrypoint chown clobbers the harness's own
    result/log files. See module docstring.
    """
    assert "/tmp/bifrost" not in _api_bind_targets(), (
        "api service bind-mounts the LOG_DIR root over /tmp/bifrost; its "
        "root entrypoint `chown -R /tmp/bifrost` will clobber the host "
        "LOG_DIR and break the e2e step's `tee $LOG_DIR/test-runner.log` "
        "with EPERM on non-uid-1000 CI runners (728 passed → exit 1). "
        "Bind only the solution-repo-fixtures subdir instead."
    )


def test_api_shares_fixture_subdir_for_install_from_repo():
    """The api must still see host-staged file:// fixture repos.

    test_solution_install_from_repo.py stages git repos under
    /tmp/bifrost/solution-repo-fixtures (uid-1000 test-runner) and the api
    clones them server-side — so that exact subdir must be bind-mounted in.
    """
    assert "/tmp/bifrost/solution-repo-fixtures" in _api_bind_targets(), (
        "api no longer shares /tmp/bifrost/solution-repo-fixtures; "
        "install/preview-repo e2e tests can't clone host-staged fixtures."
    )


def test_stack_up_reconciles_running_containers_with_current_compose_config():
    """A healthy long-lived stack may still have stale mounts or environment."""
    script = _find_repo_file("test.sh").read_text()
    already_running = script.split(
        'if stack_is_up "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; then', 1
    )[1].split('echo "Stack already up."', 1)[0]

    assert (
        'docker compose -f "$COMPOSE_FILE" --profile e2e up -d --no-build'
        in already_running
    ), "stack up must reconcile healthy containers before returning"
    assert 'expected_fixture_source="$LOG_DIR/solution-repo-fixtures"' in already_running
    assert "--force-recreate api" in already_running


def test_test_runner_mounts_pyright_inputs():
    """The Dockerized quality lane needs the same API config CI uses."""
    compose = yaml.safe_load(_COMPOSE.read_text())
    volumes = compose["services"]["test-runner"].get("volumes", [])
    assert "./api/pyrightconfig.json:/app/pyrightconfig.json:ro" in volumes
    assert "./test.sh:/app/test.sh:ro" in volumes
    assert "./api/Dockerfile.dev:/app/api/Dockerfile.dev:ro" in volumes


def test_hardened_test_runner_writes_coverage_to_results_mount():
    """pytest-cov data must be writable by the uid-1000 test runner."""
    compose = yaml.safe_load(_COMPOSE.read_text())
    runner = compose["services"]["test-runner"]

    assert runner["environment"]["COVERAGE_FILE"] == "/tmp/bifrost/.coverage"
    assert all("/coverage" not in str(volume) for volume in runner["volumes"])
    assert "test-coverage" not in compose.get("volumes", {})


def test_dev_image_installs_pyright_from_hash_pinned_lock():
    """Local Docker type-checks should not depend on host pyright installs."""
    dockerfile = _find_repo_file("api/Dockerfile.dev").read_text()
    assert "requirements-pyright.lock" in dockerfile
    assert "pip install --no-cache-dir --require-hashes -r requirements-pyright.lock" in dockerfile


def test_test_sh_advertises_dockerized_api_quality_lane():
    script = _find_repo_file("test.sh").read_text()
    assert "./test.sh quality api" in script
    assert "cmd_quality" in script
    assert "sh /app/scripts/quality_api.sh" in script


def test_pre_pr_gate_covers_every_locally_reproducible_merge_gate():
    """A PR must not be the first place broad, reproducible checks run."""
    script = _find_repo_file("test.sh").read_text()
    pre_pr = script.split("cmd_pre_pr() {", 1)[1].split("\n}", 1)[0]

    for required_call in (
        "repository_ci_checks",
        "client_ci_checks",
        "quality_api",
        "cmd_unit",
        "cmd_e2e",
        "client_smoke",
        "build_local_api_candidate",
    ):
        assert required_call in pre_pr

    assert "git status --porcelain --untracked-files=all" in pre_pr
    assert "git fetch --quiet origin main" in pre_pr
    assert "git merge-base --is-ancestor origin/main HEAD" in pre_pr
    assert 'git rev-parse HEAD' in pre_pr

    repository_checks = script.split("repository_ci_checks() {", 1)[1].split(
        "\n}", 1
    )[0]
    assert "check_github_action_pins.py --verify-versions" in repository_checks
    assert "scripts/sync-codex-skills.sh" in repository_checks
    assert "git diff --quiet -- plugins/bifrost/skills .codex/skills" in repository_checks

    api_candidate = script.split("build_local_api_candidate() {", 1)[1].split(
        "\n}", 1
    )[0]
    assert "--file api/Dockerfile" in api_candidate
    assert "from src.main import app" in api_candidate


def test_pre_pr_client_checks_use_the_ci_node_and_production_build():
    compose = yaml.safe_load(_COMPOSE.read_text())
    runner = compose["services"]["client-check-runner"]

    assert runner["build"]["dockerfile"] == "Dockerfile"
    assert runner["build"]["target"] == "builder"
    assert runner["command"] == [
        "sh",
        "-c",
        "npm run tsc && npm run lint && npm test",
    ]

    dockerfile = _find_repo_file("client/Dockerfile").read_text()
    assert "FROM --platform=$BUILDPLATFORM node:26-slim@" in dockerfile


def test_api_quality_script_runs_pyright_without_ci_venv_config():
    script = _find_repo_file("api/scripts/quality_api.sh").read_text()
    assert 'config.pop("venvPath", None)' in script
    assert 'config.pop("venv", None)' in script
    assert 'Path("pyrightconfig.docker.json")' in script
    assert "pyright --project pyrightconfig.docker.json --pythonpath /usr/local/bin/python" in script
    assert "ruff check ." in script


def test_state_reset_reloads_the_mounted_scheduler_fixture_server():
    """A fixture edit must not leave a stale long-lived HTTP process running."""
    script = _find_repo_file("test.sh").read_text()
    reset_state = script.split("reset_state() {", 1)[1].split("\n}", 1)[0]
    stop_block = reset_state.split("docker compose", 1)[1].split("2>/dev/null", 1)[0]
    assert "scheduler-fixtures" in stop_block


def test_clean_boot_optimization_is_one_shot_and_shared_by_test_runners():
    """A CI clean boot may skip only the first otherwise-redundant reset."""
    script = _find_repo_file("test.sh").read_text()
    prepare = script.split("prepare_test_state() {", 1)[1].split("\n}", 1)[0]
    pytest_runner = script.split("run_pytest() {", 1)[1].split("\n}", 1)[0]
    browser_runner = script.split("client_e2e() {", 1)[1].split("\n}", 1)[0]

    assert "BIFROST_TEST_USE_CLEAN_BOOT" in prepare
    assert ".clean-boot-consumed" in prepare
    assert "touch \"$boot_marker\"" in prepare
    assert "reset_state" in prepare
    assert "prepare_test_state" in pytest_runner
    assert "prepare_test_state" in browser_runner


def test_pytest_runner_prevents_concurrent_or_orphaned_stack_mutation():
    """A detached pytest container must block a second run on the same stack."""
    script = _find_repo_file("test.sh").read_text()
    pytest_runner = script.split("run_pytest() {", 1)[1].split("\n}", 1)[0]

    assert 'flock -n "$runner_lock_fd"' in pytest_runner
    assert 'runner_name="${COMPOSE_PROJECT_NAME}-pytest-runner"' in pytest_runner
    assert 'docker container inspect "$runner_name"' in pytest_runner
    assert '--name "$runner_name" test-runner' in pytest_runner
    assert "trap cleanup_pytest_runner INT TERM" in pytest_runner
    assert 'docker rm -f "$runner_name"' in pytest_runner


def test_playwright_uses_the_production_client_image():
    """Browser gates must cover compiled assets and nginx, not Vite transforms."""
    compose = yaml.safe_load(_COMPOSE.read_text())
    client = compose["services"]["client"]
    assert client["image"] == "bifrost-test-client-e2e:latest"
    assert client["build"]["dockerfile"] == "Dockerfile"
    assert client["build"]["target"] == "production"
    assert "volumes" not in client


def test_product_and_docs_browser_commands_start_the_client():
    """Moving client startup out of stack_up must not break docs capture."""
    script = _find_repo_file("test.sh").read_text()
    product = script.split("client_e2e() {", 1)[1].split("\n}", 1)[0]
    docs = script.split("client_docs() {", 1)[1].split("\n}", 1)[0]
    assert "start_test_client" in product
    assert "start_test_client" in docs


def test_embed_csp_is_not_lost_to_an_internal_index_redirect():
    """The final embedded SPA document must retain its computed frame policy."""
    nginx = _find_repo_file("client/nginx.conf").read_text()
    public_location = nginx.split(
        "location ~ ^/embedded/forms/public/", 1
    )[1].split("location = /_public_form_frame_policy", 1)[0]
    hmac_location = nginx.split(
        'location ~ "^/embedded/forms/hmac/', 1
    )[1].split("location = /_hmac_form_frame_policy", 1)[0]

    for location in (public_location, hmac_location):
        assert "try_files /index.html =404;" in location
        assert "try_files $uri /index.html;" not in location
        assert "add_header Content-Security-Policy" in location


def test_production_client_assets_are_world_readable():
    """Nginx workers must be able to serve assets regardless of source modes."""
    dockerfile = _find_repo_file("client/Dockerfile").read_text()
    assert "find dist -type d -exec chmod 755 {} +" in dockerfile
    assert "find dist -type f -exec chmod 644 {} +" in dockerfile
