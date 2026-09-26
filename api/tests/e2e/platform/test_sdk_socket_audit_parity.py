"""E2E: worker-local socket audit attribution parity (follow-up to #810).

A role created through the worker-local engine SDK app with a signed engine
token carrying caller claims must produce exactly one ``audit_logs`` row,
attributed to the signed caller with ``source='workflow'`` and the token's
execution id — not the engine sentinel, and not a silent skip.
"""

import uuid

import httpx
import pytest


class TestSocketAuditAttribution:
    @pytest.mark.asyncio
    async def test_role_create_over_socket_audits_signed_caller(
        self, async_session_factory, org1_user
    ):
        from sqlalchemy import delete, select

        from src.core.security import mint_engine_token
        from src.models.orm.audit import AuditLog
        from src.models.orm.users import Role as RoleModel
        from src.services.execution.worker_sdk_http import build_worker_sdk_app

        caller_id = org1_user.user_id
        org_id = org1_user.organization_id
        assert caller_id is not None and org_id is not None
        execution_id = uuid.uuid4()
        role_name = f"socket-audit-{uuid.uuid4().hex[:8]}"

        token, _ = mint_engine_token(
            execution_id=str(execution_id),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            caller_user_id=str(caller_id),
            caller_organization_id=str(org_id),
            caller_email=org1_user.email,
            caller_name=org1_user.name,
        )

        app = build_worker_sdk_app()
        transport = httpx.ASGITransport(app=app)
        role_id: uuid.UUID | None = None
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://bifrost-engine"
            ) as client:
                response = await client.post(
                    "/api/roles",
                    json={
                        "name": role_name,
                        "description": "socket audit parity",
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert response.status_code == 201, response.text
            role_id = uuid.UUID(response.json()["id"])

            async with async_session_factory() as session:
                rows = (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == "role.create",
                            AuditLog.resource_id == role_id,
                        )
                    )
                ).scalars().all()

            assert len(rows) == 1, [str(r.id) for r in rows]
            row = rows[0]
            assert row.user_id == caller_id
            assert row.organization_id == org_id
            assert row.source == "workflow"
            assert row.execution_id == execution_id
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
                await cleanup.commit()
