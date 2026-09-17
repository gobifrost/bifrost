"""End-to-end local Kubernetes build-Job spike fixture.

Run only inside the API pod for the disposable Kubernetes spike environment.
The script seeds independent standalone_v2 Apps, enqueues real build-class
PlatformJobs, and verifies that the warm scheduler can complete a lightweight
maintenance job while remote App builds are running.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import configure_mappers

from src.config import get_settings
from src.core.database import close_db, get_db_context, init_db
from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.jobs.platform.application_sdk_update import (
    APPLICATION_SDK_UPDATE_DEFINITION,
    ApplicationSdkUpdatePayload,
)
from src.jobs.platform.system_maintenance import (
    ARTIFACT_RETENTION_CLEANUP_DEFINITION,
    EmptyMaintenancePayload,
)
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.services.application_deploy_storage import ApplicationDeployStorage
from src.services.platform_jobs import enqueue_platform_job, publish_platform_job_update
from src.services.solutions.app_build import SolutionAppBuilder


BUILD_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 1
DEFAULT_SYNTHETIC_HOLD_SECONDS = 20


@dataclass
class JobSnapshot:
    id: str
    job_type: str
    status: str
    phase: str | None
    execution_backend: str
    kubernetes_job_name: str | None
    kubernetes_job_uid: str | None
    kubernetes_pod_uid: str | None
    memory_start_bytes: int | None
    memory_peak_bytes: int | None
    memory_limit_bytes: int | None
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None


def _json_default(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _guard_environment() -> None:
    settings = get_settings()
    if os.environ.get("BIFROST_KUBERNETES_SPIKE") != "1":
        raise RuntimeError("BIFROST_KUBERNETES_SPIKE=1 is required")
    if settings.environment != "testing":
        raise RuntimeError(
            f"Refusing to run outside testing environment: {settings.environment!r}"
        )
    if settings.platform_build_backend != "kubernetes":
        raise RuntimeError(
            "BIFROST_PLATFORM_BUILD_BACKEND=kubernetes is required for this spike"
        )


def _safe_job_snapshot(job: PlatformJob) -> JobSnapshot:
    return JobSnapshot(
        id=str(job.id),
        job_type=job.job_type,
        status=job.status,
        phase=job.phase,
        execution_backend=job.execution_backend,
        kubernetes_job_name=job.kubernetes_job_name,
        kubernetes_job_uid=job.kubernetes_job_uid,
        kubernetes_pod_uid=job.kubernetes_pod_uid,
        memory_start_bytes=job.memory_start_bytes,
        memory_peak_bytes=job.memory_peak_bytes,
        memory_limit_bytes=job.memory_limit_bytes,
        result=job.result,
        error_code=job.error_code,
        error_message=job.error_message,
    )


async def _load_jobs(job_ids: list[UUID]) -> list[PlatformJob]:
    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(PlatformJob)
                .where(PlatformJob.id.in_(job_ids))
                .order_by(PlatformJob.created_at.asc())
            )
        ).scalars().all()
        by_id = {row.id: row for row in rows}
        return [by_id[job_id] for job_id in job_ids if job_id in by_id]


async def _load_job(job_id: UUID) -> PlatformJob:
    rows = await _load_jobs([job_id])
    if not rows:
        raise RuntimeError(f"PlatformJob {job_id} disappeared")
    return rows[0]


def _diagnostics(jobs: list[PlatformJob]) -> list[dict[str, Any]]:
    return [asdict(_safe_job_snapshot(job)) for job in jobs]


async def _wait_for_builds_running(job_ids: list[UUID]) -> list[PlatformJob]:
    deadline = asyncio.get_running_loop().time() + BUILD_TIMEOUT_SECONDS
    while True:
        jobs = await _load_jobs(job_ids)
        if (
            len(jobs) == len(job_ids)
            and all(job.status == "running" for job in jobs)
            and all(job.kubernetes_pod_uid for job in jobs)
        ):
            return jobs
        failed = [job for job in jobs if job.status in {"failed", "canceled"}]
        if failed:
            raise RuntimeError(
                "Build failed before both Kubernetes pods were admitted: "
                + json.dumps(_diagnostics(jobs), default=_json_default)
            )
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(
                "Timed out waiting for build jobs to reach running with pod UIDs: "
                + json.dumps(_diagnostics(jobs), default=_json_default)
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _wait_for_job_terminal(job_id: UUID, *, timeout_seconds: int) -> PlatformJob:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        job = await _load_job(job_id)
        if job.status in {"succeeded", "failed", "canceled"}:
            return job
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(
                "Timed out waiting for PlatformJob terminal state: "
                + json.dumps(asdict(_safe_job_snapshot(job)), default=_json_default)
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _wait_for_job_succeeded(job_id: UUID, *, timeout_seconds: int) -> PlatformJob:
    job = await _wait_for_job_terminal(job_id, timeout_seconds=timeout_seconds)
    if job.status != "succeeded":
        raise RuntimeError(
            "PlatformJob did not succeed: "
            + json.dumps(asdict(_safe_job_snapshot(job)), default=_json_default)
        )
    return job


def _write_vite_source_zip(path: Path, *, marker: str, hold_seconds: int) -> str:
    files = {
        "package.json": json.dumps(
            {
                "name": f"bifrost-kubernetes-build-spike-{marker}",
                "private": True,
                "type": "module",
                "scripts": {"build": "vite build"},
                "devDependencies": {
                    "typescript": "^5.5.0",
                    "vite": "^5.4.0",
                },
            },
            indent=2,
        ).encode(),
        "index.html": b'<!doctype html><div id="root"></div><script type="module" src="/src/main.ts"></script>',
        "src/main.ts": (
            "const root = document.getElementById('root');\n"
            f"if (root) root.textContent = 'kubernetes build spike {marker}';\n"
        ).encode(),
        "vite.config.ts": (
            "import { defineConfig } from 'vite';\n"
            "function syntheticDelay() {\n"
            "  return {\n"
            "    name: 'bifrost-synthetic-close-bundle-delay',\n"
            "    async closeBundle() {\n"
            f"      await new Promise((resolve) => setTimeout(resolve, {hold_seconds * 1000}));\n"
            "    },\n"
            "  };\n"
            "}\n"
            "export default defineConfig({ plugins: [syntheticDelay()] });\n"
        ).encode(),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _seed_application(prefix: str, index: int) -> Application:
    app = Application(
        id=uuid4(),
        name=f"{prefix} build app {index}",
        slug=f"{prefix}-build-app-{index}",
        repo_path=None,
        organization_id=None,
        solution_id=None,
        app_model="standalone_v2",
        access_level="authenticated",
        description="Local Kubernetes build Job spike fixture; retained for inspection.",
        created_by="kubernetes-build-spike",
    )
    async with get_db_context() as db:
        db.add(app)
        await db.flush()
        await db.commit()
    return app


async def _enqueue_deploy_job(app: Application, source_zip: Path) -> PlatformJob:
    job_id = uuid4()
    deployment_id = uuid4()
    input_sha256, _input_size = await ApplicationDeployStorage(job_id).write_path(source_zip)
    async with get_db_context() as db:
        job, reused = await enqueue_platform_job(
            db,
            APPLICATION_DEPLOY_DEFINITION,
            ApplicationDeployPayload(
                application_id=app.id,
                deployment_id=deployment_id,
                input_sha256=input_sha256,
            ),
            dedupe_key=f"kubernetes-build-spike:{app.id}:{deployment_id}",
            resource_lock_key=f"application:{app.id}",
            priority=50,
            organization_id=None,
            requested_by_user_id="kubernetes-build-spike",
            requested_by_email="kubernetes-build-spike@gobifrost.local",
            requested_by_name="Kubernetes Build Spike",
            resource_type="application",
            resource_id=str(app.id),
            title=f"Deploy spike App {app.slug}",
            action_url=None,
            job_id=job_id,
        )
        if reused:
            raise RuntimeError(f"Unexpected reused deploy job for {app.id}")
        await db.commit()
    await publish_platform_job_update(job)
    return job


async def _enqueue_maintenance_job(prefix: str) -> PlatformJob:
    async with get_db_context() as db:
        job, reused = await enqueue_platform_job(
            db,
            ARTIFACT_RETENTION_CLEANUP_DEFINITION,
            EmptyMaintenancePayload(),
            dedupe_key=f"kubernetes-build-spike:{prefix}:artifact-retention",
            resource_lock_key=f"artifact_retention:{prefix}",
            priority=10,
            organization_id=None,
            requested_by_user_id="kubernetes-build-spike",
            requested_by_email="kubernetes-build-spike@gobifrost.local",
            requested_by_name="Kubernetes Build Spike",
            resource_type="system",
            resource_id="artifact.retention_cleanup",
            title="Kubernetes spike artifact retention cleanup",
            action_url="/diagnostics",
        )
        if reused:
            raise RuntimeError("Unexpected reused maintenance job")
        await db.commit()
    await publish_platform_job_update(job)
    return job


async def _enqueue_sdk_update_job(app: Application) -> PlatformJob:
    async with get_db_context() as db:
        fresh = await db.get(Application, app.id)
        if fresh is None:
            raise RuntimeError(f"Application {app.id} disappeared")
        job, reused = await enqueue_platform_job(
            db,
            APPLICATION_SDK_UPDATE_DEFINITION,
            ApplicationSdkUpdatePayload(
                application_id=fresh.id,
                deployment_id=uuid4(),
                expected_active_deployment_id=fresh.active_deployment_id,
                expected_sdk_package_version=fresh.sdk_package_version,
                expected_sdk_fingerprint=fresh.sdk_fingerprint,
                expected_sdk_contract_version=fresh.sdk_contract_version,
                expected_sdk_built_at=fresh.sdk_built_at,
            ),
            dedupe_key=f"kubernetes-build-spike:{fresh.id}:sdk-update:{uuid4()}",
            resource_lock_key=f"application:{fresh.id}",
            priority=50,
            organization_id=None,
            requested_by_user_id="kubernetes-build-spike",
            requested_by_email="kubernetes-build-spike@gobifrost.local",
            requested_by_name="Kubernetes Build Spike",
            resource_type="application",
            resource_id=str(fresh.id),
            title=f"SDK update spike App {fresh.slug}",
            action_url=None,
        )
        if reused:
            raise RuntimeError(f"Unexpected reused SDK update job for {fresh.id}")
        await db.commit()
    await publish_platform_job_update(job)
    return job


async def _verify_application_deployment(app_id: UUID) -> dict[str, Any]:
    async with get_db_context() as db:
        app = await db.get(Application, app_id)
        if app is None:
            raise RuntimeError(f"Application {app_id} disappeared")
        if app.active_deployment_id is None:
            raise RuntimeError(f"Application {app_id} has no active deployment")
        active_deployment_id = app.active_deployment_id
        sdk = {
            "sdk_package_version": app.sdk_package_version,
            "sdk_fingerprint": app.sdk_fingerprint,
            "sdk_contract_version": app.sdk_contract_version,
            "sdk_built_at": app.sdk_built_at,
        }

    index_html = await SolutionAppBuilder().read_dist(
        app_id,
        "index.html",
        deployment_id=active_deployment_id,
    )
    if b"<html" not in index_html.lower() and b"<!doctype" not in index_html.lower():
        raise RuntimeError(f"Deployment {active_deployment_id} index.html is unreadable")

    return {
        "application_id": str(app_id),
        "active_deployment_id": str(active_deployment_id),
        "index_html_bytes": len(index_html),
        **sdk,
    }


async def run_build_mode(args: argparse.Namespace) -> dict[str, Any]:
    prefix = f"k8s-build-{uuid4().hex[:10]}"
    _guard_environment()
    await init_db()
    configure_mappers()

    # Deploys serialize by default (max_concurrency=1); the overlap
    # experiment needs two concurrent builds, so lift it for the run
    # through the same Settings mechanism operators use. Restored below.
    from src.services.kubernetes_execution import KubernetesExecutionService

    async with get_db_context() as override_db:
        await KubernetesExecutionService(
            override_db
        ).set_job_type_concurrency(
            "application.deploy", 2, updated_by="kubernetes-build-spike"
        )
        await override_db.commit()

    try:
        with tempfile.TemporaryDirectory(prefix="bifrost-k8s-build-spike-") as tmp:
            tmp_path = Path(tmp)
            apps = [
                await _seed_application(prefix, 1),
                await _seed_application(prefix, 2),
            ]
            deploy_jobs: list[PlatformJob] = []
            for index, app in enumerate(apps, start=1):
                source_zip = tmp_path / f"{app.slug}.zip"
                source_sha256 = _write_vite_source_zip(
                    source_zip,
                    marker=f"{prefix}-{index}",
                    hold_seconds=args.hold_seconds,
                )
                job = await _enqueue_deploy_job(app, source_zip)
                deploy_jobs.append(job)
                print(
                    json.dumps(
                        {
                            "event": "submitted_deploy",
                            "application_id": str(app.id),
                            "job_id": str(job.id),
                            "source_sha256": source_sha256,
                            "synthetic_close_bundle_delay_seconds": (
                                args.hold_seconds
                            ),
                            "note": "Synthetic Vite closeBundle delay creates overlap; it is not build latency.",
                        }
                    ),
                    flush=True,
                )

            deploy_job_ids = [job.id for job in deploy_jobs]
            running = await _wait_for_builds_running(deploy_job_ids)
            if any(job.execution_backend != "kubernetes" for job in running):
                raise RuntimeError(
                    "Deploy jobs were not assigned to the Kubernetes backend: "
                    + json.dumps(_diagnostics(running), default=_json_default)
                )
            if any(job.memory_limit_bytes != get_settings().kubernetes_build_memory_limit_mib * 1024 * 1024 for job in running):
                raise RuntimeError(
                    "Deploy jobs did not use the configured pod memory limit: "
                    + json.dumps(_diagnostics(running), default=_json_default)
                )
            print(
                json.dumps(
                    {
                        "event": "builds_running",
                        "jobs": _diagnostics(running),
                        "synthetic_close_bundle_delay_seconds": args.hold_seconds,
                        "note": "Remote build pods admitted; synthetic Vite delay is fixture hold time, not benchmark latency.",
                    },
                    default=_json_default,
                ),
                flush=True,
            )
            maintenance = await _enqueue_maintenance_job(prefix)
            maintenance_done = await _wait_for_job_succeeded(
                maintenance.id,
                timeout_seconds=BUILD_TIMEOUT_SECONDS,
            )
            if maintenance_done.execution_backend != "local":
                raise RuntimeError(
                    "Maintenance job did not stay on the local backend: "
                    + json.dumps(
                        asdict(_safe_job_snapshot(maintenance_done)),
                        default=_json_default,
                    )
                )
            builds_at_maintenance_done = await _load_jobs(deploy_job_ids)
            if not any(job.status == "running" for job in builds_at_maintenance_done):
                raise RuntimeError(
                    "Maintenance job completed after all builds stopped; no overlap proven: "
                    + json.dumps(
                        {
                            "maintenance": asdict(_safe_job_snapshot(maintenance_done)),
                            "builds": _diagnostics(builds_at_maintenance_done),
                        },
                        default=_json_default,
                    )
                )

            succeeded_builds = [
                await _wait_for_job_succeeded(job_id, timeout_seconds=BUILD_TIMEOUT_SECONDS)
                for job_id in deploy_job_ids
            ]
            deployment_checks = [
                await _verify_application_deployment(app.id)
                for app in apps
            ]

            sdk_job = await _enqueue_sdk_update_job(apps[0])
            sdk_done = await _wait_for_job_succeeded(
                sdk_job.id,
                timeout_seconds=BUILD_TIMEOUT_SECONDS,
            )
            sdk_check = await _verify_application_deployment(apps[0].id)
            if sdk_check["active_deployment_id"] == deployment_checks[0]["active_deployment_id"]:
                raise RuntimeError(
                    "SDK update did not activate a new deployment: "
                    + json.dumps(
                        {
                            "before": deployment_checks[0],
                            "after": sdk_check,
                            "sdk_job": asdict(_safe_job_snapshot(sdk_done)),
                        },
                        default=_json_default,
                    )
                )

            result = {
                "mode": args.mode,
                "prefix": prefix,
                "submitted_job_ids": {
                    "deploy": [str(job_id) for job_id in deploy_job_ids],
                    "maintenance": str(maintenance.id),
                    "sdk_update": str(sdk_job.id),
                },
                "synthetic_close_bundle_delay_seconds": args.hold_seconds,
                "synthetic_delay_note": (
                    "The Vite closeBundle delay is only a deterministic overlap fixture, "
                    "not a build latency measurement."
                ),
                "running_before_maintenance": _diagnostics(running),
                "maintenance": asdict(_safe_job_snapshot(maintenance_done)),
                "builds_at_maintenance_done": _diagnostics(builds_at_maintenance_done),
                "deploy_outcomes": _diagnostics(succeeded_builds),
                "deployment_checks": deployment_checks,
                "sdk_update_outcome": asdict(_safe_job_snapshot(sdk_done)),
                "sdk_update_deployment_check": sdk_check,
            }
            if args.artifact_output:
                Path(args.artifact_output).write_text(
                    json.dumps(result, indent=2, default=_json_default)
                )
            return result
    finally:
        try:
            async with get_db_context() as override_db:
                await KubernetesExecutionService(
                    override_db
                ).set_job_type_concurrency(
                    "application.deploy",
                    None,
                    updated_by="kubernetes-build-spike",
                )
                await override_db.commit()
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "concurrency_restore_failed",
                        "error": str(exc),
                    }
                ),
                flush=True,
            )
        await close_db()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["build"], default="build")
    parser.add_argument(
        "--artifact-output",
        help="Optional path for the final JSON result. Fixture rows/artifacts are preserved.",
    )
    parser.add_argument(
        "--hold-seconds",
        type=int,
        default=DEFAULT_SYNTHETIC_HOLD_SECONDS,
        help=(
            "Synthetic Vite closeBundle delay used to keep build Jobs running "
            "for overlap checks. This is fixture hold time, not benchmark latency."
        ),
    )
    args = parser.parse_args()
    if args.hold_seconds < 1:
        parser.error("--hold-seconds must be at least 1")
    return args


async def main() -> None:
    args = parse_args()
    result = await run_build_mode(args)
    print(json.dumps(result, indent=2, default=_json_default), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
