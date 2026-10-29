"""Regression coverage for releasing SDK request DB connections before dispatch."""
from __future__ import annotations

from collections import Counter
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.sdk_workflow_execution import execute_sdk_workflow
from shared.sdk_users import set_platform_admin
from src.core.principal import UserPrincipal
from src.models import WorkflowExecutionRequest, WorkflowExecutionResponse
from src.models.enums import ExecutionStatus
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from tests.conftest import TEST_DATABASE_URL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_kind", "sync"),
    [
        ("workflow", True),
        ("workflow", False),
        ("data_provider", True),
        ("inline", False),
    ],
    ids=["workflow-sync", "workflow-async", "data-provider-cache-miss", "inline"],
)
async def test_execute_releases_main_pool_connection_before_dispatch(
    execution_kind: str, sync: bool
) -> None:
    """Dispatch must not retain the request's main-pool connection."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    user_id, workflow_id = uuid4(), uuid4()
    checked_out: Counter[str] = Counter()

    @event.listens_for(engine.sync_engine, "checkout")
    def _checkout(*_args: object) -> None:
        checked_out["checkout"] += 1

    @event.listens_for(engine.sync_engine, "checkin")
    def _checkin(*_args: object) -> None:
        checked_out["checkin"] += 1

    async def _assert_released_before_dispatch(
        *_args: object, **_kwargs: object
    ) -> WorkflowExecutionResponse:
        assert engine.pool.checkedout() == 0
        async with sessions() as probe:
            await probe.execute(text("SELECT 1"))
        assert engine.pool.checkedout() == 0
        return WorkflowExecutionResponse(
            execution_id=str(uuid4()),
            workflow_id=str(workflow_id),
            workflow_name="pool-retention-probe",
            status=ExecutionStatus.SUCCESS,
            result={"ok": True},
        )

    async def _invoke() -> WorkflowExecutionResponse:
        async with sessions() as db:
            if execution_kind == "inline":
                request = WorkflowExecutionRequest(
                    code="Y29kZQ==", input_data={}, sync=sync
                )
            else:
                request = WorkflowExecutionRequest(
                    workflow_id=str(workflow_id),
                    input_data={},
                    sync=sync,
                    transient=execution_kind == "data_provider",
                )
            return await execute_sdk_workflow(
                db,
                UserPrincipal(
                    user_id=user_id,
                    email="pool-retention-probe@example.test",
                    name="Pool retention probe",
                    organization_id=None,
                    is_superuser=True,
                    is_verified=True,
                ),
                request,
                caller_org_id=None,
            )

    try:
        async with sessions() as db:
            user = User(
                id=user_id,
                email="pool-retention-probe@example.test",
                name="Pool retention probe",
                is_superuser=True,
                is_verified=True,
            )
            db.add(user)
            await db.flush()
            await set_platform_admin(db, user, True, assigned_by="pool-retention-probe")
            if execution_kind != "inline":
                db.add(
                    Workflow(
                        id=workflow_id,
                        name="pool-retention-probe",
                        function_name="pool_retention_probe",
                        path="workflows/pool_retention_probe.py",
                        type=execution_kind,
                        cache_ttl_seconds=300 if execution_kind == "data_provider" else 0,
                        is_active=True,
                        permission_mode="full",
                    )
                )
            await db.commit()

        run_workflow = AsyncMock(side_effect=_assert_released_before_dispatch)
        run_code = AsyncMock(side_effect=_assert_released_before_dispatch)
        with (
            patch("src.services.execution.service.run_workflow", run_workflow),
            patch("src.services.execution.service.run_code", run_code),
            patch(
                "src.core.cache.data_provider_cache.get_cached_result",
                AsyncMock(return_value=None),
            ),
        ):
            result = await _invoke()

        if execution_kind == "inline":
            run_code.assert_awaited_once()
            run_workflow.assert_not_awaited()
        else:
            run_workflow.assert_awaited_once()
            run_code.assert_not_awaited()
        assert result.status is ExecutionStatus.SUCCESS
        assert engine.pool.checkedout() == 0
        assert checked_out["checkout"] == checked_out["checkin"]
    finally:
        async with sessions() as cleanup:
            if execution_kind != "inline":
                await cleanup.execute(text("DELETE FROM workflows WHERE id = :id"), {"id": workflow_id})
            await cleanup.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            await cleanup.commit()
        await engine.dispose()
