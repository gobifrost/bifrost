"""One cutover job proves API admission and reviewed default/audit commit.

The prerequisite analysis/report is direct setup, not another expensive job.
All mutations belong to this command-owned disposable test stack.
"""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from shared import workflow_conversions as conversion
from src.models.orm.audit import AuditLog
from src.models.orm.config import SystemConfig
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.workflow_conversions import WorkflowConversionReport, WorkflowConversionAnalysisCache
from src.models.orm.workflows import Workflow
from src.services.repo_storage import RepoStorage
from tests.e2e.platform.thin_live_helpers import wait_for_job

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_reviewed_cutover_commits_in_api_without_physical_worker(
    async_session_factory, e2e_client, platform_admin,
):
    workflow_id, analysis_id = uuid4(), uuid4()
    path = f"workflows/cutover_{workflow_id.hex}.py"
    cutover_id = None
    prior_setting = None
    setting_id = None
    setting_query = select(SystemConfig).where(
        SystemConfig.category == conversion.WORKFLOW_PERMISSIONS_CONFIG_CATEGORY,
        SystemConfig.key == conversion.DEFAULT_MODE_CONFIG_KEY,
        SystemConfig.organization_id.is_(None),
    )
    await RepoStorage().write(path, b"async def run():\n    return 1\n")
    try:
        async with async_session_factory() as db:
            # Never alter unrelated workflows to force a cutover fixture.
            assert not list((await db.scalars(select(Workflow.id).where(Workflow.is_active.is_(True)))).all())
            setting = await db.scalar(setting_query)
            if setting is not None:
                setting_id = setting.id
                prior_setting = (deepcopy(setting.value_json), setting.updated_by, setting.updated_at)
            workflow = Workflow(id=workflow_id, name=f"cutover-{workflow_id.hex}", path=path,
                function_name="run", permission_mode="restricted", is_active=True)
            db.add(workflow)
            db.add(PlatformJob(id=analysis_id, job_type=conversion.ANALYZE_JOB_TYPE,
                status="succeeded", payload={"platform_cutover": True}, title="Cutover prerequisite",
                requested_by_user_id=str(platform_admin.user_id), requested_by_email=platform_admin.email,
                requested_by_name=platform_admin.name))
            await db.flush()
            report = await conversion._report_for_workflow(db, workflow)
            assert not report['unknowns'] and report['status'] == 'already_declared'
            db.add(WorkflowConversionReport(job_id=analysis_id, workflow_id=workflow_id,
                source_fingerprint=report['source_fingerprint'], status=report['status'], report=report))
            await db.commit()
        response = e2e_client.post(f"/api/workflow-conversions/{analysis_id}/cutover",
            headers=platform_admin.headers, json={"acknowledged_remaining_exceptions": True})
        assert response.status_code == 202, response.text
        cutover_id = UUID(response.json()['job_id'])
        result = wait_for_job(e2e_client, platform_admin.headers, cutover_id, conversion.CUTOVER_JOB_TYPE)
        assert result['result'] == {'default_mode': 'restricted'}
        async with async_session_factory() as db:
            row = await db.get(PlatformJob, cutover_id)
            assert row.attempt == 1 and row.executor_worker_id is None
            assert row.executor_compute_capability is None
            setting = await db.scalar(setting_query)
            assert setting.value_json == {'mode': 'restricted'}
            audit = await db.scalar(select(AuditLog).where(
                AuditLog.action == 'workflow.permission_conversion.cutover',
                AuditLog.details['analysis_job_id'].astext == str(analysis_id)))
            assert audit is not None and audit.user_id == platform_admin.user_id
    finally:
        await RepoStorage().delete(path)
        async with async_session_factory() as db:
            setting = await db.scalar(setting_query)
            if cutover_id is not None:
                if prior_setting is not None:
                    assert setting is not None and setting.id == setting_id
                    setting.value_json, setting.updated_by, setting.updated_at = prior_setting
                elif setting is not None:
                    # Only this disposable fixture's cutover may have created it.
                    assert setting.value_json == {'mode': 'restricted'}
                    await db.delete(setting)
            await db.execute(delete(AuditLog).where(
                AuditLog.action == 'workflow.permission_conversion.cutover',
                AuditLog.details['analysis_job_id'].astext == str(analysis_id)))
            await db.execute(delete(WorkflowConversionAnalysisCache).where(
                WorkflowConversionAnalysisCache.source_key == conversion._cache_source_key(path)))
            await db.execute(delete(PlatformJob).where(PlatformJob.id.in_(
                [analysis_id] + ([cutover_id] if cutover_id else []))))
            await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await db.commit()
