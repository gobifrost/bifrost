"""Boundary/capability gates for Workflow administration routes."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models import (
    RemapWorkflowRequest,
    ReplaceWorkflowRequest,
)
from src.models.orm.workflows import Workflow
from src.routers import workflows
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    organization_id: UUID,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary
        or AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _workflow(
    *,
    workflow_id: UUID | None = None,
    organization_id: UUID | None,
    is_orphaned: bool = True,
) -> Workflow:
    return Workflow(
        id=workflow_id or uuid4(),
        name="Customer workflow",
        function_name="run",
        path="workflows/customer.py",
        organization_id=organization_id,
        is_active=not is_orphaned,
        is_orphaned=is_orphaned,
    )


@pytest.mark.asyncio
async def test_usage_stats_rejects_managed_collection_boundary() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"workflows.read"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        await workflows.get_workflow_usage_stats(authorization, SimpleNamespace())

    assert exc.value.status_code == 409
    assert "Select one organization" in exc.value.detail


@pytest.mark.asyncio
async def test_usage_stats_requires_workflow_read_capability() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities=set(),
    )

    with pytest.raises(HTTPException) as exc:
        await workflows.get_workflow_usage_stats(authorization, SimpleNamespace())

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: workflows.read"


@pytest.mark.asyncio
async def test_list_orphaned_workflows_filters_to_visible_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visible_id = uuid4()
    hidden_id = uuid4()
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"workflows.read"},
    )

    class _Repo:
        async def list(self, **filters):  # noqa: ANN001, ANN201
            assert filters == {"is_orphaned": True}
            return [
                _workflow(
                    workflow_id=visible_id,
                    organization_id=authorization.selected_boundary.organization_id,
                )
            ]

    class _Service:
        def __init__(self, db):  # noqa: ANN001
            self.db = db

        async def get_orphaned_workflows(self):  # noqa: ANN201
            return [
                SimpleNamespace(
                    id=str(visible_id),
                    name="Visible",
                    function_name="visible",
                    last_path="workflows/visible.py",
                    code="",
                    used_by=[],
                    orphaned_at=None,
                ),
                SimpleNamespace(
                    id=str(hidden_id),
                    name="Hidden",
                    function_name="hidden",
                    last_path="workflows/hidden.py",
                    code="",
                    used_by=[],
                    orphaned_at=None,
                ),
            ]

    monkeypatch.setattr(
        workflows, "authorized_workflow_repository", lambda db, auth: _Repo()
    )
    monkeypatch.setattr("src.services.workflow_orphan.WorkflowOrphanService", _Service)

    result = await workflows.list_orphaned_workflows(authorization, SimpleNamespace())

    assert [workflow.id for workflow in result.workflows] == [str(visible_id)]


@pytest.mark.asyncio
async def test_compatible_replacements_requires_visible_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"workflows.read"},
    )
    calls: list[UUID] = []

    async def _authorized(db, auth, row_id):  # noqa: ANN001, ANN201
        calls.append(row_id)
        return _workflow(
            workflow_id=row_id, organization_id=auth.selected_boundary.organization_id
        )

    class _Service:
        def __init__(self, db):  # noqa: ANN001
            self.db = db

        async def get_compatible_replacements(self, row_id):  # noqa: ANN001, ANN201
            assert row_id == workflow_id
            return []

    monkeypatch.setattr(workflows, "authorized_workflow_by_id", _authorized)
    monkeypatch.setattr("src.services.workflow_orphan.WorkflowOrphanService", _Service)

    await workflows.get_compatible_replacements(
        workflow_id, authorization, SimpleNamespace()
    )

    assert calls == [workflow_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "payload"),
    [
        (
            "replace_workflow",
            ReplaceWorkflowRequest(source_path="workflows/new.py", function_name="run"),
        ),
        (
            "remap_workflow_references",
            RemapWorkflowRequest(target_workflow_id=str(uuid4())),
        ),
        ("recreate_workflow_file", None),
        ("deactivate_workflow", None),
    ],
)
async def test_orphan_mutations_require_workflow_readwrite(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
    payload: object | None,
) -> None:
    workflow_id = uuid4()
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"workflows.read"},
    )

    async def _authorized(db, auth, row_id):  # noqa: ANN001, ANN201
        return _workflow(
            workflow_id=row_id, organization_id=auth.selected_boundary.organization_id
        )

    monkeypatch.setattr(workflows, "authorized_workflow_by_id", _authorized)

    route = getattr(workflows, route_name)
    with pytest.raises(HTTPException) as exc:
        if payload is None:
            await route(workflow_id, authorization, SimpleNamespace())
        else:
            await route(workflow_id, payload, authorization, SimpleNamespace())

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: workflows.readwrite"


@pytest.mark.asyncio
async def test_remap_requires_target_workflow_in_same_authorized_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = uuid4()
    target_id = uuid4()
    selected_org_id = uuid4()
    authorization = _authorization(
        organization_id=selected_org_id,
        capabilities={"workflows.readwrite"},
    )

    async def _authorized(db, auth, row_id):  # noqa: ANN001, ANN201
        organization_id = selected_org_id if row_id == source_id else uuid4()
        return _workflow(workflow_id=row_id, organization_id=organization_id)

    monkeypatch.setattr(workflows, "authorized_workflow_by_id", _authorized)

    with pytest.raises(HTTPException) as exc:
        await workflows.remap_workflow_references(
            source_id,
            RemapWorkflowRequest(target_workflow_id=str(target_id)),
            authorization,
            SimpleNamespace(),
        )

    assert exc.value.status_code == 409
