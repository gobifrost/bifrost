"""One live automatic App SDK upgrade through the shared coordinator.

Cost: one ``application.sdk_update`` PlatformJob and one real Vite build. Source,
the old immutable deployment, and its retained archive are seeded directly because
they are setup artifacts; the test's cross-process contract is the automatic job
that builds, activates, and preserves its recovery artifacts.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from shared.application_sdk_upgrades import (
    automatic_upgrade_eligible,
    enqueue_automatic_app_sdk_upgrades,
)
from shared.subprocess_env import package_tool_env
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.services.application_sdk_status import current_sdk_metadata
from src.services.application_source_artifact import ApplicationSourceArtifactStorage
from src.services.sdk_package import build_sdk_tarball
from src.services.solutions.app_build import SolutionAppBuilder
from tests.e2e.platform.thin_live_helpers import wait_for_job

pytestmark = pytest.mark.e2e

_SETTINGS_PATH = "/api/maintenance/app-sdk-upgrades/settings"


def _automatic_upgrade_source() -> dict[str, bytes]:
    """Build retained source with a lockfile matching the worker's SDK tarball."""
    package = {
        "name": "automatic-sdk-upgrade-live",
        "private": True,
        "dependencies": {
            "bifrost": "file:./bifrost-sdk.tgz",
            "lucide-react": "^1.61.0",
            "react": "18.3.1",
            "react-dom": "18.3.1",
        },
        "devDependencies": {"vite": "6.2.2"},
    }
    with tempfile.TemporaryDirectory(prefix="bifrost-auto-sdk-lock-") as tmp:
        workdir = Path(tmp)
        (workdir / "package.json").write_text(json.dumps(package))
        (workdir / "bifrost-sdk.tgz").write_bytes(
            build_sdk_tarball(current_sdk_metadata().package_version)
        )
        subprocess.run(
            [
                "npm",
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            cwd=workdir,
            check=True,
            capture_output=True,
            timeout=120,
            env=package_tool_env(),
        )
        lockfile = (workdir / "package-lock.json").read_bytes()

    return {
        "package.json": json.dumps(package).encode(),
        "package-lock.json": lockfile,
        "index.html": (
            b"<!doctype html><html><body><div id='root'></div>"
            b"<script type='module' src='/src/main.ts'></script></body></html>"
        ),
        "src/main.ts": (
            b"import { tables } from 'bifrost';\n"
            b"document.querySelector('#root')!.textContent = String(Boolean(tables));\n"
        ),
    }


async def _store_source(app_id, deployment_id, files: dict[str, bytes]) -> None:
    with tempfile.NamedTemporaryFile(suffix=".zip") as temporary:
        path = Path(temporary.name)
        with zipfile.ZipFile(path, "w") as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        await ApplicationSourceArtifactStorage().write_deployment_source(
            app_id, deployment_id, path
        )


async def test_automatic_upgrade_builds_once_and_keeps_recovery_artifacts(
    e2e_client, platform_admin, async_session_factory
) -> None:
    """An eligible contract-2 App reaches current SDK through one live worker job."""
    target = current_sdk_metadata()
    assert target.contract_version == 3
    assert target.automatic_upgrade_from == frozenset({2})
    assert target.fingerprint

    app_id = uuid4()
    old_deployment_id = uuid4()
    app_slug = f"auto-sdk-live-{app_id.hex[:8]}"
    source_storage = ApplicationSourceArtifactStorage()
    builder = SolutionAppBuilder()
    setting_before: bool | None = None
    setting_changed = False

    try:
        source = _automatic_upgrade_source()
        async with async_session_factory() as db:
            app = Application(
                id=app_id,
                name="Automatic SDK Upgrade Live",
                slug=app_slug,
                app_model="standalone_v2",
                active_deployment_id=old_deployment_id,
                deployed_at=datetime.now(timezone.utc),
                sdk_package_version="old-sdk",
                sdk_fingerprint="old-fingerprint",
                sdk_contract_version=2,
                sdk_built_at=datetime.now(timezone.utc),
                access_level="authenticated",
            )
            db.add(app)
            await db.commit()
            assert automatic_upgrade_eligible(app, target)

        await builder.upload_deployment(
            app_id, old_deployment_id, {"index.html": b"<main>old SDK</main>"}
        )
        await _store_source(app_id, old_deployment_id, source)

        previous_setting = e2e_client.get(
            _SETTINGS_PATH, headers=platform_admin.headers
        )
        assert previous_setting.status_code == 200, previous_setting.text
        setting_before = previous_setting.json()["enabled"]
        enabled = e2e_client.put(
            _SETTINGS_PATH, json={"enabled": True}, headers=platform_admin.headers
        )
        assert enabled.status_code == 200, enabled.text
        setting_changed = True

        await enqueue_automatic_app_sdk_upgrades()
        async with async_session_factory() as db:
            jobs = list(
                await db.scalars(
                    select(PlatformJob).where(
                        PlatformJob.job_type == "application.sdk_update",
                        PlatformJob.resource_id == str(app_id),
                    )
                )
            )
        assert len(jobs) == 1
        job_record = jobs[0]
        assert job_record.payload["automatic_upgrade"] is True
        assert job_record.payload["target_sdk_fingerprint"] == target.fingerprint
        assert (
            job_record.payload["target_sdk_contract_version"] == target.contract_version
        )
        job_id = job_record.id
        assert job_id is not None

        job = wait_for_job(
            e2e_client, platform_admin.headers, job_id, "application.sdk_update"
        )
        new_deployment_id = job["result"]["deployment_id"]
        assert new_deployment_id != str(old_deployment_id)

        async with async_session_factory() as db:
            updated = await db.get(Application, app_id)
            assert updated is not None
            assert str(updated.active_deployment_id) == new_deployment_id
            assert updated.sdk_fingerprint == target.fingerprint
            assert updated.sdk_contract_version == target.contract_version

            job_count = await db.scalar(
                select(func.count()).where(
                    PlatformJob.job_type == "application.sdk_update",
                    PlatformJob.resource_id == str(app_id),
                )
            )
        assert job_count == 1
        assert await builder.read_dist(
            app_id, "index.html", deployment_id=old_deployment_id
        ) == (b"<main>old SDK</main>")
        assert b"assets/" in await builder.read_dist(
            app_id, "index.html", deployment_id=new_deployment_id
        )
        assert await source_storage.deployment_source_exists(app_id, old_deployment_id)
        assert await source_storage.deployment_source_exists(app_id, new_deployment_id)

        await enqueue_automatic_app_sdk_upgrades()
        async with async_session_factory() as db:
            jobs = list(
                await db.scalars(
                    select(PlatformJob).where(
                        PlatformJob.job_type == "application.sdk_update",
                        PlatformJob.resource_id == str(app_id),
                    )
                )
            )
        assert len(jobs) == 1
    finally:
        try:
            if setting_changed and setting_before is not None:
                restored = e2e_client.put(
                    _SETTINGS_PATH,
                    json={"enabled": setting_before},
                    headers=platform_admin.headers,
                )
                assert restored.status_code == 200, restored.text
        finally:
            try:
                deleted = e2e_client.delete(
                    f"/api/applications/{app_id}", headers=platform_admin.headers
                )
                if deleted.status_code not in {204, 404}:
                    raise AssertionError(deleted.text)
            finally:
                try:
                    await builder.delete_deployment(app_id, old_deployment_id)
                finally:
                    try:
                        await source_storage.delete_application_artifacts(app_id)
                    finally:
                        async with async_session_factory() as db:
                            await db.execute(
                                delete(PlatformJob).where(
                                    PlatformJob.job_type == "application.sdk_update",
                                    PlatformJob.resource_id == str(app_id),
                                )
                            )
                            await db.commit()
