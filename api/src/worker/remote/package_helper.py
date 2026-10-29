"""Run one physical package tool command for the API-hosted consumer."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from src.jobs.consumers.package_install import PackageInstallConsumer
from src.services.execution.process_pool import _get_installed_packages


async def dispatch(command: dict[str, Any]) -> dict[str, Any]:
    """Use canonical pip helpers without creating a process pool in this process."""
    helper = PackageInstallConsumer()
    operation = command["operation"]
    if operation == "install":
        error = await helper._pip_install(command["package"], command.get("version"))
        return {"error": error}
    if operation == "uninstall":
        error = await helper._pip_uninstall(command["package"])
        return {"error": error}
    if operation == "requirements":
        error = await helper._pip_install_requirements()
        return {"error": error}
    if operation == "inventory":
        return {"packages": await asyncio.to_thread(_get_installed_packages)}
    raise ValueError(f"Unknown package tool operation: {operation}")


async def main() -> None:
    logging.basicConfig(stream=sys.stderr, force=True)
    result = await dispatch(json.loads(sys.argv[1]))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
