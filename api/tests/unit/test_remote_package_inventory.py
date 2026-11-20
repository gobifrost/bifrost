"""Inventory must not import the execution pool or package mutation consumer."""

import importlib
import io
import json
import sys
from unittest.mock import AsyncMock

import pytest

from src.services.execution.requirements_setup_result import RequirementsInstallResult


@pytest.mark.asyncio
async def test_inventory_does_not_load_execution_or_mutation_runtime(monkeypatch):
    monkeypatch.delitem(sys.modules, "src.worker.remote.package_helper", raising=False)
    monkeypatch.setitem(sys.modules, "src.services.execution.process_pool", None)
    monkeypatch.setitem(sys.modules, "src.jobs.consumers.package_install", None)
    helper = importlib.import_module("src.worker.remote.package_helper")
    packages = [{"name": "example-package", "version": "1.2.3"}]
    monkeypatch.setattr(helper, "_get_installed_packages", lambda: packages)

    assert await helper.dispatch({"operation": "inventory"}) == {"packages": packages}


@pytest.mark.asyncio
async def test_requirements_uses_explicit_content_without_mutation_consumer(monkeypatch):
    monkeypatch.delitem(sys.modules, "src.worker.remote.package_helper", raising=False)
    monkeypatch.setitem(sys.modules, "src.jobs.consumers.package_install", None)
    helper = importlib.import_module("src.worker.remote.package_helper")
    installed = RequirementsInstallResult(installed=["example-package"])
    monkeypatch.setattr(helper, "run_requirements_setup_for_content", lambda content: installed)
    monkeypatch.setattr(helper, "_get_installed_packages", lambda: [{"name": "example-package", "version": "1.2.3"}])

    result = await helper.dispatch({"operation": "requirements", "content": "example-package==1.2.3\n"})

    assert result == {
        "error": None,
        "requirements": {
            "attempted": [],
            "installed": ["example-package"],
            "failed": [],
            "requirements_installed": 0,
            "requirements_total": 0,
        },
        "packages": [{"name": "example-package", "version": "1.2.3"}],
    }


@pytest.mark.asyncio
async def test_main_reads_package_command_from_stdin(monkeypatch):
    helper = importlib.import_module("src.worker.remote.package_helper")
    stdout = io.StringIO()
    dispatch = AsyncMock(return_value={"packages": []})
    monkeypatch.setattr(helper, "dispatch", dispatch)
    monkeypatch.setattr(helper.sys, "stdin", io.StringIO(json.dumps({"operation": "inventory"})))
    monkeypatch.setattr(helper.sys, "stdout", stdout)

    await helper.main()

    dispatch.assert_awaited_once_with({"operation": "inventory"})
    assert json.loads(stdout.getvalue()) == {"packages": []}
