"""Security contracts for the API container images."""

import re
from pathlib import Path

import pytest


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


@pytest.mark.parametrize("filename", ["Dockerfile", "Dockerfile.dev"])
def test_package_copy_matches_python_image_minor(filename):
    """A runtime upgrade must also copy the installed packages for that interpreter."""
    text = (_api_dir() / filename).read_text()
    versions = re.findall(r"^FROM python:(\d+\.\d+)\.", text, re.MULTILINE)
    assert len(versions) == 2
    assert versions[0] == versions[1]
    paths = re.findall(
        r"^COPY --from=builder (/usr/local/lib/python[^ ]+/site-packages) "
        r"(/usr/local/lib/python[^ ]+/site-packages)$",
        text,
        re.MULTILINE,
    )
    expected = f"/usr/local/lib/python{versions[0]}/site-packages"
    assert paths == [(expected, expected)]
