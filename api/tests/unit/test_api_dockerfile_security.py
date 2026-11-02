"""Security contracts for the API container images."""

from pathlib import Path


def _dockerfile_path(name: str) -> Path:
    """Locate the Compose-mounted Dockerfile or its checkout counterpart."""
    mounted = Path("/app/api") / name
    if mounted.is_file():
        return mounted
    return Path(__file__).resolve().parents[3] / "api" / name


def _runtime_stage_lines(path: Path) -> list[str]:
    lines = path.read_text().splitlines()
    runtime_start = max(
        index for index, line in enumerate(lines) if line.startswith("FROM ")
    )
    return lines[runtime_start:]


def test_api_images_default_to_unprivileged_runtime_user():
    for relative_path in ("api/Dockerfile", "api/Dockerfile.dev"):
        runtime_lines = _runtime_stage_lines(_dockerfile_path(Path(relative_path).name))
        user_lines = [
            line.strip() for line in runtime_lines if line.startswith("USER ")
        ]

        assert user_lines == ["USER bifrost"], relative_path
        assert runtime_lines.index("USER bifrost") < runtime_lines.index(
            'ENTRYPOINT ["/entrypoint.sh"]'
        )
