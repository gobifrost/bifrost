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


def test_test_runner_mounts_pyright_inputs():
    """The Dockerized quality lane needs the same API config CI uses."""
    compose = yaml.safe_load(_COMPOSE.read_text())
    volumes = compose["services"]["test-runner"].get("volumes", [])
    assert "./api/pyrightconfig.json:/app/pyrightconfig.json:ro" in volumes
    assert "./test.sh:/app/test.sh:ro" in volumes
    assert "./api/Dockerfile.dev:/app/api/Dockerfile.dev:ro" in volumes


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


def test_api_quality_script_runs_pyright_without_ci_venv_config():
    script = _find_repo_file("api/scripts/quality_api.sh").read_text()
    assert 'config.pop("venvPath", None)' in script
    assert 'config.pop("venv", None)' in script
    assert 'Path("pyrightconfig.docker.json")' in script
    assert "pyright --project pyrightconfig.docker.json --pythonpath /usr/local/bin/python" in script
    assert "ruff check ." in script
