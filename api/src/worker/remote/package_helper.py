"""Run one physical package tool command for the API-hosted consumer."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from src.services.execution.requirements_setup_helper import (
    _get_installed_packages,
    run_requirements_setup_for_content,
)


async def dispatch(command: dict[str, Any]) -> dict[str, Any]:
    """Use canonical pip helpers without creating a process pool in this process."""
    operation = command["operation"]
    if operation == "inventory":
        return {"packages": await asyncio.to_thread(_get_installed_packages)}

    if operation == "requirements":
        if "content" not in command:
            raise ValueError("requirements operation requires explicit content")
        content = command["content"]
        if content is not None and not isinstance(content, str):
            raise ValueError("requirements content must be a string or null")
        result = await asyncio.to_thread(run_requirements_setup_for_content, content)
        error = None
        if result.failed:
            error = "; ".join(f"{failed.package}: {failed.error}" for failed in result.failed)
        return {
            "error": error,
            "requirements": result.to_json_dict(),
            "packages": await asyncio.to_thread(_get_installed_packages),
        }

    from src.jobs.consumers.package_install import PackageInstallConsumer

    helper = PackageInstallConsumer()
    if operation == "install":
        error = await helper._pip_install(command["package"], command.get("version"))
        return {"error": error}
    if operation == "uninstall":
        error = await helper._pip_uninstall(command["package"])
        return {"error": error}
    raise ValueError(f"Unknown package tool operation: {operation}")


async def main() -> None:
    logging.basicConfig(stream=sys.stderr, force=True)
    result = await dispatch(json.load(sys.stdin))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
