"""Engine-local transport for execution reads.

Covers the acceptance surface that does not need a forked child:

- the parent dispatcher calls the shared ``shared.sdk_execution_reads``
  service with a parent-derived token-equivalent user (engine superuser
  vs service non-superuser — never child fields) and maps workflow
  filters, execution filters and pagination, pending-execution detail,
  hidden cross-org 404/403 outcomes, and malformed frames to HTTP-style
  statuses;
- the child transport performs real list/get round trips with zero HTTP
  requests and no silent HTTP fallback;
- the SDK facades map local results to the public surface
  (``WorkflowMetadata`` list, ``ExecutionList`` with continuation
  token, ``ValueError``/``PermissionError`` on get) and preserve the
  existing ``workflow_id``-wins and limit-clamp behavior;
- external callers (no transport) keep the HTTP path unchanged.
"""

from __future__ import annotations

import contextlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._local_transport import (
    OP_EXECUTIONS_GET,
    OP_EXECUTIONS_LIST,
    OP_WORKFLOWS_LIST,
)
from src.models.enums import ExecutionStatus


def test_sdk_workflow_metadata_accepts_server_parameter_list():
    from bifrost.models import WorkflowMetadata as SdkWorkflowMetadata
    from src.models.contracts.workflows import (
        WorkflowMetadata as ServerWorkflowMetadata,
        WorkflowParameter,
    )

    server = ServerWorkflowMetadata(
        id=str(uuid4()),
        name="parameterized",
        parameters=[WorkflowParameter(name="count", type="int", required=True)],
        created_at=datetime.now(timezone.utc),
    )
    parsed = SdkWorkflowMetadata.model_validate(server.model_dump(mode="json"))
    assert parsed.parameters[0]["name"] == "count"


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Exercise real commits while rolling back seeded rows after each test."""
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(org_id=None, **kwargs):
    data = {
        "organization": {"id": str(org_id)} if org_id is not None else None,
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", "exec-1"),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = str(kwargs["solution_id"])
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _engine_principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(org_id, **kwargs))


def _service_principal(org_id, **kwargs):
    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    return _engine_principal(
        org_id,
        service={"service_id": service_id, "attempt_id": attempt_id},
        execution_id=attempt_id,
    )


def _reads_user(principal):
    from src.services.execution.sdk_local_dispatch import (
        _workflow_user_for_principal,
    )

    return _workflow_user_for_principal(principal)


async def _seed_org(db_session, **kwargs):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=kwargs.get("name", f"sdk-reads-org-{uuid4().hex[:8]}"),
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
        organization_id=org_id,
        is_superuser=is_superuser,
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
        access_level=kwargs.get("access_level", "authenticated"),
        cache_ttl_seconds=kwargs.get("cache_ttl_seconds", 0),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_execution(db_session, name, *, user_id, user_name="Seed User", **kwargs):
    from src.models.orm.executions import Execution as ExecutionModel

    row = ExecutionModel(
        workflow_name=name,
        workflow_id=kwargs.get("workflow_id"),
        status=kwargs.get("status", ExecutionStatus.SUCCESS),
        parameters=kwargs.get("parameters", {}),
        result=kwargs.get("result"),
        variables=kwargs.get("variables"),
        executed_by=user_id,
        executed_by_name=user_name,
        organization_id=kwargs.get("org_id"),
        started_at=kwargs.get("started_at"),
        completed_at=kwargs.get("completed_at"),
        created_at=kwargs.get("created_at"),
        is_local_execution=kwargs.get("is_local_execution", False),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _dispatch(frame, principal, db_session):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


def _list_frame(op, frame_id="reads-1", **fields):
    return {"v": 1, "id": frame_id, "op": op, **fields}


def _sdk_summary(**overrides):
    """One SDK-shaped execution dict, as the local parent returns it."""
    data = {
        "execution_id": str(uuid4()),
        "workflow_name": "wf",
        "org_id": None,
        "form_id": None,
        "executed_by": str(uuid4()),
        "executed_by_name": "User",
        "status": "Success",
        "result_type": None,
        "error_message": None,
        "duration_ms": 3,
        "started_at": None,
        "completed_at": None,
        "scheduled_at": None,
        "created_at": None,
        "session_id": None,
        "peak_memory_bytes": None,
        "process_rss_bytes": None,
        "cpu_total_seconds": None,
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
class TestWorkflowsListDispatch:
    async def test_lists_seeded_workflows_with_metadata(self, db_session):
        org = await _seed_org(db_session)
        stem = uuid4().hex[:8]
        await _seed_workflow(db_session, f"reads-global-{stem}")
        await _seed_workflow(db_session, f"reads-org-{stem}", org_id=org.id)
        await _seed_workflow(db_session, f"reads-inactive-{stem}", is_active=False)
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST), principal, db_session
        )

        assert response["ok"] is True, response
        names = {w["name"] for w in response["result"]["items"]}
        assert f"reads-global-{stem}" in names
        assert f"reads-org-{stem}" in names
        assert f"reads-inactive-{stem}" not in names
        for item in response["result"]["items"]:
            assert "used_by_count" in item
            assert "role_ids" in item

    async def test_type_and_scope_filters(self, db_session):
        org = await _seed_org(db_session)
        stem = uuid4().hex[:8]
        await _seed_workflow(db_session, f"reads-tool-{stem}", type="tool")
        await _seed_workflow(db_session, f"reads-wf-{stem}", type="workflow")
        principal = _engine_principal()

        typed = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST, "t-1", type="tool"), principal, db_session
        )
        assert typed["ok"] is True, typed
        assert {w["name"] for w in typed["result"]["items"]} == {f"reads-tool-{stem}"}

        scoped = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST, "t-2", scope=str(org.id)),
            principal,
            db_session,
        )
        assert scoped["ok"] is True, scoped
        for item in scoped["result"]["items"]:
            assert item["organization_id"] == str(org.id)

    async def test_filter_by_agent(self, db_session):
        from src.models.orm.agents import Agent as AgentModel
        from src.models.orm.agents import AgentTool as AgentToolModel

        stem = uuid4().hex[:8]
        wf = await _seed_workflow(db_session, f"reads-agent-wf-{stem}")
        await _seed_workflow(db_session, f"reads-other-wf-{stem}")
        agent = AgentModel(
            name=f"reads-agent-{stem}",
            system_prompt="test prompt",
            organization_id=None,
            is_active=True,
            created_by="sdk-reads-test",
        )
        db_session.add(agent)
        await db_session.flush()
        db_session.add(AgentToolModel(agent_id=agent.id, workflow_id=wf.id))
        await db_session.flush()
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST, "a-1", filter_by_agent=str(agent.id)),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert [w["name"] for w in response["result"]["items"]] == [wf.name]

    async def test_service_principal_is_403(self, db_session):
        org = await _seed_org(db_session)
        principal = _service_principal(org.id)

        response = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST), principal, db_session
        )

        assert response["ok"] is False
        assert response["status"] == 403

    async def test_malformed_scope_is_400(self, db_session):
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST, "bad-scope", scope="not-a-scope"),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 400

    async def test_malformed_filter_is_422(self, db_session):
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST, "bad-f", filter_by_form="not-a-uuid"),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 422

        bad_tool = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST, "bad-t", is_tool="maybe"),
            principal,
            db_session,
        )
        assert bad_tool["ok"] is False
        assert bad_tool["status"] == 422

    async def test_new_op_is_allowlisted(self):
        from src.services.execution.sdk_local_dispatch import SDK_CHANNEL_ALLOWED_OPS

        assert OP_WORKFLOWS_LIST in SDK_CHANNEL_ALLOWED_OPS
        assert OP_EXECUTIONS_LIST in SDK_CHANNEL_ALLOWED_OPS
        assert OP_EXECUTIONS_GET in SDK_CHANNEL_ALLOWED_OPS

    async def test_http_and_local_list_agree(self, db_session):
        from shared.sdk_execution_reads import list_sdk_workflows

        stem = uuid4().hex[:8]
        await _seed_workflow(db_session, f"reads-parity-{stem}")
        principal = _engine_principal()
        user = _reads_user(principal)

        expected = await list_sdk_workflows(db_session, user)
        local = await _dispatch(
            _list_frame(OP_WORKFLOWS_LIST, "parity-1"), principal, db_session
        )

        assert local["ok"] is True, local
        assert local["result"]["items"] == [
            w.model_dump(mode="json") for w in expected
        ]

    async def test_forged_scope_claims_are_ignored(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(
                OP_WORKFLOWS_LIST,
                "forge-1",
                scope=str(org.id),
                caller_org_id=str(uuid4()),
                is_superuser=True,
                actor_email="attacker@evil.local",
            ),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        for item in response["result"]["items"]:
            assert item["organization_id"] == str(org.id)


@pytest.mark.asyncio
class TestExecutionsListDispatch:
    async def _seed_history(self, db_session, org=None):
        user = await _seed_user(
            db_session,
            org_id=org.id if org else None,
            # Users without an org must be superusers
            # (ck_users_org_requires_superuser).
            is_superuser=org is None,
        )
        wf = await _seed_workflow(db_session, f"reads-hist-{uuid4().hex[:8]}")
        base = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for i, status in enumerate(
            [ExecutionStatus.SUCCESS, ExecutionStatus.FAILED, ExecutionStatus.SUCCESS]
        ):
            rows.append(
                await _seed_execution(
                    db_session,
                    wf.name,
                    user_id=user.id,
                    org_id=org.id if org else None,
                    workflow_id=wf.id,
                    status=status,
                    started_at=base - timedelta(hours=i),
                    completed_at=base - timedelta(hours=i) + timedelta(minutes=5),
                    created_at=base - timedelta(hours=i),
                )
            )
        local_row = await _seed_execution(
            db_session,
            wf.name,
            user_id=user.id,
            org_id=org.id if org else None,
            workflow_id=wf.id,
            status=ExecutionStatus.SUCCESS,
            started_at=base - timedelta(hours=9),
            is_local_execution=True,
        )
        return user, wf, rows, local_row

    async def test_lists_with_default_exclude_local(self, db_session):
        _, _, rows, _ = await self._seed_history(db_session)
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST, "el-1", workflow_name=rows[0].workflow_name
            ),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert len(response["result"]["executions"]) == 3
        assert response["result"]["continuation_token"] is None
        # List responses are summaries: payloads stay behind executions.get.
        for summary in response["result"]["executions"]:
            assert "input_data" not in summary
            assert "result" not in summary
            assert "logs" not in summary
            assert "variables" not in summary

    async def test_status_and_date_filters(self, db_session):
        user, wf, rows, _ = await self._seed_history(db_session)
        principal = _engine_principal()
        start = (rows[0].started_at - timedelta(minutes=30)).isoformat()

        failed = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST,
                "el-f",
                workflow_name=wf.name,
                status="Failed",
            ),
            principal,
            db_session,
        )
        assert failed["ok"] is True, failed
        assert len(failed["result"]["executions"]) == 1
        assert failed["result"]["executions"][0]["execution_id"] == str(rows[1].id)

        dated = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST,
                "el-d",
                workflow_name=wf.name,
                start_date=start,
            ),
            principal,
            db_session,
        )
        assert dated["ok"] is True, dated
        assert len(dated["result"]["executions"]) == 1
        assert dated["result"]["executions"][0]["execution_id"] == str(rows[0].id)

    async def test_workflow_id_wins_over_name(self, db_session):
        _, wf, rows, _ = await self._seed_history(db_session)
        other = await _seed_workflow(db_session, f"reads-other-{uuid4().hex[:8]}")
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST,
                "el-w",
                workflow_name=other.name,
                workflow_id=str(wf.id),
            ),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert len(response["result"]["executions"]) == 3

    async def test_keyset_pagination(self, db_session):
        _, wf, rows, _ = await self._seed_history(db_session)
        principal = _engine_principal()

        first = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST, "pg-1", workflow_name=wf.name, limit=2
            ),
            principal,
            db_session,
        )
        assert first["ok"] is True, first
        assert len(first["result"]["executions"]) == 2
        token = first["result"]["continuation_token"]
        assert token

        second = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST,
                "pg-2",
                workflow_name=wf.name,
                limit=2,
                continuation_token=token,
            ),
            principal,
            db_session,
        )
        assert second["ok"] is True, second
        assert len(second["result"]["executions"]) == 1
        assert second["result"]["continuation_token"] is None

        seen = {
            e["execution_id"]
            for e in first["result"]["executions"] + second["result"]["executions"]
        }
        assert seen == {str(r.id) for r in rows}

    async def test_legacy_offset_token(self, db_session):
        _, wf, rows, _ = await self._seed_history(db_session)
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST,
                "pg-off",
                workflow_name=wf.name,
                limit=2,
                continuation_token="1",
            ),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert len(response["result"]["executions"]) == 2

    async def test_scope_filters_to_org(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        await self._seed_history(db_session, org=org_a)
        _, wf_b, _, _ = await self._seed_history(db_session, org=org_b)
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(OP_EXECUTIONS_LIST, "el-s", scope=str(org_b.id)),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert {e["workflow_name"] for e in response["result"]["executions"]} == {
            wf_b.name
        }

    async def test_service_principal_sees_only_own_rows(self, db_session):
        from src.core.constants import SYSTEM_USER_UUID

        org = await _seed_org(db_session)
        other = await _seed_user(db_session, org_id=org.id)
        await _seed_execution(
            db_session, f"reads-foreign-{uuid4().hex[:8]}", user_id=other.id
        )
        own = await _seed_execution(
            db_session,
            f"reads-own-{uuid4().hex[:8]}",
            user_id=SYSTEM_USER_UUID,
            org_id=org.id,
        )
        principal = _service_principal(org.id)

        response = await _dispatch(
            _list_frame(OP_EXECUTIONS_LIST), principal, db_session
        )

        assert response["ok"] is True, response
        assert [e["execution_id"] for e in response["result"]["executions"]] == [
            str(own.id)
        ]

    async def test_malformed_values_are_422(self, db_session):
        principal = _engine_principal()
        cases = [
            ("bad-wid", {"workflow_id": "not-a-uuid"}),
            ("bad-start", {"start_date": "not-a-date"}),
            ("bad-end", {"end_date": "2026-13-99"}),
            ("bad-bool", {"exclude_local": "sometimes"}),
            ("bad-limit-lo", {"limit": 0}),
            ("bad-limit-hi", {"limit": 1001}),
            ("bad-limit-type", {"limit": "many"}),
            ("bad-token", {"continuation_token": "!!!"}),
            ("bad-token-neg", {"continuation_token": "-3"}),
        ]
        for frame_id, fields in cases:
            response = await _dispatch(
                _list_frame(OP_EXECUTIONS_LIST, frame_id, **fields),
                principal,
                db_session,
            )
            assert response["ok"] is False, (frame_id, response)
            assert response["status"] == 422, (frame_id, response)

    async def test_malformed_scope_is_422(self, db_session):
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(OP_EXECUTIONS_LIST, "bad-scope", scope="nope"),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 422

    async def test_http_and_local_list_agree(self, db_session):
        from shared.sdk_execution_reads import list_sdk_executions

        _, wf, _, _ = await self._seed_history(db_session)
        principal = _engine_principal()
        user = _reads_user(principal)

        expected, expected_token = await list_sdk_executions(
            db_session, user, workflow_name=wf.name, limit=25
        )
        local = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_LIST, "parity-l", workflow_name=wf.name, limit=25
            ),
            principal,
            db_session,
        )

        assert local["ok"] is True, local
        assert local["result"]["executions"] == [
            s.model_dump(mode="json") for s in expected
        ]
        assert local["result"]["continuation_token"] == expected_token


@pytest.mark.asyncio
class TestExecutionsGetDispatch:
    async def test_get_completed_row_with_admin_fields(self, db_session):
        from src.models.orm.ai_usage import AIUsage
        from src.models.orm.executions import ExecutionLog as ExecutionLogORM

        user = await _seed_user(db_session, is_superuser=True)
        row = await _seed_execution(
            db_session,
            f"reads-get-{uuid4().hex[:8]}",
            user_id=user.id,
            status=ExecutionStatus.SUCCESS,
            result={"ok": True},
            variables={"secret": "shh"},
        )
        db_session.add(
            ExecutionLogORM(
                execution_id=row.id,
                sequence=0,
                level="info",
                message="done",
                timestamp=datetime.now(timezone.utc),
            )
        )
        db_session.add(
            AIUsage(
                execution_id=row.id,
                provider="test",
                model="test-model",
                input_tokens=10,
                output_tokens=5,
                sequence=0,
            )
        )
        await db_session.flush()
        principal = _engine_principal()

        response = await _dispatch(
            _list_frame(OP_EXECUTIONS_GET, "get-1", execution_id=str(row.id)),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        detail = response["result"]
        assert detail["execution_id"] == str(row.id)
        assert detail["result"] == {"ok": True}
        assert detail["variables"] == {"secret": "shh"}
        assert detail["logs"] and detail["logs"][0]["message"] == "done"
        assert detail["ai_totals"]["total_input_tokens"] == 10

    async def test_service_principal_hides_admin_fields(self, db_session):
        from src.core.constants import SYSTEM_USER_UUID

        org = await _seed_org(db_session)
        row = await _seed_execution(
            db_session,
            f"reads-get-svc-{uuid4().hex[:8]}",
            user_id=SYSTEM_USER_UUID,
            org_id=org.id,
            variables={"secret": "shh"},
        )
        principal = _service_principal(org.id)

        response = await _dispatch(
            _list_frame(OP_EXECUTIONS_GET, "get-svc", execution_id=str(row.id)),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert response["result"]["variables"] is None
        assert response["result"]["execution_context"] is None

    async def test_missing_is_404_and_foreign_is_403(self, db_session):
        org = await _seed_org(db_session)
        other = await _seed_user(db_session, org_id=org.id)
        foreign = await _seed_execution(
            db_session, f"reads-foreign-{uuid4().hex[:8]}", user_id=other.id
        )
        service = _service_principal(org.id)

        missing = await _dispatch(
            _list_frame(OP_EXECUTIONS_GET, "get-404", execution_id=str(uuid4())),
            service,
            db_session,
        )
        assert missing["ok"] is False
        assert missing["status"] == 404

        denied = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_GET, "get-403", execution_id=str(foreign.id)
            ),
            service,
            db_session,
        )
        assert denied["ok"] is False
        assert denied["status"] == 403

        # The engine superuser sees the same foreign row: no 404/403 leak.
        engine = _engine_principal()
        visible = await _dispatch(
            _list_frame(
                OP_EXECUTIONS_GET, "get-ok", execution_id=str(foreign.id)
            ),
            engine,
            db_session,
        )
        assert visible["ok"] is True, visible

    async def test_malformed_id_is_422(self, db_session):
        principal = _engine_principal()

        missing = await _dispatch(
            {"v": 1, "id": "get-bad-1", "op": OP_EXECUTIONS_GET},
            principal,
            db_session,
        )
        assert missing["ok"] is False
        assert missing["status"] == 422

        malformed = await _dispatch(
            _list_frame(OP_EXECUTIONS_GET, "get-bad-2", execution_id="nope"),
            principal,
            db_session,
        )
        assert malformed["ok"] is False
        assert malformed["status"] == 422

    async def test_pending_fallback_flows_through_local(self, db_session):
        from src.models import WorkflowExecution

        execution_id = uuid4()
        pending = WorkflowExecution(
            execution_id=str(execution_id),
            workflow_name="Pending execution",
            workflow_id=None,
            org_id=None,
            org_name="Global",
            form_id=None,
            executed_by=str(uuid4()),
            executed_by_name="Pending User",
            executed_by_email=None,
            status=ExecutionStatus.PENDING,
            input_data={},
            result=None,
            logs=[],
            started_at=None,
            completed_at=None,
        )
        principal = _engine_principal()

        with patch(
            "shared.pending_execution.get_pending_execution_fallback",
            new=AsyncMock(return_value=(pending, None)),
        ):
            response = await _dispatch(
                _list_frame(
                    OP_EXECUTIONS_GET, "get-pending", execution_id=str(execution_id)
                ),
                principal,
                db_session,
            )

        assert response["ok"] is True, response
        assert response["result"]["execution_id"] == str(execution_id)
        assert response["result"]["status"] == ExecutionStatus.PENDING.value

    async def test_pending_forbidden_is_403(self, db_session):
        principal = _engine_principal()

        with patch(
            "shared.pending_execution.get_pending_execution_fallback",
            new=AsyncMock(return_value=(None, "Forbidden")),
        ):
            response = await _dispatch(
                _list_frame(
                    OP_EXECUTIONS_GET, "get-pf", execution_id=str(uuid4())
                ),
                principal,
                db_session,
            )

        assert response["ok"] is False
        assert response["status"] == 403

    async def test_http_and_local_get_agree(self, db_session):
        from shared.sdk_execution_reads import get_sdk_execution

        user = await _seed_user(db_session, is_superuser=True)
        row = await _seed_execution(
            db_session, f"reads-parity-{uuid4().hex[:8]}", user_id=user.id
        )
        principal = _engine_principal()
        service_user = _reads_user(principal)

        expected = await get_sdk_execution(db_session, service_user, row.id)
        local = await _dispatch(
            _list_frame(OP_EXECUTIONS_GET, "parity-g", execution_id=str(row.id)),
            principal,
            db_session,
        )

        assert local["ok"] is True, local
        assert local["result"] == expected.model_dump(mode="json")


@pytest.mark.asyncio
class TestFacadeLocalMapping:
    def _install_fake_transport(self, monkeypatch, fake, module):
        import bifrost._local_transport as local_transport

        monkeypatch.setattr(local_transport, "_installed", fake)

        def _dead(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        monkeypatch.setattr(sys.modules[module], "get_client", _dead)

    async def test_workflows_list_uses_local(self, monkeypatch):
        from bifrost.workflows import workflows

        class FakeTransport:
            async def call_workflows_list(self, **kwargs):
                assert kwargs == {}
                return [
                    {
                        "id": str(uuid4()),
                        "name": "local-wf",
                        "description": None,
                        "category": None,
                        "tags": [],
                        "parameters": [],
                        "execution_mode": "sync",
                        "timeout_seconds": 60,
                        "retry_policy": None,
                        "endpoint_enabled": False,
                        "allowed_methods": None,
                        "disable_global_key": False,
                        "public_endpoint": False,
                        "is_tool": False,
                        "tool_description": None,
                        "time_saved": None,
                        "source_file_path": None,
                        "relative_file_path": None,
                    }
                ]

        self._install_fake_transport(monkeypatch, FakeTransport(), "bifrost.workflows")
        result = await workflows.list()

        assert [w.name for w in result] == ["local-wf"]

    async def test_executions_list_forwards_params_and_token(self, monkeypatch):
        from bifrost.executions import executions

        seen = {}

        class FakeTransport:
            async def call_executions_list(self, **kwargs):
                seen.update(kwargs)
                return {
                    "executions": [_sdk_summary()],
                    "continuation_token": "tok-1",
                }

        self._install_fake_transport(
            monkeypatch, FakeTransport(), "bifrost.executions"
        )
        result = await executions.list(
            workflow_id=str(uuid4()),
            workflow_name="ignored",
            status="Success",
            exclude_local=False,
            limit=5000,
            continuation_token="tok-0",
        )

        assert seen["workflow_name"] is None
        assert seen["status"] == "Success"
        assert seen["exclude_local"] is False
        assert seen["limit"] == 1000
        assert seen["continuation_token"] == "tok-0"
        assert len(result) == 1
        assert result.continuation_token == "tok-1"
        assert result[0].status == "Success"

    async def test_executions_list_defaults(self, monkeypatch):
        from bifrost.executions import executions

        seen = {}

        class FakeTransport:
            async def call_executions_list(self, **kwargs):
                seen.update(kwargs)
                return {"executions": [], "continuation_token": None}

        self._install_fake_transport(
            monkeypatch, FakeTransport(), "bifrost.executions"
        )
        result = await executions.list()

        assert seen["exclude_local"] is None
        assert seen["limit"] == 50
        assert result == []
        assert result.continuation_token is None

    async def test_executions_get_uses_local(self, monkeypatch):
        from bifrost.executions import executions

        execution_id = str(uuid4())

        class FakeTransport:
            async def call_executions_get(self, exec_id):
                assert exec_id == execution_id
                return _sdk_summary(execution_id=execution_id)

        self._install_fake_transport(
            monkeypatch, FakeTransport(), "bifrost.executions"
        )
        detail = await executions.get(execution_id)

        assert detail.execution_id == execution_id

    async def test_workflows_get_delegates_to_executions_get(self, monkeypatch):
        from bifrost.workflows import workflows

        execution_id = str(uuid4())

        class FakeTransport:
            async def call_executions_get(self, exec_id):
                assert exec_id == execution_id
                return _sdk_summary(execution_id=execution_id)

        # workflows.get delegates to executions.get, so the executions
        # transport (and its dead HTTP client) applies here too.
        self._install_fake_transport(
            monkeypatch, FakeTransport(), "bifrost.executions"
        )
        detail = await workflows.get(execution_id)

        assert detail.execution_id == execution_id

    async def test_local_errors_preserve_public_mapping(self, monkeypatch):
        from bifrost._local_transport import raise_for_local_status
        from bifrost.client import BifrostAPIError
        from bifrost.executions import executions
        from bifrost.workflows import workflows

        class FailList:
            async def call_workflows_list(self, **kwargs):
                raise_for_local_status(403, "denied", OP_WORKFLOWS_LIST)

            async def call_executions_list(self, **kwargs):
                raise_for_local_status(422, "bad scope", OP_EXECUTIONS_LIST)

        self._install_fake_transport(monkeypatch, FailList(), "bifrost.workflows")
        with pytest.raises(BifrostAPIError) as exc_info:
            await workflows.list()
        assert exc_info.value.response.status_code == 403

        self._install_fake_transport(monkeypatch, FailList(), "bifrost.executions")
        with pytest.raises(BifrostAPIError) as exc_info:
            await executions.list()
        assert exc_info.value.response.status_code == 422

        class FailGet:
            async def call_executions_get(self, exec_id):
                raise_for_local_status(self.status, "denied", OP_EXECUTIONS_GET)

        for status, expected in ((404, ValueError), (403, PermissionError)):
            FailGet.status = status
            self._install_fake_transport(
                monkeypatch, FailGet(), "bifrost.executions"
            )
            with pytest.raises(expected):
                await executions.get(str(uuid4()))

        class FailGetOther:
            async def call_executions_get(self, exec_id):
                raise_for_local_status(500, "boom", OP_EXECUTIONS_GET)

        self._install_fake_transport(
            monkeypatch, FailGetOther(), "bifrost.executions"
        )
        with pytest.raises(BifrostAPIError):
            await executions.get(str(uuid4()))

    async def test_external_callers_keep_http(self, monkeypatch):
        import bifrost._local_transport as local_transport
        from bifrost.executions import executions
        from bifrost.workflows import workflows

        monkeypatch.setattr(local_transport, "_installed", None)

        wf_response = MagicMock()
        wf_response.status_code = 200
        wf_response.json = lambda: []
        wf_response.headers = {}
        fake_wf = MagicMock()
        fake_wf.get = AsyncMock(return_value=wf_response)
        monkeypatch.setattr(
            sys.modules["bifrost.workflows"], "get_client", lambda: fake_wf
        )
        assert await workflows.list() == []
        fake_wf.get.assert_awaited_once_with("/api/workflows")

        list_response = MagicMock()
        list_response.status_code = 200
        list_response.json = lambda: {"executions": [], "continuation_token": None}
        list_response.headers = {}
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json = lambda: _sdk_summary()
        get_response.headers = {}
        fake_ex = MagicMock()
        fake_ex.get = AsyncMock(side_effect=[list_response, get_response])
        monkeypatch.setattr(
            sys.modules["bifrost.executions"], "get_client", lambda: fake_ex
        )
        listed = await executions.list()
        assert listed == []
        assert listed.continuation_token is None
        detail = await executions.get(str(uuid4()))
        assert detail.workflow_name == "wf"
