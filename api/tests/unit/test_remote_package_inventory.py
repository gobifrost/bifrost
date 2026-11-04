"""Inventory must not import the execution pool or package mutation consumer."""

import importlib
import sys

import pytest


@pytest.mark.asyncio
async def test_inventory_does_not_load_execution_or_mutation_runtime(monkeypatch):
    monkeypatch.delitem(sys.modules, "src.worker.remote.package_helper", raising=False)
    monkeypatch.setitem(sys.modules, "src.services.execution.process_pool", None)
    monkeypatch.setitem(sys.modules, "src.jobs.consumers.package_install", None)
    helper = importlib.import_module("src.worker.remote.package_helper")
    packages = [{"name": "example-package", "version": "1.2.3"}]
    monkeypatch.setattr(helper, "_get_installed_packages", lambda: packages)

    assert await helper.dispatch({"operation": "inventory"}) == {"packages": packages}
