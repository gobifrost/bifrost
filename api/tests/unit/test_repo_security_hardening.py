"""Static checks for repo and dev-surface hardening invariants."""

from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github").is_dir() and (parent / "debug.sh").exists():
            return parent
    raise RuntimeError("Could not locate repository root")


REPO_ROOT = _repo_root()
REQUIRED_CI_CHECK_NAMES = {"Lint & Type Check", "Unit Tests", "E2E Tests"}


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _load_yaml(relative_path: str) -> dict[str, Any]:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_pull_request_ci_does_not_use_noop_path_ignore() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    pull_request = ci[True]["pull_request"]

    assert "paths-ignore" not in pull_request


def test_docs_noop_workflow_cannot_spoof_required_ci_check_names() -> None:
    noop = _load_yaml(".github/workflows/ci-noop.yml")
    job_names = {job.get("name", job_id) for job_id, job in noop["jobs"].items()}

    assert noop["name"] != "CI"
    assert job_names.isdisjoint(REQUIRED_CI_CHECK_NAMES)


def test_dependabot_lock_validation_does_not_commit_to_pr_branch() -> None:
    workflow = _load_yaml(".github/workflows/dependabot-lockfile-regen.yml")
    text = _read(".github/workflows/dependabot-lockfile-regen.yml")

    assert workflow["permissions"] == {"contents": "read"}
    assert "git push" not in text
    assert "git commit" not in text
    assert "contents: write" not in text


def test_dependabot_auto_merge_requires_python_lockfile_updates() -> None:
    text = _read(".github/workflows/dependabot-auto-merge.yml")

    assert "Require reviewed Python lockfile updates" in text
    assert "steps.metadata.outputs.package-ecosystem == 'pip'" in text
    assert "requirements.lock" in text
    assert "needs-review" in text


def test_debug_port_mode_binds_client_to_loopback_only() -> None:
    port_overlay = _load_yaml("docker-compose.debug.port.yml")
    ports = port_overlay["services"]["client"]["ports"]

    assert ports == ["127.0.0.1:${DEBUG_CLIENT_PORT}:80"]


def test_debug_stack_has_no_checked_in_default_admin_password() -> None:
    compose = _load_yaml("docker-compose.debug.yml")
    debug_env = _read(".env.debug")
    debug_script = _read("debug.sh")
    debug_skill = _read(".claude/skills/bifrost-debug/SKILL.md")

    api_env = compose["services"]["api"]["environment"]
    assert (
        api_env["BIFROST_DEFAULT_USER_PASSWORD"] == "${BIFROST_DEFAULT_USER_PASSWORD:-}"
    )
    assert "BIFROST_DEFAULT_USER_PASSWORD=password" not in debug_env
    assert "password: password" not in debug_script
    assert "dev@gobifrost.com" not in debug_skill
    assert "--password password" not in debug_skill


def test_debug_env_loader_does_not_source_env_files() -> None:
    debug_script = _read("debug.sh")

    assert 'source "$SCRIPT_DIR/scripts/lib/test_helpers.sh"' in debug_script
    assert 'source "$SCRIPT_DIR/.env"' not in debug_script
    assert 'source "$SCRIPT_DIR/.env.debug"' not in debug_script
    assert 'source "$HOME/.config/bifrost/debug.env"' not in debug_script


def test_claude_hook_shell_quotes_exported_env_values() -> None:
    hook = _read(".claude/hooks/bifrost-detect.sh")

    assert "printf 'export %s=%q\\n'" in hook
    assert 'echo "export BIFROST_DEV_URL=\\"$BIFROST_DEV_URL\\""' not in hook


def test_claude_skills_avoid_unquoted_url_and_body_shell_patterns() -> None:
    setup = _read(".claude/skills/bifrost-setup/SKILL.md")
    issues = _read(".claude/skills/bifrost-issues/SKILL.md")

    assert "$BIFROST_PIP_CMD {url}/api/cli/download" not in setup
    assert "bifrost login --url {url}" not in setup
    assert "claude mcp add --transport http bifrost {url}/mcp" not in setup
    assert "curl {url}/api/cli/download" not in setup
    assert '--body "$(cat <<' not in issues
    assert 'gh issue list --search "<2-3 key terms>"' not in issues
