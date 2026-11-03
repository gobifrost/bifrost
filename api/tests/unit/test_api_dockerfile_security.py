"""Security contracts for the API container images."""

from pathlib import Path


def _api_dir() -> Path:
    """Locate the Compose-mounted API directory or its checkout counterpart."""
    mounted = Path("/app/api")
    return mounted if mounted.is_dir() else Path(__file__).resolve().parents[3] / "api"


def _entrypoint_path() -> Path:
    """The image copies api/entrypoint.sh to /entrypoint.sh at build time."""
    installed = Path("/entrypoint.sh")
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[3] / "api" / "entrypoint.sh"


def _runtime_stage_lines(path: Path) -> list[str]:
    lines = path.read_text().splitlines()
    runtime_start = max(
        index for index, line in enumerate(lines) if line.startswith("FROM ")
    )
    return lines[runtime_start:]


def test_dev_image_defaults_to_unprivileged_runtime_user():
    runtime_lines = _runtime_stage_lines(_api_dir() / "Dockerfile.dev")
    user_lines = [line.strip() for line in runtime_lines if line.startswith("USER ")]

    assert user_lines == ["USER bifrost"]
    assert runtime_lines.index("USER bifrost") < runtime_lines.index(
        'ENTRYPOINT ["/entrypoint.sh"]'
    )


def test_production_image_drops_privileges_before_serving():
    """Production either starts unprivileged or drops via the entrypoint."""
    runtime_lines = _runtime_stage_lines(_api_dir() / "Dockerfile")
    starts_unprivileged = any(line.strip() == "USER bifrost" for line in runtime_lines)
    entrypoint = _entrypoint_path().read_text()

    assert starts_unprivileged or "gosu bifrost" in entrypoint
