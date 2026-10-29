"""Live disposable-runner coverage for non-build PlatformJob handlers.

Each case queues work through ``enqueue_platform_job`` and observes it through
the live API.  The thin API worker claims the job, hands it over the bridge,
and starts the physical runner process.  The cases deliberately seed one
observable unit of work instead of accepting an empty successful sweep.

Cost: eight PlatformJob runner children.  ``agent.summary_backfill`` also
hands one real summary dispatch to the test worker, which is the cross-process
contract unique to that parent job.
"""

from __future__ import annotations

from copy import deepcopy
import gzip
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from shared.file_paths import resolve_s3_key
from src.config import get_settings
from src.jobs.platform.audit_archive import AUDIT_ARCHIVE_DEFINITION, AuditArchivePayload
from src.jobs.platform.audit_query import AUDIT_QUERY_DEFINITION
from src.jobs.platform.file_move import FILE_MOVE_DEFINITION, FileMovePayload, FileMoveWorkItem
from src.jobs.platform.reimport import WORKSPACE_REIMPORT_DEFINITION, WorkspaceReimportPayload
from src.jobs.platform.run_retention import RUN_RETENTION_DEFINITION, RunRetentionPayload
from src.jobs.platform.summary_backfill import SUMMARY_BACKFILL_DEFINITION, SummaryBackfillPayload
from src.jobs.platform.system_maintenance import (
    ARTIFACT_RETENTION_CLEANUP_DEFINITION,
    EmptyMaintenancePayload,
)
from src.jobs.platform.workspace_bundle_import import (
    WORKSPACE_BUNDLE_IMPORT_DEFINITION,
    WorkspaceBundleImportPayload,
)
from src.models.contracts.artifact_retention import ArtifactRetentionSettings
from src.models.contracts.audit_retention import AuditExportRequest, AuditRetentionSettings
from src.models.orm import Artifact, AuditArchiveSegment, AuditLog, Event, EventSource, PlatformJob, SystemConfig
from src.models.orm.agents import Agent
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection
from src.models.orm.events import EventSourceType
from src.models.orm.file_metadata import FileMetadata
from src.models.orm.file_index import FileIndex
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.work_dispatches import WorkDispatch
from src.models.orm.workflows import Workflow
from src.services.artifact_retention import ArtifactRetentionSettingsService
from src.services.audit_retention.export import AuditQueryPayload, ReachSnapshot, export_key
from src.services.audit_retention.settings import AuditRetentionSettingsService
from src.services.audit_retention.store import AuditArchiveStore
from src.services.file_storage.s3_client import S3StorageClient
from src.services.platform_jobs import enqueue_platform_job
from src.services.repo_storage import RepoStorage
from src.models.contracts.run_retention import RunRetentionSettings
from src.services.run_retention.settings import RunRetentionSettingsService
from src.services.solutions.deploy import SolutionBundle
from src.services.solutions.export import build_workspace_zip
from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage
from src.models.orm.solutions import Solution
from tests.e2e.platform.thin_live_helpers import wait_for_job


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _enqueue(
    async_session_factory, platform_admin, definition, payload, *, label: str, job_id: UUID | None = None,
) -> UUID:
    """Use canonical enqueue; the live thin API worker claims and runs it."""
    async with async_session_factory() as db:
        job, reused = await enqueue_platform_job(
            db,
            definition,
            payload,
            dedupe_key=f"thin-misc:{label}:{uuid4()}",
            resource_lock_key=f"thin-misc:{label}:{uuid4()}",
            organization_id=None,
            requested_by_user_id=platform_admin.user_id,
            requested_by_email=platform_admin.email,
            requested_by_name=platform_admin.name,
            resource_type="thin-misc",
            resource_id=label,
            title=f"Thin misc {label}",
            action_url=None,
            job_id=job_id,
        )
        assert not reused
        job_id = job.id
        await db.commit()
    return job_id


async def _delete_job(async_session_factory, job_id: UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
        await db.commit()


async def _put_s3(key: str, content: bytes) -> None:
    storage = S3StorageClient(get_settings())
    async with storage.get_client() as s3:
        await s3.put_object(Bucket=get_settings().s3_bucket, Key=key, Body=content)


async def _object_exists(key: str) -> bool:
    return await S3StorageClient(get_settings()).head_object(key) is not None


async def _delete_s3(key: str) -> None:
    storage = S3StorageClient(get_settings())
    async with storage.get_client() as s3:
        await s3.delete_object(Bucket=get_settings().s3_bucket, Key=key)


async def test_artifact_retention_cleanup_runs_real_handler_and_deletes_seeded_artifact(
    async_session_factory, e2e_client, platform_admin,
):
    artifact_id = uuid4()
    key = f"_artifacts/thin-misc/{artifact_id}.txt"
    job_id: UUID | None = None
    previous_value: dict | None = None
    try:
        await _put_s3(key, b"expired artifact")
        async with async_session_factory() as db:
            previous = await db.scalar(select(SystemConfig).where(
                SystemConfig.category == "artifact_retention", SystemConfig.key == "chat",
                SystemConfig.organization_id.is_(None),
            ))
            previous_value = deepcopy(previous.value_json) if previous is not None else None
            await ArtifactRetentionSettingsService(db).update_settings(
                ArtifactRetentionSettings(enabled=True, retention_days=1), updated_by="thin-misc"
            )
            db.add(Artifact(
                id=artifact_id, s3_key=key, filename="expired.txt", content_type="text/plain",
                size_bytes=16, created_at=datetime.now(UTC) - timedelta(days=2),
            ))
            await db.commit()

        job_id = await _enqueue(
            async_session_factory, platform_admin, ARTIFACT_RETENTION_CLEANUP_DEFINITION, EmptyMaintenancePayload(),
            label="artifact-retention",
        )
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "artifact.retention_cleanup")
        assert job["result"] == {"enabled": True, "retention_days": 1, "deleted_count": 1, "failed_count": 0}
        async with async_session_factory() as db:
            assert await db.get(Artifact, artifact_id) is None
        assert not await _object_exists(key)
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Artifact).where(Artifact.id == artifact_id))
            current = await db.scalar(select(SystemConfig).where(
                SystemConfig.category == "artifact_retention", SystemConfig.key == "chat",
                SystemConfig.organization_id.is_(None),
            ))
            if previous_value is None:
                if current is not None:
                    await db.delete(current)
            elif current is not None:
                current.value_json = previous_value
            await db.commit()
        if job_id is not None:
            await _delete_job(async_session_factory, job_id)


async def test_audit_archive_runs_real_handler_and_archives_seeded_event(
    async_session_factory, e2e_client, platform_admin,
):
    source_id, audit_id = uuid4(), uuid4()
    job_id: UUID | None = None
    segment_keys: list[str] = []
    previous_value: dict | None = None
    try:
        async with async_session_factory() as db:
            previous = await db.scalar(select(SystemConfig).where(
                SystemConfig.category == "audit_retention", SystemConfig.key == "policy",
                SystemConfig.organization_id.is_(None),
            ))
            previous_value = deepcopy(previous.value_json) if previous is not None else None
            await AuditRetentionSettingsService(db).update_settings(
                AuditRetentionSettings(hot_days=1, archive_days=None), updated_by="thin-misc"
            )
            db.add(EventSource(id=source_id, name=f"thin-audit-{source_id}", source_type=EventSourceType.TOPIC,
                               event_type="thin.audit", created_by="thin-misc"))
            db.add(AuditLog(id=audit_id, action="thin.misc.archive", source="service",
                            details={"fixture": "thin-misc"}, created_at=datetime.now(UTC) - timedelta(days=2)))
            await db.commit()
        job_id = await _enqueue(
            async_session_factory, platform_admin, AUDIT_ARCHIVE_DEFINITION, AuditArchivePayload(), label="audit-archive"
        )
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "audit.archive")
        assert job["result"] is not None and job["result"]["archived_rows"] == 1
        async with async_session_factory() as db:
            assert await db.get(AuditLog, audit_id) is None
            segments = (await db.execute(select(AuditArchiveSegment).where(
                AuditArchiveSegment.platform_job_id == job_id
            ))).scalars().all()
            assert len(segments) == 1
            segment_keys = [segment.object_key for segment in segments]
            await db.execute(delete(AuditArchiveSegment).where(AuditArchiveSegment.platform_job_id == job_id))
            await db.execute(delete(EventSource).where(EventSource.id == source_id))
            await db.commit()
    finally:
        store = AuditArchiveStore(get_settings())
        for key in segment_keys:
            await store.delete(key)
        async with async_session_factory() as db:
            await db.execute(delete(AuditLog).where(AuditLog.id == audit_id))
            await db.execute(delete(EventSource).where(EventSource.id == source_id))
            current = await db.scalar(select(SystemConfig).where(
                SystemConfig.category == "audit_retention", SystemConfig.key == "policy",
                SystemConfig.organization_id.is_(None),
            ))
            if previous_value is None:
                if current is not None:
                    await db.delete(current)
            elif current is not None:
                current.value_json = previous_value
            await db.commit()
        if job_id is not None:
            await _delete_job(async_session_factory, job_id)


async def test_audit_query_runs_real_handler_and_writes_seeded_export(
    async_session_factory, e2e_client, platform_admin,
):
    audit_id = uuid4()
    job_id: UUID | None = None
    now = datetime.now(UTC)
    try:
        async with async_session_factory() as db:
            db.add(AuditLog(id=audit_id, action="thin.misc.export", source="service",
                            details={"fixture": "thin-misc"}, created_at=now))
            await db.commit()
        payload = AuditQueryPayload(
            request=AuditExportRequest(
                start_date=now - timedelta(minutes=1), end_date=now + timedelta(minutes=1),
                action="thin.misc.export",
            ),
            reach=ReachSnapshot(everything=True, organization_ids=[], managed=False, include_global=True),
        )
        job_id = await _enqueue(async_session_factory, platform_admin, AUDIT_QUERY_DEFINITION, payload, label="audit-query")
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "audit.query")
        assert job["result"] is not None and job["result"]["rows"] == 1
        assert job["result"]["export_key"] == export_key(job_id)
        content = await AuditArchiveStore(get_settings()).get(export_key(job_id))
        assert b"thin.misc.export" in gzip.decompress(content)
    finally:
        if job_id is not None:
            await AuditArchiveStore(get_settings()).delete(export_key(job_id))
            await _delete_job(async_session_factory, job_id)
        async with async_session_factory() as db:
            await db.execute(delete(AuditLog).where(AuditLog.id == audit_id))
            await db.commit()


async def test_run_retention_runs_real_handler_and_deletes_seeded_event(
    async_session_factory, e2e_client, platform_admin,
):
    source_id, event_id = uuid4(), uuid4()
    job_id: UUID | None = None
    previous = None
    try:
        async with async_session_factory() as db:
            previous = await RunRetentionSettingsService(db).get_settings()
            await RunRetentionSettingsService(db).update_settings(RunRetentionSettings(days=30), updated_by="thin-misc")
            db.add(EventSource(id=source_id, name=f"thin-retention-{source_id}", source_type=EventSourceType.TOPIC,
                               event_type="thin.retention", created_by="thin-misc"))
            db.add(Event(id=event_id, event_source_id=source_id, data={"fixture": "thin-misc"},
                         created_at=datetime.now(UTC) - timedelta(days=31)))
            await db.commit()
        job_id = await _enqueue(
            async_session_factory, platform_admin, RUN_RETENTION_DEFINITION, RunRetentionPayload(), label="run-retention"
        )
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "run.retention")
        assert job["result"] is not None and job["result"]["events_deleted"] == 1
        async with async_session_factory() as db:
            assert await db.get(Event, event_id) is None
    finally:
        async with async_session_factory() as db:
            if previous is not None:
                await RunRetentionSettingsService(db).update_settings(previous, updated_by="thin-misc")
            await db.execute(delete(Event).where(Event.id == event_id))
            await db.execute(delete(EventSource).where(EventSource.id == source_id))
            await db.commit()
        if job_id is not None:
            await _delete_job(async_session_factory, job_id)


async def test_files_move_runs_real_handler_and_moves_seeded_object(
    async_session_factory, e2e_client, platform_admin,
):
    scope = f"thin-misc-{uuid4().hex}"
    source, destination = "before.txt", "after.txt"
    source_key = resolve_s3_key("docs", scope, source)
    destination_key = resolve_s3_key("docs", scope, destination)
    job_id: UUID | None = None
    try:
        await _put_s3(source_key, b"physical runner move")
        user_id = platform_admin.user_id
        payload = FileMovePayload(
            location="docs", scope=scope, requested_by_user_id=user_id, policy_user_id=user_id,
            total=1, items=[FileMoveWorkItem(index=0, source=source, destination=destination)],
        )
        job_id = await _enqueue(async_session_factory, platform_admin, FILE_MOVE_DEFINITION, payload, label="files-move")
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "files.move")
        assert job["result"] is not None and job["result"]["moved"] == 1
        assert job["result"]["results"] == [{"source": source, "destination": destination, "path": None,
                                            "renamed": False, "adopted": False, "status_code": 200, "error": None}]
        assert not await _object_exists(source_key)
        assert await S3StorageClient(get_settings()).read_uploaded_file(destination_key) == b"physical runner move"
    finally:
        await _delete_s3(source_key)
        await _delete_s3(destination_key)
        async with async_session_factory() as db:
            await db.execute(delete(FileMetadata).where(FileMetadata.s3_key.in_((source_key, destination_key))))
            await db.commit()
        if job_id is not None:
            await _delete_job(async_session_factory, job_id)


async def test_workspace_reimport_runs_real_handler_and_indexes_seeded_source(
    async_session_factory, e2e_client, platform_admin,
):
    workflow_id = uuid4()
    suffix = uuid4().hex[:8]
    path = f"workflows/thin_reimport_{suffix}.py"
    source = f"def thin_reimport_{suffix}():\n    return 'indexed'\n"
    job_id: UUID | None = None
    try:
        await RepoStorage().write(path, source.encode())
        async with async_session_factory() as db:
            db.add(Workflow(id=workflow_id, name=f"thin-reimport-{suffix}", function_name=f"thin_reimport_{suffix}",
                            path=path, type="workflow", access_level="authenticated", is_active=True))
            await db.commit()
        job_id = await _enqueue(
            async_session_factory, platform_admin, WORKSPACE_REIMPORT_DEFINITION, WorkspaceReimportPayload(), label="workspace-reimport"
        )
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "workspace.reimport")
        assert job["result"] is not None and "entities_imported" in job["result"]
        async with async_session_factory() as db:
            indexed = await db.get(FileIndex, path)
            assert indexed is not None and indexed.content == source
    finally:
        await RepoStorage().delete(path)
        async with async_session_factory() as db:
            await db.execute(delete(FileIndex).where(FileIndex.path == path))
            await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await db.commit()
        if job_id is not None:
            await _delete_job(async_session_factory, job_id)


async def test_workspace_bundle_import_runs_real_handler_and_promotes_seeded_bundle(
    async_session_factory, e2e_client, platform_admin,
):
    suffix = uuid4().hex[:8]
    workflow_id = uuid4()
    path = f"workflows/thin_bundle_{suffix}.py"
    source = f"def thin_bundle_{suffix}():\n    return 'imported'\n"
    archive = build_workspace_zip(SolutionBundle(
        solution=Solution(id=uuid4(), slug=f"thin-bundle-{suffix}", name="Thin Bundle"),
        python_files={path: source},
        workflows=[{"id": str(workflow_id), "name": f"thin-bundle-{suffix}", "path": path,
                    "function_name": f"thin_bundle_{suffix}", "organization_id": None}],
    ))
    preview = e2e_client.post(
        "/api/solutions/import-workspace/preview",
        headers={k: v for k, v in platform_admin.headers.items() if k.lower() != "content-type"},
        files={"file": ("thin-bundle.zip", archive, "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    storage = WorkspaceBundleStorage(preview.json()["preview_token"])
    metadata = await storage.load_metadata()
    preview_id = UUID(preview.json()["preview_token"])
    payload = WorkspaceBundleImportPayload(
        preview_id=preview_id, package_sha256=preview.json()["package_sha256"], decisions=[]
    )
    async with async_session_factory() as db:
        # Use the same canonical durable enqueue service as the endpoint, then
        # bind the staged preview to this exact job before the physical runner.
        job, reused = await enqueue_platform_job(
            db, WORKSPACE_BUNDLE_IMPORT_DEFINITION, payload,
            dedupe_key=f"{metadata['requested_by']}:{preview_id}",
            resource_lock_key="workspace:mutation", priority=500, organization_id=None,
            requested_by_user_id=UUID(metadata["requested_by"]),
            requested_by_email=platform_admin.email, requested_by_name=platform_admin.name,
            resource_type="workspace", resource_id="bundle-import", title="Thin workspace bundle import",
            action_url="/solutions",
        )
        assert not reused
        metadata["platform_job_id"] = str(job.id)
        await storage.stage_metadata(metadata)
        job_id = job.id
        await db.commit()
    try:
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, "workspace.bundle_import")
        assert job["result"] is not None and str(workflow_id) in job["result"]["imported_entity_ids"]
        assert await RepoStorage().read(path) == source.encode()
    finally:
        await WorkspaceBundleStorage(preview.json()["preview_token"]).delete()
        await RepoStorage().delete(path)
        async with async_session_factory() as db:
            await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await db.commit()
        await _delete_job(async_session_factory, job_id)


async def test_summary_backfill_runs_real_handler_and_summary_child(async_session_factory, e2e_client, platform_admin):
    """The parent must defer only after handing one real summary to the worker."""
    suffix = uuid4().hex[:8]
    connection_id, profile_id, agent_id, run_id, backfill_id = (uuid4() for _ in range(5))
    original_assignments: dict[str, UUID] = {}
    try:
        # The scheduler-fixtures service is the deterministic local OpenAI-compatible provider.
        async with async_session_factory() as db:
            original_assignments = dict((await db.execute(select(
                AIModelAssignment.assignment_key, AIModelAssignment.profile_id
            ))).all())
        connection = e2e_client.post("/api/admin/ai/connections", headers=platform_admin.headers, json={
            "name": f"thin-summary-{suffix}", "provider": "openai_compatible", "api_key": "fixture-key",
            "endpoint": "http://scheduler-fixtures:8080/v1",
        })
        assert connection.status_code == 201, connection.text
        connection_id = UUID(connection.json()["id"])
        profile = e2e_client.post("/api/admin/ai/profiles", headers=platform_admin.headers, json={
            "name": f"thin-summary-profile-{suffix}", "connection_id": str(connection_id), "model": "fixture-summary",
        })
        assert profile.status_code == 201, profile.text
        profile_id = UUID(profile.json()["id"])
        async with async_session_factory() as db:
            configured = await db.get(AIModelProfile, profile_id)
            assert configured is not None
            configured.openai_transport = "chat_completions"
            assignment = await db.get(AIModelAssignment, "summarization")
            if assignment is None:
                db.add(AIModelAssignment(assignment_key="summarization", profile_id=profile_id))
            else:
                assignment.profile_id = profile_id
            db.add(Agent(id=agent_id, name=f"thin-summary-agent-{suffix}", description="thin summary",
                         system_prompt="test", channels=[], access_level="authenticated", is_active=True,
                         knowledge_sources=[], system_tools=[], created_by="thin-misc"))
            db.add(SummaryBackfillJob(id=backfill_id, requested_by=platform_admin.user_id, agent_id=agent_id,
                                      status="running", total=1, estimated_cost_usd=Decimal("0")))
            db.add(AgentRun(id=run_id, agent_id=agent_id, trigger_type="thin-misc", status="completed",
                            summary_status="pending", summary_backfill_job_id=backfill_id,
                            input={"request": "summarize thin fixture"}, output={"text": "completed"},
                            completed_at=datetime.now(UTC)))
            await db.commit()
        job_id = await _enqueue(
            async_session_factory, platform_admin, SUMMARY_BACKFILL_DEFINITION,
            SummaryBackfillPayload(backfill_job_id=backfill_id, run_ids=[run_id]),
            label="summary-backfill", job_id=backfill_id,
        )
        wait_for_job(e2e_client, platform_admin.headers, job_id, "agent.summary_backfill")
        async with async_session_factory() as db:
            run = await db.get(AgentRun, run_id)
            backfill = await db.get(SummaryBackfillJob, backfill_id)
            assert run is not None and run.summary_status == "completed"
            assert backfill is not None and (backfill.succeeded, backfill.failed) == (1, 0)
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(WorkDispatch).where(WorkDispatch.subject_id == run_id))
            await db.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await db.execute(delete(SummaryBackfillJob).where(SummaryBackfillJob.id == backfill_id))
            await db.execute(delete(PlatformJob).where(PlatformJob.id == backfill_id))
            await db.execute(delete(Agent).where(Agent.id == agent_id))
            assignments = (await db.execute(select(AIModelAssignment).where(
                AIModelAssignment.profile_id == profile_id
            ))).scalars().all()
            for assignment in assignments:
                if assignment.assignment_key in original_assignments:
                    assignment.profile_id = original_assignments[assignment.assignment_key]
                else:
                    await db.delete(assignment)
            await db.flush()
            await db.execute(delete(AIModelProfile).where(AIModelProfile.id == profile_id))
            await db.execute(delete(AIProviderConnection).where(AIProviderConnection.id == connection_id))
            await db.commit()
        # The backfill id is the parent job id, so it is deleted above.
