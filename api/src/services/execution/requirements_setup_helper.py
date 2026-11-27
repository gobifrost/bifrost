"""Subprocess entry point for worker requirements setup."""

from __future__ import annotations

import json
import logging
import subprocess
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from shared.subprocess_env import package_tool_env
from src.services.execution.requirements_setup_result import RequirementsInstallResult

logger = logging.getLogger(__name__)


def _get_installed_packages() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
            env=package_tool_env(),
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"Failed to get installed packages: {e}")
    return []


def _requirement_name(line: str) -> str:
    return canonicalize_name(Requirement(line).name)


def _update_requirements_status_from_content(
    result: RequirementsInstallResult, content: str | None
) -> None:
    from src.services.execution.simple_worker import _parse_requirement_lines

    if not content:
        result.requirements_total = 0
        result.requirements_installed = 0
        return

    try:
        lines = _parse_requirement_lines(content)
    except ValueError:
        result.requirements_total = result.requirements_installed = 0
        return  # install_requirements already reports the malformed options.
    required: set[str] = set()
    for line in lines:
        try:
            required.add(_requirement_name(line))
        except InvalidRequirement:
            # Source directives/URLs are successful install targets, not a
            # distribution identity we can compare with pip list.
            continue
    result.requirements_total = len(required)

    installed = {canonicalize_name(p["name"]) for p in _get_installed_packages()}
    result.requirements_installed = len(required & installed)

    missing = required - installed
    if missing:
        logger.warning(f"[pool] Missing required packages: {', '.join(sorted(missing))}")
    else:
        logger.info(f"[pool] All {result.requirements_total} required packages installed")


def run_requirements_setup_for_content(content: str | None) -> RequirementsInstallResult:
    """Install and inventory explicitly supplied content without backend IO."""
    from src.services.execution.simple_worker import install_requirements_from_content

    result = install_requirements_from_content(content)
    try:
        _update_requirements_status_from_content(result, content)
    except Exception as e:  # noqa: BLE001 - status counts are heartbeat-only
        logger.warning(f"Failed to check requirements status: {e}")
    return result


def run_requirements_setup() -> RequirementsInstallResult:
    """Run the existing cache-backed setup path for local bootstrap callers."""
    from src.core.requirements_cache import get_requirements_sync

    return run_requirements_setup_for_content(get_requirements_sync())


def _requirements_content_from_stdin() -> str | None:
    """Read the remote helper's explicit requirements payload from stdin."""
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or "content" not in payload:
        raise ValueError("requirements setup input must be an object with content")
    content = payload["content"]
    if content is not None and not isinstance(content, str):
        raise ValueError("requirements setup input content must be a string or null")
    return content


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--local"]:
        result = run_requirements_setup()
    elif arguments:
        raise ValueError("Unknown requirements setup arguments")
    else:
        result = run_requirements_setup_for_content(_requirements_content_from_stdin())
    sys.stdout.write(json.dumps(result.to_json_dict(), separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
