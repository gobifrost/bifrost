"""Legacy global surfaces resolve stored roles before external work."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.contracts.agent_runs import BackfillSummariesRequest
from src.models.orm.forms import Form
from src.models.orm.agent_runs import AgentRun
from src.models.orm.metrics import ExecutionMetricsDaily
from src.models.orm.organizations import Organization
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.product_updates import ProductUpdateReceipt
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.models.orm.workflows import Workflow
from src.routers import agent_runs, decorator_properties, dependencies, memory, product_updates, roi_reports
from src.models.contracts.product_updates import ProductUpdatesReceiptRequest
from src.services.authorization.enforce import load_caller
from src.services.dependency_graph import compute_relationship_availability
from src.services.platform_job_access import PlatformJobAccessDenied, require_platform_job_access


async def _person(
    db,
    *permissions,
    global_grant=False,
    actor_flag=False,
    boundary_organization_id=None,
):
    home, foreign = [Organization(name=name, created_by="test") for name in ("Contoso", "Fabrikam")]
    db.add_all([home, foreign])
    await db.flush()
    user = User(
        email=f"legacy-global-{uuid4().hex}@contoso.example",
        organization_id=home.id,
        base_role_id=USER_ROLE_ID,
        is_superuser=actor_flag,
    )
    role = Role(name=f"Contoso Legacy Global {uuid4().hex}", created_by="test")
    db.add_all([user, role])
    await db.flush()
    db.add_all([
        UserRole(user_id=user.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(
            user_id=user.id,
            role_id=role.id,
            kind="platform" if global_grant else "organization",
            organization_id=None if global_grant else (boundary_organization_id or home.id),
        ),
        *(RolePermission(role_id=role.id, permission=permission) for permission in permissions),
    ])
    await db.flush()
    principal = UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=home.id,
        is_superuser=actor_flag,
    )
    return await load_caller(db, principal), principal, user, role, home, foreign


@pytest_asyncio.fixture
async def authority(db_session, async_session_factory):
    owned = []

    async def make(*permissions, global_grant=False, actor_flag=False, boundary_organization_id=None):
        caller, principal, user, role, home, foreign = await _person(
            db_session,
            *permissions,
            global_grant=global_grant,
            actor_flag=actor_flag,
            boundary_organization_id=boundary_organization_id,
        )
        owned.append((user.id, role.id, home.id, foreign.id))
        return caller, principal, home, foreign

    try:
        yield make
    finally:
        await db_session.rollback()
        async with async_session_factory() as cleanup:
            for user_id, role_id, home_id, foreign_id in owned:
                await cleanup.execute(delete(ProductUpdateReceipt).where(ProductUpdateReceipt.admin_id == user_id))
                await cleanup.execute(delete(PlatformJob).where(PlatformJob.organization_id.in_((home_id, foreign_id))))
                await cleanup.execute(delete(Form).where(Form.organization_id.in_((home_id, foreign_id))))
                await cleanup.execute(delete(Workflow).where(Workflow.organization_id.in_((home_id, foreign_id))))
                await cleanup.execute(delete(User).where(User.id == user_id))
                await cleanup.execute(delete(Role).where(Role.id == role_id))
                await cleanup.execute(delete(Organization).where(Organization.id.in_((home_id, foreign_id))))
            await cleanup.commit()


async def test_named_global_grant_admits_dependency_graph_without_actor_admin_flag(db_session, authority):
    caller, principal, home, foreign = await authority("platform.read", global_grant=True)
    assert not principal.is_superuser
    workflow_id = uuid4()
    workflow = Workflow(
        id=workflow_id,
        name=f"contoso_{uuid4().hex}",
        function_name="contoso_workflow",
        path="workflows/contoso.py",
        organization_id=home.id,
    )
    foreign_form = Form(
        name=f"Fabrikam {uuid4().hex}",
        organization_id=foreign.id,
        workflow_id=str(workflow_id),
        created_by="test",
    )
    db_session.add_all([workflow, foreign_form])
    await db_session.flush()

    graph = await dependencies.get_dependency_graph(
        "workflow", workflow.id, db_session, principal, depth=2
    )
    availability = await compute_relationship_availability(
        db_session, caller=caller, workflow_ids=[workflow.id]
    )

    assert [node.id for node in graph.nodes] == [f"workflow:{workflow.id}"]
    assert graph.edges == []
    assert availability == {f"workflow:{workflow.id}": False}


async def test_raw_actor_admin_flag_does_not_read_decorator_source_before_named_grant(db_session, authority, monkeypatch):
    _caller, principal, _home, _foreign = await authority(actor_flag=True)
    storage = Mock(side_effect=AssertionError("storage initialized before named authorization"))
    monkeypatch.setattr(decorator_properties, "FileStorageService", storage)

    with pytest.raises(HTTPException) as error:
        await decorator_properties.get_decorator_properties(
            SimpleNamespace(), principal, path="workflows/contoso.py", db=db_session
        )

    assert error.value.status_code == 403
    storage.assert_not_called()


async def test_summary_backfill_uses_named_readwrite_scope_before_aggregate_work(
    db_session, authority
):
    _reader, reader_principal, home, foreign = await authority("agentruns.read.all")
    _writer, writer_principal, _writer_home, _writer_foreign = await authority(
        "agentruns.readwrite.all", boundary_organization_id=home.id
    )
    db_session.add_all([
        AgentRun(
            trigger_type="api",
            status="completed",
            summary_status="pending",
            org_id=home.id,
        ),
        AgentRun(
            trigger_type="api",
            status="completed",
            summary_status="pending",
            org_id=foreign.id,
        ),
    ])
    await db_session.flush()

    eligible = await agent_runs.get_backfill_eligible(db_session, reader_principal)
    assert eligible.eligible == 1

    request = BackfillSummariesRequest(dry_run=True)
    with pytest.raises(HTTPException) as error:
        await agent_runs.backfill_summaries(request, db_session, reader_principal)
    assert error.value.status_code == 403

    response = await agent_runs.backfill_summaries(request, db_session, writer_principal)
    assert response.eligible == 1
    assert response.queued == 0


async def test_named_global_settings_reader_reaches_memory_settings_without_actor_admin_flag(
    db_session, authority, monkeypatch
):
    _caller, principal, _home, _foreign = await authority("settings.read", global_grant=True)
    service = SimpleNamespace(platform_enabled=AsyncMock(return_value=False))
    monkeypatch.setattr(memory, "_service", Mock(return_value=service))

    response = await memory.get_platform_memory_settings(db_session, principal)

    assert response.enabled is False
    service.platform_enabled.assert_awaited_once()


async def test_platform_job_owner_and_named_read_all_preserve_original_reach_privacy(db_session, authority):
    owner, _owner_principal, home, _foreign = await authority()
    reader, reader_principal, _reader_home, _reader_foreign = await authority(
        "platformjobs.read.all", boundary_organization_id=home.id
    )
    own_job = PlatformJob(
        job_type="workspace.sync",
        payload={},
        organization_id=home.id,
        requested_by_user_id=str(owner.ctx.user_id),
        requested_by_email="owner@contoso.example",
        requested_by_name="Owner",
        title="Contoso sync",
        status="queued",
    )
    other_job = PlatformJob(
        job_type="workspace.sync",
        payload={},
        organization_id=home.id,
        requested_by_user_id=str(uuid4()),
        requested_by_email="other@contoso.example",
        requested_by_name="Other",
        title="Other Contoso sync",
        status="queued",
    )
    db_session.add_all([own_job, other_job])
    await db_session.flush()

    require_platform_job_access(owner, own_job)
    with pytest.raises(PlatformJobAccessDenied):
        require_platform_job_access(owner, other_job)
    require_platform_job_access(reader, other_job)
    assert reader.ctx is not None
    assert reader.ctx.user_id == reader_principal.user_id


async def test_product_update_receipt_is_written_for_the_original_caller(db_session, authority, monkeypatch):
    caller, principal, _home, _foreign = await authority("settings.readwrite", global_grant=True)
    entry_id = uuid4()
    monkeypatch.setattr(product_updates, "get_running_product_updates_bundle", object)
    monkeypatch.setattr(product_updates, "visible_entry_ids", lambda _bundle: {entry_id})

    response = await product_updates.create_product_update_receipts(
        ProductUpdatesReceiptRequest(entry_ids=[entry_id]), principal, db_session
    )

    assert response.seen_entry_ids == [str(entry_id)]
    receipt = await db_session.get(ProductUpdateReceipt, (caller.ctx.user_id, entry_id))
    assert receipt is not None


async def test_org_scoped_reports_grant_filters_omitted_scope_and_refuses_foreign_scope(
    db_session, authority
):
    _caller, principal, home, foreign = await authority("reports.read")
    report_date = date(2026, 10, 1)
    db_session.add_all([
        ExecutionMetricsDaily(
            date=report_date,
            organization_id=home.id,
            execution_count=3,
            success_count=2,
            total_time_saved=30,
            total_value=30,
        ),
        ExecutionMetricsDaily(
            date=report_date,
            organization_id=foreign.id,
            execution_count=99,
            success_count=99,
            total_time_saved=990,
            total_value=990,
        ),
    ])
    await db_session.flush()

    report = await roi_reports.get_roi_summary(
        principal,
        db_session,
        start_date=report_date,
        end_date=report_date,
        scope=None,
    )

    assert report.total_executions == 3
    assert report.total_time_saved == 30
    with pytest.raises(HTTPException) as error:
        await roi_reports.get_roi_summary(
            principal,
            db_session,
            start_date=report_date,
            end_date=report_date,
            scope=str(foreign.id),
        )
    assert error.value.status_code == 403
