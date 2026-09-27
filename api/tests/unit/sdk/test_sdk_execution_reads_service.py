"""Focused unit tests for the SDK execution-reads shared service.

Covers ``shared.sdk_execution_reads`` — the single implementation behind
``workflows.list()`` (``GET /api/workflows``), ``executions.list()``
(``GET /api/executions``), and ``executions.get()`` / ``workflows.get()``
(``GET /api/executions/{id}``):

- scope resolution and error codes (workflows: 400 scope / 403
  non-superuser; executions: 422 scope),
- workflow type/entity filters, used-by counts, and role IDs,
- execution keyset pagination, legacy offset, status match-any,
  workflow_id-wins, and owner-only visibility,
- execution detail shape, admin-only fields, and 403/404 precedence,
- cursor encode/decode round-trip,
- router thin-boundary delegation for the three handlers.

DB-backed via the ``db_session`` fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.sdk_execution_reads import (
    SdkExecutionReadError,
    decode_history_cursor,
    encode_history_cursor,
    get_sdk_execution,
    list_sdk_executions,
    list_sdk_workflows,
)
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus


def _admin(org_id=None, **kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk-reads-admin@test.local"),
        organization_id=org_id,
        name="SDK Reads Admin",
        is_superuser=True,
        is_verified=True,
    )


def _user(org_id, **kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk-reads-user@test.local"),
        organization_id=org_id,
        name="SDK Reads User",
        is_superuser=False,
        is_verified=True,
    )


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-reads-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-reads-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, org_id=None, is_superuser=False):
    from src.models import User as UserORM

    row = UserORM(
        email=f"sdk-reads-{uuid4().hex[:8]}@example.com",
        name="SDK Reads",
        is_superuser=is_superuser,
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_workflow(db_session, name, *, org_id=None, **kwargs):
    from src.models.orm.workflows import Workflow as WorkflowModel

    row = WorkflowModel(
        name=name,
        function_name=kwargs.get("function_name", f"{name}_fn"),
        path=kwargs.get("path", f"workflows/{name}.py"),
        type=kwargs.get("type", "workflow"),
        organization_id=org_id,
        is_active=kwargs.get("is_active", True),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_execution(
    db_session, name, *, user_id, user_name="Seed User", org_id=None, **kwargs
):
    from src.models.orm.executions import Execution as ExecutionModel

    row = ExecutionModel(
        workflow_name=name,
        workflow_id=kwargs.get("workflow_id"),
        status=kwargs.get("status", ExecutionStatus.SUCCESS),
        parameters={},
        executed_by=user_id,
        executed_by_name=user_name,
        organization_id=org_id,
        started_at=kwargs.get("started_at"),
        completed_at=kwargs.get("completed_at"),
        created_at=kwargs.get("created_at"),
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
class TestListSdkWorkflows:
    async def test_superuser_sees_all_active(self, db_session):
        org = await _seed_org(db_session)
        await _seed_workflow(db_session, "reads-global-wf")
        await _seed_workflow(db_session, "reads-org-wf", org_id=org.id)
        await _seed_workflow(db_session, "reads-inactive-wf", is_active=False)

        result = await list_sdk_workflows(db_session, _admin(), scope=None)

        names = {w.name for w in result}
        assert {"reads-global-wf", "reads-org-wf"} <= names
        assert "reads-inactive-wf" not in names

    async def test_global_scope_hides_org_workflows(self, db_session):
        org = await _seed_org(db_session)
        await _seed_workflow(db_session, "reads-scope-global")
        await _seed_workflow(db_session, "reads-scope-org", org_id=org.id)

        result = await list_sdk_workflows(db_session, _admin(), scope="global")

        assert [w.name for w in result if w.name.startswith("reads-scope-")] == [
            "reads-scope-global"
        ]

    async def test_org_scope_pins_to_org(self, db_session):
        org = await _seed_org(db_session)
        await _seed_workflow(db_session, "reads-pin-global")
        await _seed_workflow(db_session, "reads-pin-org", org_id=org.id)

        result = await list_sdk_workflows(
            db_session, _admin(), scope=str(org.id)
        )

        names = [w.name for w in result if w.name.startswith("reads-pin-")]
        assert names == ["reads-pin-org"]

    async def test_invalid_scope_raises_400(self, db_session):
        with pytest.raises(SdkExecutionReadError) as exc_info:
            await list_sdk_workflows(db_session, _admin(), scope="nope")
        assert exc_info.value.status_code == 400

    async def test_non_superuser_denied(self, db_session):
        org = await _seed_org(db_session)
        with pytest.raises(SdkExecutionReadError) as exc_info:
            await list_sdk_workflows(
                db_session, _user(org.id), scope=None
            )
        assert exc_info.value.status_code == 403

    async def test_type_and_legacy_is_tool_filters(self, db_session):
        await _seed_workflow(db_session, "reads-tool-wf", type="tool")
        await _seed_workflow(db_session, "reads-plain-wf", type="workflow")

        by_type = await list_sdk_workflows(db_session, _admin(), type="tool")
        assert "reads-tool-wf" in {w.name for w in by_type}
        assert "reads-plain-wf" not in {w.name for w in by_type}

        by_legacy = await list_sdk_workflows(db_session, _admin(), is_tool=True)
        assert "reads-tool-wf" in {w.name for w in by_legacy}
        assert "reads-plain-wf" not in {w.name for w in by_legacy}

    async def test_used_by_count_and_role_ids(self, db_session):
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.users import Role as RoleModel
        from src.models.orm.workflow_roles import WorkflowRole as WorkflowRoleModel

        wf = await _seed_workflow(db_session, "reads-used-wf")
        form = FormModel(
            name="reads-used-form",
            workflow_id=str(wf.id),
            is_active=True,
            created_by="sdk-reads-test",
        )
        db_session.add(form)
        role = RoleModel(
            name=f"reads-role-{uuid4().hex[:8]}", created_by="sdk-reads-test"
        )
        db_session.add(role)
        await db_session.flush()
        db_session.add(
            WorkflowRoleModel(workflow_id=wf.id, role_id=role.id)
        )
        await db_session.flush()

        result = await list_sdk_workflows(db_session, _admin(), scope=None)

        match = next(w for w in result if w.name == "reads-used-wf")
        assert match.used_by_count >= 1
        assert str(role.id) in match.role_ids

    async def test_filter_by_form_empty_returns_empty(self, db_session):
        await _seed_workflow(db_session, "reads-unrelated-wf")

        result = await list_sdk_workflows(
            db_session, _admin(), filter_by_form=uuid4()
        )

        assert result == []


@pytest.mark.asyncio
class TestListSdkExecutions:
    async def _seed_history(self, db_session, admin_user_id):
        base = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
        wf = await _seed_workflow(db_session, "reads-history-wf")
        rows = []
        for index in range(3):
            rows.append(
                await _seed_execution(
                    db_session,
                    "reads-history-wf",
                    user_id=admin_user_id,
                    workflow_id=wf.id,
                    started_at=base + timedelta(minutes=index),
                    completed_at=base + timedelta(minutes=index, seconds=2),
                    created_at=base + timedelta(minutes=index),
                )
            )
        return wf, rows

    async def test_keyset_pagination_newest_first(self, db_session):
        admin = await _seed_user(db_session, is_superuser=True)
        _, rows = await self._seed_history(db_session, admin.id)

        first, token = await list_sdk_executions(
            db_session,
            _admin(user_id=admin.id),
            workflow_name="reads-history-wf",
            limit=1,
        )
        assert [e.execution_id for e in first] == [str(rows[2].id)]
        assert token is not None

        cursor = decode_history_cursor(token)
        assert cursor is not None
        second, token2 = await list_sdk_executions(
            db_session,
            _admin(user_id=admin.id),
            workflow_name="reads-history-wf",
            limit=1,
            cursor=cursor,
        )
        assert [e.execution_id for e in second] == [str(rows[1].id)]
        assert token2 is not None

    async def test_legacy_offset_still_honored(self, db_session):
        admin = await _seed_user(db_session, is_superuser=True)
        _, rows = await self._seed_history(db_session, admin.id)

        page, _ = await list_sdk_executions(
            db_session,
            _admin(user_id=admin.id),
            workflow_name="reads-history-wf",
            limit=1,
            offset=1,
        )
        assert [e.execution_id for e in page] == [str(rows[1].id)]

    async def test_workflow_id_wins_over_name_and_status_match_any(
        self, db_session
    ):
        admin = await _seed_user(db_session, is_superuser=True)
        wf, _ = await self._seed_history(db_session, admin.id)
        failed = await _seed_execution(
            db_session,
            "reads-history-wf",
            user_id=admin.id,
            workflow_id=wf.id,
            status=ExecutionStatus.FAILED,
            started_at=datetime(2026, 8, 27, 19, 0, tzinfo=timezone.utc),
        )

        filtered, _ = await list_sdk_executions(
            db_session,
            _admin(user_id=admin.id),
            workflow_name="ignored-because-id-wins",
            workflow_id=wf.id,
            status_filter="Failed,Timeout",
            limit=25,
        )
        assert [e.execution_id for e in filtered] == [str(failed.id)]

    async def test_non_superuser_sees_only_own(self, db_session):
        org = await _seed_org(db_session)
        owner = await _seed_user(db_session, org_id=org.id)
        other = await _seed_user(db_session, org_id=org.id)
        own = await _seed_execution(
            db_session, "reads-own-wf", user_id=owner.id, org_id=org.id
        )
        await _seed_execution(
            db_session, "reads-other-wf", user_id=other.id, org_id=org.id
        )

        result, _ = await list_sdk_executions(
            db_session, _user(org.id, user_id=owner.id), limit=25
        )

        assert [e.execution_id for e in result] == [str(own.id)]

    async def test_invalid_scope_raises_422(self, db_session):
        with pytest.raises(SdkExecutionReadError) as exc_info:
            await list_sdk_executions(
                db_session, _admin(), scope="not-a-scope"
            )
        assert exc_info.value.status_code == 422

    async def test_exclude_local_default_true(self, db_session):
        admin = await _seed_user(db_session, is_superuser=True)
        from src.models.orm.executions import Execution as ExecutionModel

        local = ExecutionModel(
            workflow_name="reads-local-wf",
            status=ExecutionStatus.SUCCESS,
            parameters={},
            executed_by=admin.id,
            executed_by_name="Admin",
            is_local_execution=True,
        )
        db_session.add(local)
        await db_session.flush()

        result, _ = await list_sdk_executions(
            db_session,
            _admin(user_id=admin.id),
            workflow_name="reads-local-wf",
            limit=100,
        )
        assert str(local.id) not in {e.execution_id for e in result}

        included, _ = await list_sdk_executions(
            db_session,
            _admin(user_id=admin.id),
            workflow_name="reads-local-wf",
            limit=100,
            exclude_local=False,
        )
        assert str(local.id) in {e.execution_id for e in included}


@pytest.mark.asyncio
class TestGetSdkExecution:
    async def test_admin_gets_full_detail(self, db_session):
        org = await _seed_org(db_session)
        admin = await _seed_user(db_session, is_superuser=True)
        row = await _seed_execution(
            db_session,
            "reads-detail-wf",
            user_id=admin.id,
            org_id=org.id,
        )
        row.variables = {"k": "v"}
        row.execution_context = {"ctx": True}
        await db_session.flush()

        result = await get_sdk_execution(
            db_session, _admin(user_id=admin.id), row.id
        )

        assert result.execution_id == str(row.id)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.variables == {"k": "v"}
        assert result.execution_context == {"ctx": True}
        assert result.org_name == org.name

    async def test_owner_gets_redacted_detail(self, db_session):
        org = await _seed_org(db_session)
        owner = await _seed_user(db_session, org_id=org.id)
        row = await _seed_execution(
            db_session,
            "reads-redacted-wf",
            user_id=owner.id,
            org_id=org.id,
        )
        row.variables = {"k": "v"}
        await db_session.flush()

        result = await get_sdk_execution(
            db_session, _user(org.id, user_id=owner.id), row.id
        )

        assert result.execution_id == str(row.id)
        assert result.variables is None
        assert result.execution_context is None

    async def test_non_owner_denied_before_missing_check(self, db_session):
        org = await _seed_org(db_session)
        owner = await _seed_user(db_session, org_id=org.id)
        stranger = await _seed_user(db_session, org_id=org.id)
        row = await _seed_execution(
            db_session, "reads-denied-wf", user_id=owner.id, org_id=org.id
        )

        with pytest.raises(SdkExecutionReadError) as exc_info:
            await get_sdk_execution(
                db_session, _user(org.id, user_id=stranger.id), row.id
            )
        assert exc_info.value.status_code == 403

    async def test_missing_raises_404(self, db_session):
        with pytest.raises(SdkExecutionReadError) as exc_info:
            await get_sdk_execution(db_session, _admin(), uuid4())
        assert exc_info.value.status_code == 404


class TestHistoryCursor:
    def test_round_trip(self):
        moment = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
        row_id = uuid4()

        assert decode_history_cursor(encode_history_cursor(moment, row_id)) == (
            moment,
            row_id,
        )

    def test_legacy_and_garbage_decode_to_none(self):
        assert decode_history_cursor("42") is None
        assert decode_history_cursor("not-a-token") is None


@pytest.mark.asyncio
class TestRouterBoundaries:
    """Handlers delegate to the shared service and map its errors."""

    async def test_list_workflows_delegates_and_maps_403(self):
        from src.routers.workflows import list_workflows

        principal = _admin()
        sentinel = ["WF"]
        with patch(
            "shared.sdk_execution_reads.list_sdk_workflows",
            new=AsyncMock(return_value=sentinel),
        ) as mock_list:
            result = await list_workflows(
                principal,
                AsyncMock(),
                type="tool",
                is_tool=None,
                scope=None,
                filter_by_form=None,
                filter_by_app=None,
                filter_by_agent=None,
            )
        assert result == sentinel
        mock_list.assert_awaited_once()
        assert mock_list.call_args[1]["type"] == "tool"

        with patch(
            "shared.sdk_execution_reads.list_sdk_workflows",
            new=AsyncMock(side_effect=SdkExecutionReadError(403, "denied")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await list_workflows(
                    principal,
                    AsyncMock(),
                    type=None,
                    is_tool=None,
                    scope=None,
                    filter_by_form=None,
                    filter_by_app=None,
                    filter_by_agent=None,
                )
        assert exc_info.value.status_code == 403

    async def test_list_executions_handler_maps_422(self):
        from src.routers.executions import list_executions

        ctx = SimpleNamespace(user=_admin(), db=AsyncMock())
        request = SimpleNamespace(query_params={})
        with patch(
            "shared.sdk_execution_reads.list_sdk_executions",
            new=AsyncMock(return_value=([], None)),
        ) as mock_list:
            result = await list_executions(
                ctx,
                request,
                scope=None,
                workflowName=None,
                workflowId=None,
                status_filter=None,
                startDate=None,
                endDate=None,
                excludeLocal=True,
                limit=25,
                continuationToken=None,
            )
        assert result.executions == []
        mock_list.assert_awaited_once()
        assert mock_list.call_args[1]["scope"] is None

        with patch(
            "shared.sdk_execution_reads.list_sdk_executions",
            new=AsyncMock(side_effect=SdkExecutionReadError(422, "bad scope")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await list_executions(
                    ctx,
                    request,
                    scope="bad",
                    workflowName=None,
                    workflowId=None,
                    status_filter=None,
                    startDate=None,
                    endDate=None,
                    excludeLocal=True,
                    limit=25,
                    continuationToken=None,
                )
        assert exc_info.value.status_code == 422

    async def test_get_execution_handler_maps_404_and_403(self):
        from src.routers.executions import get_execution

        execution_id = uuid4()
        ctx = SimpleNamespace(user=_admin(), db=AsyncMock())
        with patch(
            "shared.sdk_execution_reads.get_sdk_execution",
            new=AsyncMock(return_value="EXEC"),
        ) as mock_get:
            assert await get_execution(execution_id, ctx) == "EXEC"
        mock_get.assert_awaited_once_with(ctx.db, ctx.user, execution_id)

        with patch(
            "shared.sdk_execution_reads.get_sdk_execution",
            new=AsyncMock(side_effect=SdkExecutionReadError(404, "missing")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_execution(execution_id, ctx)
        assert exc_info.value.status_code == 404

        with patch(
            "shared.sdk_execution_reads.get_sdk_execution",
            new=AsyncMock(side_effect=SdkExecutionReadError(403, "denied")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_execution(execution_id, ctx)
        assert exc_info.value.status_code == 403

    async def test_execution_repository_exposes_no_read_compatibility_shims(self):
        """SDK reads have one public implementation in the shared service."""
        from src.routers.executions import ExecutionRepository

        assert not hasattr(ExecutionRepository, "list_executions")
        assert not hasattr(ExecutionRepository, "get_execution")
        assert not hasattr(ExecutionRepository, "_to_summary")


def test_shared_service_has_no_transport_imports():
    """The shared service must stay callable without FastAPI/router imports."""
    import ast
    from pathlib import Path

    import shared.sdk_execution_reads as service

    tree = ast.parse(Path(service.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "fastapi" not in imported
    assert "routers" not in {m.split(".")[-1] for m in imported}
    assert not any("routers" in (m or "") for m in imported)
