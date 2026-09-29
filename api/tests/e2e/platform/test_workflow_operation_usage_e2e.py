"""E2E: a real engine-token workflow call produces a durable
``workflow_operation_usage`` row after a forced flush (R2a-2 Part 2).

Mirrors the socket-audit-parity pattern in test_sdk_socket_audit_parity.py:
mints a real engine token (this time carrying ``engine_workflow_id``), hits
an SDK endpoint over the worker-local socket, then forces the 15-minute
flush job instead of waiting for the scheduler.
"""

import uuid

import httpx
import pytest


class TestWorkflowOperationUsageE2E:
    @pytest.mark.asyncio
    async def test_engine_workflow_call_produces_usage_row_after_flush(
        self, async_session_factory, org1_user
    ):
        from sqlalchemy import delete, select

        from src.core.security import mint_engine_token
        from src.jobs.schedulers.workflow_operation_usage_flush import (
            flush_workflow_operation_usage,
        )
        from src.models.orm.audit import AuditLog
        from src.models.orm.users import Role as RoleModel
        from src.models.orm.workflow_operation_usage import WorkflowOperationUsage
        from src.models.orm.workflows import Workflow
        from src.services.execution.worker_sdk_http import build_worker_sdk_app
        from tests.helpers.live_execution import create_live_execution, delete_live_execution

        caller_id = org1_user.user_id
        org_id = org1_user.organization_id
        assert caller_id is not None and org_id is not None
        execution_id = uuid.uuid4()
        workflow_id = uuid.uuid4()
        role_name = f"wf-usage-{uuid.uuid4().hex[:8]}"

        async with async_session_factory() as session:
            session.add(
                Workflow(
                    id=workflow_id,
                    name=f"wf-usage-test-{workflow_id.hex[:8]}",
                    function_name="wf_usage_test",
                    type="workflow",
                    path=f"workflows/wf_usage_test_{workflow_id.hex[:8]}.py",
                )
            )
            await session.commit()

        token, _ = mint_engine_token(
            execution_id=str(execution_id),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            caller_user_id=str(caller_id),
            caller_organization_id=str(org_id),
            caller_email=org1_user.email,
            caller_name=org1_user.name,
            engine_workflow_id=str(workflow_id),
        )
        await create_live_execution(async_session_factory, str(execution_id))

        app = build_worker_sdk_app()
        transport = httpx.ASGITransport(app=app)
        role_id: uuid.UUID | None = None
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://bifrost-engine"
            ) as client:
                response = await client.post(
                    "/api/roles",
                    json={"name": role_name, "description": "workflow usage e2e"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert response.status_code == 201, response.text
            role_id = uuid.UUID(response.json()["id"])

            upserted = await flush_workflow_operation_usage()
            assert upserted >= 1

            async with async_session_factory() as session:
                rows = (
                    await session.execute(
                        select(WorkflowOperationUsage).where(
                            WorkflowOperationUsage.workflow_id == workflow_id
                        )
                    )
                ).scalars().all()

            assert len(rows) == 1, [r.operation_key for r in rows]
            assert rows[0].count >= 1
            assert rows[0].operation_key == "roles.create"
        finally:
            async with async_session_factory() as cleanup:
                if role_id is not None:
                    await cleanup.execute(
                        delete(AuditLog).where(
                            AuditLog.action == "role.create",
                            AuditLog.resource_id == role_id,
                        )
                    )
                    await cleanup.execute(
                        delete(RoleModel).where(RoleModel.id == role_id)
                    )
                await cleanup.execute(
                    delete(WorkflowOperationUsage).where(
                        WorkflowOperationUsage.workflow_id == workflow_id
                    )
                )
                await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
                await cleanup.commit()
            await delete_live_execution(async_session_factory, str(execution_id))
