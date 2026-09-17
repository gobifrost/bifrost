"""E2E resource-cost probe for the ``workspace.reimport`` platform job.

Seeds a modest but representative workspace into the S3-backed ``_repo/``
(3 workflow ``.py`` files + ``.bifrost/workflows.yaml`` entries, 1 app with
preview source files, 1 table + 1 form manifest entry), enqueues a
``workspace.reimport`` job, executes it to a terminal state, and records the
working-set cost.

Execution path note: the scheduler's ``run_claimed_platform_job`` spawns
``python -m src.jobs.platform.runner`` as a subprocess with a heartbeat loop.
Driving a subprocess + heartbeat from inside e2e is impractical, so this test
claims the row directly (mirroring the scheduler claim's memory bookkeeping)
and executes the attempt in-process via
``src.jobs.platform.runner.run_claimed_platform_job`` — the same function the
subprocess entrypoint calls — which runs the handler and finalizes the row via
``finish_platform_job``. Start/peak working set is sampled with
``src.services.execution.memory_monitor.get_cgroup_memory`` before/after the
run and persisted onto the ``platform_jobs`` row.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)

MIB = 1024 * 1024

WORKFLOW_TEMPLATE = """\
from bifrost import workflow


@workflow(name="{tool_name}")
def {func_name}(message: str):
    \"\"\"Memory-probe workflow for workspace.reimport measurement.\"\"\"
    return {{"result": message, "workflow": "{tool_name}"}}
"""

APP_PACKAGE_JSON = """\
{{"name": "{slug}", "version": "0.0.0", "private": true, "dependencies": {{}}}}
"""

APP_TSX = """\
export default function App() {{
  return <div>memreimport probe app {suffix}</div>;
}}
"""

APP_HTML = """\
<!doctype html><html><body><div id="root"></div></body></html>
"""


def _read_memory_bytes() -> tuple[int | None, int | None, str]:
    """Return (working_set_bytes, limit_bytes, source) for this container.

    Primary source is the cgroup v2 working set (same signal the scheduler
    claim/heartbeat bookkeeping uses). Falls back to process RSS when cgroup
    files are unavailable so the probe still records evidence.
    """
    try:
        from src.services.execution.memory_monitor import get_cgroup_memory

        current, limit = get_cgroup_memory()
        if current is not None and current >= 0:
            return current, (limit if limit and limit > 0 else None), "cgroup"
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.debug(f"cgroup memory read failed, trying RSS fallback: {exc}")
    try:
        import resource
        import sys

        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        value = int(rss_kb) if sys.platform == "darwin" else int(rss_kb) * 1024
        return value, None, "ru_maxrss-fallback"
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.debug(f"RSS fallback read failed: {exc}")
        return None, None, "unavailable"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workspace_reimport_memory(db_session: AsyncSession):
    """Enqueue + execute workspace.reimport; assert terminal success + memory recorded."""
    from src.jobs.platform import runner as platform_runner
    from src.jobs.platform.reimport import WORKSPACE_REIMPORT_DEFINITION
    from src.models.orm.applications import Application
    from src.models.orm.forms import Form, FormField
    from src.models.orm.platform_jobs import PlatformJob
    from src.models.orm.tables import Table
    from src.models.orm.workflows import Workflow
    from src.services.platform_jobs import enqueue_platform_job

    suffix = uuid4().hex[:8]
    repo = RepoStorage(get_settings())
    seeded_s3_paths: list[str] = []
    wf_ids: list = []
    app_id = uuid4()
    table_id = uuid4()
    form_id = uuid4()
    job_id = None

    async def _write_s3(path: str, content: bytes) -> None:
        await repo.write(path, content)
        seeded_s3_paths.append(path)

    try:
        # -- Seed 1: three workflow .py files in _repo/ + matching DB rows --
        wf_specs: list[tuple[str, str, str]] = []
        for tag in ("a", "b", "c"):
            func_name = f"memreimport_{suffix}_{tag}"
            wf_specs.append((f"memreimport_{suffix}_{tag}.py", func_name, func_name))
        for path, func_name, tool_name in wf_specs:
            await _write_s3(
                path, WORKFLOW_TEMPLATE.format(tool_name=tool_name, func_name=func_name).encode()
            )
            db_session.add(
                Workflow(
                    name=tool_name,
                    function_name=func_name,
                    path=path,
                    type="workflow",
                    is_active=True,
                )
            )

        # -- Seed 2: one app directory with preview source files + DB row --
        app_slug = f"memreimport-demo-{suffix}"
        app_dir = f"apps/memreimport-demo-{suffix}"
        await _write_s3(f"{app_dir}/package.json", APP_PACKAGE_JSON.format(slug=app_slug).encode())
        await _write_s3(f"{app_dir}/src/App.tsx", APP_TSX.format(suffix=suffix).encode())
        await _write_s3(f"{app_dir}/index.html", APP_HTML.encode())
        db_session.add(
            Application(
                id=app_id,
                name=f"MemReimport Demo {suffix}",
                slug=app_slug,
                repo_path=app_dir,
                app_model="inline_v1",
            )
        )

        # -- Seed 3: one table + one form (manifest-inline entities, no files) --
        db_session.add(
            Table(
                id=table_id,
                name=f"memreimport_items_{suffix}",
                description="Memory-probe table",
                schema={"columns": [{"name": "title", "type": "text"}]},
            )
        )
        db_session.add(
            Form(
                id=form_id,
                name=f"memreimport_form_{suffix}",
                description="Memory-probe form",
                created_by="e2e-memreimport",
                workflow_path=wf_specs[0][0],
                workflow_function_name=wf_specs[0][1],
            )
        )
        db_session.add(
            FormField(
                form_id=form_id,
                name="item_name",
                label="Item",
                type="text",
                required=False,
                position=0,
            )
        )
        await db_session.flush()

        # -- Seed 4: .bifrost/*.yaml manifest entries built with the real --
        # -- manifest models so the YAML follows api/bifrost/manifest.py.  --
        from bifrost.manifest import (
            Manifest,
            ManifestApp,
            ManifestForm,
            ManifestTable,
            ManifestWorkflow,
            serialize_manifest_dir,
        )

        wf_rows = (
            await db_session.execute(
                select(Workflow).where(Workflow.path.like(f"memreimport_{suffix}_%"))
            )
        ).scalars().all()
        assert len(wf_rows) == 3, f"expected 3 seeded workflows, got {len(wf_rows)}"
        for wf in wf_rows:
            wf_ids.append(wf.id)
        manifest = Manifest(
            workflows={
                wf.name: ManifestWorkflow(
                    id=str(wf.id),
                    name=wf.name,
                    path=wf.path,
                    function_name=wf.function_name,
                    type="workflow",
                )
                for wf in wf_rows
            },
            apps={
                app_slug: ManifestApp(
                    id=str(app_id),
                    path=app_dir,
                    slug=app_slug,
                    name=f"MemReimport Demo {suffix}",
                    app_model="inline_v1",
                )
            },
            tables={
                f"memreimport_items_{suffix}": ManifestTable(
                    id=str(table_id),
                    name=f"memreimport_items_{suffix}",
                    description="Memory-probe table",
                    schema={"columns": [{"name": "title", "type": "text"}]},
                )
            },
            forms={
                f"memreimport_form_{suffix}": ManifestForm(
                    id=str(form_id),
                    name=f"memreimport_form_{suffix}",
                    description="Memory-probe form",
                    form_schema={
                        "fields": [
                            {
                                "name": "item_name",
                                "type": "text",
                                "label": "Item",
                                "required": False,
                            }
                        ]
                    },
                )
            },
        )
        manifest_files = serialize_manifest_dir(manifest)
        assert manifest_files, "serialize_manifest_dir produced no files"
        for filename, content in manifest_files.items():
            await _write_s3(f".bifrost/{filename}", content.encode())
        await db_session.commit()

        # -- Enqueue workspace.reimport --
        job, reused = await enqueue_platform_job(
            db_session,
            WORKSPACE_REIMPORT_DEFINITION,
            {},
            dedupe_key=f"memreimport-{suffix}",
            organization_id=None,
            requested_by_user_id=uuid4(),
            requested_by_email="dev@gobifrost.com",
            requested_by_name="E2E MemReimport",
            resource_type="workspace",
            resource_id="e2e",
            title="E2E workspace reimport memory probe",
            action_url=None,
        )
        assert not reused
        job_id = job.id

        # -- Claim the row directly (mirrors the scheduler claim, minus the --
        # -- subprocess): never visible as queued, so the live test-stack   --
        # -- scheduler cannot race us for it.                                --
        start_bytes, limit_bytes, mem_source = _read_memory_bytes()
        now = datetime.now(timezone.utc)
        from datetime import timedelta

        lease_token = uuid4()
        job.status = "running"
        job.phase = "Starting (e2e memory probe)"
        job.attempt = 1
        job.started_at = now
        job.lease_owner = "e2e-memreimport"
        job.lease_token = lease_token
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(minutes=5)
        job.memory_start_bytes = start_bytes
        job.memory_peak_bytes = start_bytes
        job.memory_limit_bytes = limit_bytes
        job.revision = (job.revision or 0) + 1
        await db_session.commit()

        # -- Execute the attempt in-process (same function the runner --
        # -- subprocess entrypoint calls) to a terminal state.         --
        wall_start = time.monotonic()
        completed = await platform_runner.run_claimed_platform_job(job_id, lease_token)
        wall_seconds = time.monotonic() - wall_start
        assert completed, "in-process runner did not complete the claimed job"

        # -- Record post-run peak on the terminal row. --
        end_bytes, _, end_source = _read_memory_bytes()
        await db_session.refresh(job)
        if end_bytes is not None and end_bytes >= 0:
            prior_peak = job.memory_peak_bytes or 0
            job.memory_peak_bytes = max(prior_peak, end_bytes)
            if job.memory_start_bytes is None:
                job.memory_start_bytes = (
                    start_bytes if start_bytes is not None else end_bytes
                )
            await db_session.commit()
            await db_session.refresh(job)

        # -- Assertions: robust, no hardcoded byte counts. --
        assert job.status == "succeeded", f"job failed: {job.error_code} {job.error_message}"
        assert isinstance(job.result, dict) and "entities_imported" in job.result, (
            f"unexpected result shape: {job.result}"
        )
        entity_count = job.result["entities_imported"]
        assert isinstance(entity_count, int) and entity_count >= 0
        assert job.memory_start_bytes is not None, "memory_start_bytes was not recorded"
        assert job.memory_peak_bytes is not None, "memory_peak_bytes was not recorded"
        mem_start_bytes = cast(int, job.memory_start_bytes)
        mem_peak_bytes = cast(int, job.memory_peak_bytes)
        assert mem_start_bytes >= 0
        assert mem_peak_bytes >= mem_start_bytes

        delta_bytes = mem_peak_bytes - mem_start_bytes
        evidence = (
            f"[memreimport] entities_imported={entity_count} "
            f"wall_time_s={wall_seconds:.2f} "
            f"mem_start_mib={mem_start_bytes / MIB:.2f} "
            f"mem_peak_mib={mem_peak_bytes / MIB:.2f} "
            f"delta_mib={delta_bytes / MIB:.2f} "
            f"mem_source={mem_source}/{end_source} "
            f"seeded=3-workflows+1-app({len([p for p in seeded_s3_paths if p.startswith(app_dir)])}-files)+1-table+1-form"
        )
        logger.info(evidence)
        print(evidence, flush=True)
    finally:
        # Best-effort cleanup so reruns without a state reset stay isolated.
        try:
            for path in seeded_s3_paths:
                if not path.startswith(".bifrost/"):
                    try:
                        await repo.delete(path)
                    except Exception as exc:
                        logger.debug(f"cleanup S3 {path} skipped: {exc}")
            await db_session.execute(
                delete(FormField).where(FormField.form_id == form_id)
            )
            await db_session.execute(delete(Form).where(Form.id == form_id))
            await db_session.execute(delete(Table).where(Table.id == table_id))
            await db_session.execute(
                delete(Application).where(Application.id == app_id)
            )
            if wf_ids:
                await db_session.execute(delete(Workflow).where(Workflow.id.in_(wf_ids)))
            if job_id is not None:
                await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db_session.commit()
        except Exception as exc:
            logger.debug(f"memreimport cleanup skipped: {exc}")
