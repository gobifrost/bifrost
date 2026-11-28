"""Direct DB fault case: grant effects and checkpoint must roll back together.

No worker job is launched; full graph/grant/runtime success is covered by the
single existing conversion journey.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from shared import workflow_conversions as conversion
from src.jobs.platform.base import PlatformJobContext, PlatformJobCancelled
from src.models.orm.audit import AuditLog
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.workflow_conversions import WorkflowConversionReport
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from tests.unit.test_workflow_conversion_service import _admin

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_cancelled_apply_rolls_back_grants_audit_report_and_completion_checkpoint(
    async_session_factory, platform_admin, monkeypatch,
):
    workflow_id, analysis_id, job_id, token = (uuid4() for _ in range(4))
    fingerprint = 'a'*64
    async with async_session_factory() as db:
        db.add(Workflow(id=workflow_id, name=f'atomic-apply-{workflow_id.hex}', function_name='run',
            path=f'workflows/atomic_{workflow_id.hex}.py', permission_mode='full', is_active=True))
        for identifier, kind, status in [(analysis_id, conversion.ANALYZE_JOB_TYPE, 'succeeded'), (job_id, conversion.APPLY_JOB_TYPE, 'running')]:
            db.add(PlatformJob(id=identifier, job_type=kind, status=status, payload={}, title='Atomic apply fixture',
                result={'completed': []} if identifier == job_id else None,
                lease_token=token if identifier == job_id else None,
                lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5) if identifier == job_id else None,
                requested_by_user_id=str(platform_admin.user_id), requested_by_email=platform_admin.email,
                requested_by_name=platform_admin.name))
        await db.flush()
        db.add(WorkflowPermissionGrant(workflow_id=workflow_id, permission='configs.read'))
        db.add(WorkflowConversionReport(job_id=analysis_id, workflow_id=workflow_id,
            source_fingerprint=fingerprint, status='ready', report={'status': 'ready'}))
        await db.commit()
    context = PlatformJobContext(job_id, token, None, str(platform_admin.user_id), platform_admin.email,
        platform_admin.name, file_compute=AsyncMock())
    checkpoint_reached = False
    async def cancel_checkpoint(self, db, result, *, phase):
        nonlocal checkpoint_reached
        assert result == {'completed': [str(workflow_id)]}
        # The intended effects really reached the uncommitted DB transaction.
        row = await db.get(Workflow, workflow_id)
        assert row.permission_mode == 'restricted'
        assert set((await db.scalars(select(WorkflowPermissionGrant.permission).where(
            WorkflowPermissionGrant.workflow_id == workflow_id))).all()) == {'roles.read'}
        checkpoint_reached = True
        raise PlatformJobCancelled
    monkeypatch.setattr(PlatformJobContext, 'checkpoint_in_transaction', cancel_checkpoint)
    monkeypatch.setattr(conversion, '_require_current_conversion_admin', AsyncMock(return_value=_admin()))
    monkeypatch.setattr(conversion, 'read_runtime_modules', AsyncMock())
    monkeypatch.setattr(conversion, '_source_for_workflow', AsyncMock(return_value=object()))
    monkeypatch.setattr(conversion, '_prepare_source_graph', AsyncMock(return_value=SimpleNamespace(
        allow_outbound_access=True, analysis={'dependencies': {}}, sources={}, summaries={})))
    monkeypatch.setattr(conversion, '_verify_prepared_source_graph', AsyncMock(return_value=True))
    monkeypatch.setattr(conversion, '_report_for_workflow', AsyncMock(return_value={
        'source_fingerprint': fingerprint, 'unknowns': [], 'proposed_permissions': ['roles.read']}))
    monkeypatch.setattr(conversion, '_refresh_applied_report', AsyncMock(return_value={'source_fingerprint': 'b'*64}))
    payload = SimpleNamespace(analysis_job_id=analysis_id, acknowledged_solution_requests=False,
        selections=[SimpleNamespace(workflow_id=workflow_id, source_fingerprint=fingerprint, acknowledged_unknowns=False)])
    try:
        with pytest.raises(PlatformJobCancelled):
            await conversion.run_apply(context, payload)
        assert checkpoint_reached
        async with async_session_factory() as db:
            assert (await db.get(Workflow, workflow_id)).permission_mode == 'full'
            assert set((await db.scalars(select(WorkflowPermissionGrant.permission).where(
                WorkflowPermissionGrant.workflow_id == workflow_id))).all()) == {'configs.read'}
            report = await db.scalar(select(WorkflowConversionReport).where(
                WorkflowConversionReport.job_id == analysis_id))
            assert report.status == 'ready' and report.review is None
            assert (await db.get(PlatformJob, job_id)).result == {'completed': []}
            assert not list((await db.scalars(select(AuditLog.id).where(AuditLog.resource_id == workflow_id))).all())
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(AuditLog).where(AuditLog.resource_id == workflow_id))
            await db.execute(delete(PlatformJob).where(PlatformJob.id.in_((job_id, analysis_id))))
            await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await db.commit()
