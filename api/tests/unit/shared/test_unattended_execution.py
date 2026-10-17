"""Unattended HTTP and queue entry points use the selected run identity."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import Request

from shared.builtin_roles import USER_ROLE_ID
from shared.run_lineage import RunLineage
from shared.unattended_execution import create_unattended_context
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.routers import endpoints


async def _identity(db):
    org = Organization(name="Contoso", created_by="test")
    db.add(org)
    await db.flush()
    identity = User(email=f"identity-{uuid4().hex}@contoso.example", name="Contoso Automation", organization_id=org.id,
                    identity_kind="custom", base_role_id=USER_ROLE_ID, is_superuser=True)
    db.add(identity)
    await db.flush()
    return identity, org


async def test_context_effects_use_run_identity_and_role_facts(db_session):
    identity, org = await _identity(db_session)
    execution_id = str(uuid4())
    context = await create_unattended_context(db_session, lineage=RunLineage(identity.id, identity.id, None),
                    execution_id=execution_id, org_id=str(org.id), is_function_key=True)
    assert context.user_id == str(identity.id)
    assert context.email == identity.email
    assert context.execution_id == execution_id
    assert context.org_id == str(org.id)
    assert context.is_function_key is True
    assert context.is_platform_admin is False  # A stored actor flag grants no role.


async def test_api_key_endpoint_queues_the_identity_instead_of_a_system_actor(db_session, monkeypatch):
    identity, _org = await _identity(db_session)
    workflow_id = str(uuid4())
    lineage = RunLineage(identity.id, identity.id, None)

    @asynccontextmanager
    async def database():
        yield db_session

    monkeypatch.setattr(endpoints, "get_db_context", database)
    monkeypatch.setattr(endpoints, "unattended_lineage", AsyncMock(return_value=lineage))
    monkeypatch.setattr(endpoints, "validate_workflow_key", AsyncMock(return_value=(True, workflow_id)))
    monkeypatch.setattr(endpoints, "get_redis_client", lambda: SimpleNamespace(get_endpoint_workflow_cache=AsyncMock(return_value={
        "workflow_id": workflow_id, "file_path": "contoso.py", "execution_mode": "sync", "timeout_seconds": 30, "allowed_methods": ["POST"],
    })))
    run = AsyncMock(return_value=endpoints.EndpointExecuteResponse(execution_id=str(uuid4()), status="Success"))
    monkeypatch.setattr(endpoints, "_execute_sync", run)

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    await endpoints.execute_endpoint(workflow_id, Request({"type": "http", "method": "POST", "query_string": b"", "headers": []}, receive), "test-key")
    context = run.await_args.kwargs["context"]
    assert context.user_id == str(identity.id)
    assert context.is_function_key is True
    assert run.await_args.kwargs["lineage"] == lineage
