"""Registration authorizes the scope it will actually persist, before file reads."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.workflows import RegisterWorkflowRequest
from src.routers import workflows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_scope", "target", "runtime_scope", "expected"),
    [
        ("stored", "omitted", None, "stored"),
        ("stored", "omitted", "runtime", "stored"),
        (None, "omitted", "runtime", None),
        ("stored", None, None, None),
        ("stored", "explicit", None, "explicit"),
        ("new", "omitted", None, "home"),
        ("new", "omitted", "runtime", "runtime"),
        ("new", None, "runtime", None),
    ],
)
async def test_registration_authorizes_final_scope_before_reading_source(
    existing_scope, target, runtime_scope, expected,
):
    organizations = {name: uuid4() for name in ("stored", "runtime", "explicit", "home")}
    existing = (
        None if existing_scope == "new" else SimpleNamespace(
            is_active=False, organization_id=organizations.get(existing_scope),
        )
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock(
        scalar_one_or_none=Mock(return_value=existing),
    )))
    caller = SimpleNamespace(
        principal=SimpleNamespace(runtime_authority=(
            SimpleNamespace(organization_id=organizations[runtime_scope])
            if runtime_scope else None
        )),
        ctx=SimpleNamespace(home_organization_id=organizations["home"]),
    )
    body = {"path": "workflows/example.py", "function_name": "example"}
    if target != "omitted":
        body["organization_id"] = str(organizations[target]) if target else None
    user = SimpleNamespace()
    service = SimpleNamespace(read_file=AsyncMock(side_effect=FileNotFoundError))
    with (
        patch.object(workflows, "load_caller", AsyncMock(return_value=caller)),
        patch.object(workflows, "_require_workflow_permission", AsyncMock()) as source_permission,
        patch.object(workflows, "_require_workflow_target_permission", AsyncMock()) as target_permission,
        patch("src.services.file_storage.FileStorageService", return_value=service),
    ):
        with pytest.raises(HTTPException) as error:
            await workflows.register_workflow(RegisterWorkflowRequest(**body), db, user)
    assert error.value.status_code == 404
    target_permission.assert_awaited_once_with(
        db, user, organizations.get(expected), "workflows.readwrite",
    )
    if existing is not None:
        source_permission.assert_awaited_once_with(db, user, existing, "workflows.readwrite")
    else:
        source_permission.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_at", ["source", "target"])
async def test_registration_refuses_unauthorized_scope_before_reading_source(denied_at):
    existing = SimpleNamespace(is_active=False, organization_id=uuid4())
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock(
        scalar_one_or_none=Mock(return_value=existing),
    )))
    source = AsyncMock(side_effect=HTTPException(404) if denied_at == "source" else None)
    target = AsyncMock(side_effect=HTTPException(403) if denied_at == "target" else None)
    with (
        patch.object(workflows, "_require_workflow_permission", source),
        patch.object(workflows, "_require_workflow_target_permission", target),
        patch("src.services.file_storage.FileStorageService") as storage,
    ):
        with pytest.raises(HTTPException) as error:
            await workflows.register_workflow(
                RegisterWorkflowRequest(path="workflows/example.py", function_name="example"),
                db, SimpleNamespace(),
            )
    assert error.value.status_code == (404 if denied_at == "source" else 403)
    storage.assert_not_called()

