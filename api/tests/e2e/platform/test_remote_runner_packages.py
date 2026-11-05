"""Live package-generation coverage for the physical remote executors.

The wheel is staged in the fixture directory mounted by the test runner, API,
scheduler, and worker.  The only package actions are the public refresh endpoint
and the public uninstall endpoint; no registry or network package source is
used.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import ZipFile

import pytest
import redis
from sqlalchemy import func, select

from src.config import get_settings
from src.core.requirements_cache import REQUIREMENTS_KEY
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.package_changes import PackageChange
from tests.e2e.conftest import execute_workflow_sync, write_and_register


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_SHARED_ROOT = Path("/tmp/bifrost/solution-repo-fixtures")
_REQUIREMENTS_PATH = "requirements.txt"


async def _wait_for(
    predicate: Callable[[], Awaitable[Any | None]], *, timeout: float, description: str
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = await predicate()
        if last is not None:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {description}: {last!r}")


def _write_wheel(directory: Path, module: str, distribution: str) -> Path:
    """Build the smallest valid pure-Python wheel entirely from local bytes."""
    wheel = directory / f"{module}-0.0.1-py3-none-any.whl"
    metadata = f"{module}-0.0.1.dist-info"
    files = {
        f"{module}/__init__.py": 'VALUE = "installed-from-local-wheel"\n',
        f"{metadata}/METADATA": (
            "Metadata-Version: 2.1\n"
            f"Name: {distribution}\n"
            "Version: 0.0.1\n"
        ),
        f"{metadata}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: bifrost-e2e\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
    }
    with ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(f"{metadata}/RECORD", "".join(f"{name},,\n" for name in files))
    return wheel


def _read_requirements(e2e_client, headers) -> str | None:
    response = e2e_client.get(
        "/api/files/editor/content", headers=headers, params={"path": _REQUIREMENTS_PATH}
    )
    if response.status_code == 404:
        return None
    assert response.status_code == 200, response.text
    return response.json()["content"]


def _write_requirements(e2e_client, headers, content: str) -> None:
    response = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": _REQUIREMENTS_PATH, "content": content, "encoding": "utf-8"},
    )
    assert response.status_code in (200, 201), response.text


def _restore_requirements(e2e_client, headers, original: str | None) -> None:
    if original is not None:
        _write_requirements(e2e_client, headers, original)
        return
    response = e2e_client.delete(
        "/api/files/editor", headers=headers, params={"path": _REQUIREMENTS_PATH}
    )
    assert response.status_code in (204, 404), response.text


async def _package_changes_after(async_session_factory, generation: int) -> list[PackageChange]:
    async with async_session_factory() as db:
        return list(
            await db.scalars(
                select(PackageChange)
                .where(PackageChange.id > generation)
                .order_by(PackageChange.id)
            )
        )


async def _current_package_generation(async_session_factory) -> int:
    async with async_session_factory() as db:
        return int(await db.scalar(select(func.coalesce(func.max(PackageChange.id), 0))) or 0)


async def _workflow_workers_at_generation(
    async_session_factory, generation: int
) -> list[ExecutionWorker] | None:
    async with async_session_factory() as db:
        workers = list(
            await db.scalars(
                select(ExecutionWorker).where(
                    ExecutionWorker.form == "static",
                    ExecutionWorker.state == "ready",
                    ExecutionWorker.remote_owner_id.is_not(None),
                    ExecutionWorker.supported_handlers.op("?")("workflow.execute"),
                )
            )
        )
    # The E2E profile has exactly two physical workflow launchers: scheduler
    # and worker. The API-resident agent executor does not advertise workflow.
    if len(workers) != 2:
        return None
    if any(
        worker.package_generation is None or worker.package_generation < generation
        for worker in workers
    ):
        return None
    return workers


async def test_refreshing_requirements_recycles_both_remote_workflow_templates(
    async_session_factory,
    e2e_client,
    platform_admin,
) -> None:
    suffix = uuid4().hex
    module = f"bifrost_remote_pkg_{suffix}"
    distribution = module.replace("_", "-")
    workflow_name = f"e2e_remote_package_{suffix}"
    workflow_path = f"workflows/{workflow_name}.py"
    wheel_dir = _SHARED_ROOT / f"remote-package-{suffix}"
    wheel_dir.mkdir(parents=True)
    wheel = _write_wheel(wheel_dir, module, distribution)
    original_requirements = _read_requirements(e2e_client, platform_admin.headers)
    cache = redis.Redis.from_url(get_settings().redis_url)
    original_cache = cache.get(REQUIREMENTS_KEY)
    original_cache_ttl = cache.pttl(REQUIREMENTS_KEY)
    original_generation = await _current_package_generation(async_session_factory)
    refresh_generation: int | None = None
    uninstall_generation: int | None = None
    workflow_written = False

    try:
        # ``--no-index`` guarantees pip can satisfy this refresh only from the
        # wheel mounted at the file URI; the test never contacts PyPI.
        _write_requirements(
            e2e_client,
            platform_admin.headers,
            f"--no-index\n{wheel.as_uri()}\n",
        )
        refreshed = e2e_client.post(
            "/api/packages/install", headers=platform_admin.headers, json={}
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["package_name"] is None

        async def refresh_recorded() -> PackageChange | None:
            changes = await _package_changes_after(async_session_factory, original_generation)
            if not changes:
                return None
            assert len(changes) == 1, changes
            change = changes[0]
            assert (change.action, change.package, change.version) == ("install", None, None)
            return change

        refresh_change = await _wait_for(
            refresh_recorded, timeout=20, description="requirements refresh package generation"
        )
        refresh_generation = refresh_change.id
        workers = await _wait_for(
            lambda: _workflow_workers_at_generation(async_session_factory, refresh_generation),
            timeout=120,
            description="both physical workflow workers applying the refresh",
        )
        assert len(workers) == 2

        workflow_source = f'''from bifrost import workflow

@workflow(name="{workflow_name}")
async def {workflow_name}():
    import {module}
    return {{"value": {module}.VALUE}}
'''
        workflow = write_and_register(
            e2e_client,
            platform_admin.headers,
            workflow_path,
            workflow_source,
            workflow_name,
        )
        workflow_written = True
        installed = execute_workflow_sync(
            e2e_client, platform_admin.headers, workflow["id"], max_wait=90.0
        )
        assert installed["status"] == "Success", installed
        assert installed["result"] == {"value": "installed-from-local-wheel"}

        # Restore the durable workspace source first. The canonical uninstall
        # removes the installed distribution and recycles both templates.
        _restore_requirements(e2e_client, platform_admin.headers, original_requirements)
        uninstalled = e2e_client.delete(
            f"/api/packages/{distribution}", headers=platform_admin.headers
        )
        assert uninstalled.status_code == 200, uninstalled.text

        async def uninstall_recorded() -> PackageChange | None:
            changes = await _package_changes_after(async_session_factory, original_generation)
            if len(changes) < 2:
                return None
            assert len(changes) == 2, changes
            change = changes[1]
            assert (change.action, change.package, change.version) == (
                "uninstall",
                distribution,
                None,
            )
            return change

        uninstall_change = await _wait_for(
            uninstall_recorded, timeout=20, description="canonical uninstall package generation"
        )
        uninstall_generation = uninstall_change.id
        await _wait_for(
            lambda: _workflow_workers_at_generation(async_session_factory, uninstall_generation),
            timeout=120,
            description="both physical workflow workers applying the uninstall",
        )
        unavailable = execute_workflow_sync(
            e2e_client, platform_admin.headers, workflow["id"], max_wait=90.0
        )
        assert unavailable["status"] == "Failed", unavailable
        assert module in unavailable["error_message"], unavailable
    finally:
        # A failure before the normal cleanup still uses the canonical API to
        # remove the physical package. This makes the real interpreter state
        # safe before the shared fixture directory disappears.
        if refresh_generation is not None and uninstall_generation is None:
            _restore_requirements(e2e_client, platform_admin.headers, original_requirements)
            response = e2e_client.delete(
                f"/api/packages/{distribution}", headers=platform_admin.headers
            )
            assert response.status_code == 200, response.text
        else:
            _restore_requirements(e2e_client, platform_admin.headers, original_requirements)

        if workflow_written:
            response = e2e_client.delete(
                "/api/files/editor", headers=platform_admin.headers, params={"path": workflow_path}
            )
            assert response.status_code in (204, 404), response.text
        shutil.rmtree(wheel_dir, ignore_errors=True)
        if original_cache is None:
            cache.delete(REQUIREMENTS_KEY)
        elif original_cache_ttl > 0:
            cache.set(REQUIREMENTS_KEY, original_cache, px=original_cache_ttl)
        else:
            cache.set(REQUIREMENTS_KEY, original_cache)
        cache.close()
