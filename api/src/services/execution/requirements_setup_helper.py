"""Subprocess entry point for worker requirements setup."""

from __future__ import annotations

import json
import logging
import subprocess
import sys

from src.services.execution.requirements_setup_result import RequirementsInstallResult

logger = logging.getLogger(__name__)


def _get_installed_packages() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"Failed to get installed packages: {e}")
    return []


def _requirement_name(line: str) -> str:
    return line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip().lower()


def _update_requirements_status(result: RequirementsInstallResult) -> None:
    from src.core.requirements_cache import get_requirements_sync
    from src.services.execution.simple_worker import _parse_requirement_lines

    content = get_requirements_sync()
    if not content:
        result.requirements_total = 0
        result.requirements_installed = 0
        return

    required = {_requirement_name(line) for line in _parse_requirement_lines(content)}
    result.requirements_total = len(required)

    installed = {p["name"].lower() for p in _get_installed_packages()}
    result.requirements_installed = len(required & installed)

    missing = required - installed
    if missing:
        logger.warning(f"[pool] Missing required packages: {', '.join(sorted(missing))}")
    else:
        logger.info(f"[pool] All {result.requirements_total} required packages installed")


def run_requirements_setup() -> RequirementsInstallResult:
    from src.services.execution.simple_worker import install_requirements

    result = install_requirements()
    try:
        _update_requirements_status(result)
    except Exception as e:  # noqa: BLE001 - status counts are heartbeat-only
        logger.warning(f"Failed to check requirements status: {e}")
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    result = run_requirements_setup()
    sys.stdout.write(json.dumps(result.to_json_dict(), separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
