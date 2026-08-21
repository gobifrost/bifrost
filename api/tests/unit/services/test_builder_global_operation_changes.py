"""Reviewed Global Builder operation changeset behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.jobs.platform.builder_global_operations import (
    _capabilities_for_operation_ids,
    required_capabilities_for_changes,
)
from src.jobs.platform.builder_global_release import (
    BuilderGlobalReleaseApplyPayload,
    BuilderGlobalReleaseRollbackPayload,
)
from src.models.orm.solution_builder import SolutionGlobalOperationChange
from src.services.builder.global_operation_changes import (
    GlobalOperationChangeError,
    GlobalOperationConflict,
    apply_staged_global_operation_changes,
    discard_staged_global_operation_change,
    global_operation_inventory,
    list_applied_global_operation_changes,
    list_staged_global_operation_changes,
    operation_change_applied_fingerprint,
    operation_change_review_fingerprint,
    recover_interrupted_global_operation_changes,
    rollback_applied_global_operation_changes,
    stage_global_operation_change,
)


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uuid4(),
        user_email="builder@example.test",
        user_name="Builder",
        org_id=None,
        is_platform_admin=True,
        is_external=False,
        authorization_boundary="platform",
    )


def _scalars(rows: list[SolutionGlobalOperationChange]) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = rows
    return scalars


@pytest.mark.asyncio
async def test_stage_agent_create_validates_dto_without_live_mutation(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="agents.create",
        payload={"name": "Global Agent", "system_prompt": "Help."},
        created_by=uuid4(),
    )

    assert result.operation_id == "agents.create"
    assert result.validation_errors == []
    assert staged[0].payload["organization_id"] is None
    assert staged[0].state == "staged"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage_planned_domain_fails_closed_without_staging():
    db = AsyncMock()

    with pytest.raises(GlobalOperationChangeError, match="fails closed"):
        await stage_global_operation_change(
            db,
            solution_id=uuid4(),
            context=_context(),
            operation_id="forms.delete",
            payload={"name": "Intake"},
            created_by=uuid4(),
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_stage_workflow_register_fails_closed_without_staging():
    db = AsyncMock()

    with pytest.raises(GlobalOperationChangeError, match="fails closed"):
        await stage_global_operation_change(
            db,
            solution_id=uuid4(),
            context=_context(),
            operation_id="workflows.register",
            payload={"path": "workflows/example.py", "function_name": "example"},
            created_by=uuid4(),
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_stage_app_publish_fails_closed_without_staging():
    db = AsyncMock()

    with pytest.raises(GlobalOperationChangeError, match="fails closed"):
        await stage_global_operation_change(
            db,
            solution_id=uuid4(),
            context=_context(),
            operation_id="apps.publish",
            resource_id=str(uuid4()),
            payload={},
            created_by=uuid4(),
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_stage_agent_update_rejects_unsupported_irreversible_fields(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="agents.update",
        resource_id=str(uuid4()),
        payload={"description": "Cannot safely restore prior null today"},
        created_by=uuid4(),
    )

    assert result.validation_errors
    assert "unsupported field(s): description" in result.validation_errors[0]
    assert staged[0].state == "staged"


@pytest.mark.asyncio
async def test_stage_form_update_rejects_role_side_effects(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    before = {
        "id": str(uuid4()),
        "name": "Role form",
        "role_ids": [str(uuid4())],
    }
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(return_value=(200, before)),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="forms.update",
        resource_id=before["id"],
        payload={"name": "Updated"},
        created_by=uuid4(),
    )

    assert result.validation_errors
    assert "role_ids" in result.validation_errors[0]


@pytest.mark.asyncio
async def test_stage_form_update_rejects_role_ids_even_when_empty(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    before = {
        "id": str(uuid4()),
        "name": "Role-free form",
        "role_ids": [],
    }
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(return_value=(200, before)),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="forms.update",
        resource_id=before["id"],
        payload={"name": "Updated", "role_ids": []},
        created_by=uuid4(),
    )

    assert result.validation_errors
    assert "unsupported field(s): role_ids" in result.validation_errors[0]


@pytest.mark.asyncio
async def test_stage_form_create_rejects_role_side_effects(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="forms.create",
        payload={
            "name": "Role form",
            "form_schema": {"fields": [{"name": "email", "type": "text"}]},
            "role_ids": [uuid4()],
        },
        created_by=uuid4(),
    )

    assert result.validation_errors
    assert "role_ids" in result.validation_errors[0]


@pytest.mark.asyncio
async def test_stage_agent_create_rejects_role_tool_side_effects(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="agents.create",
        payload={
            "name": "Role tool agent",
            "system_prompt": "Help.",
            "role_ids": [uuid4()],
            "tool_ids": [uuid4()],
        },
        created_by=uuid4(),
    )

    assert result.validation_errors
    assert "role_ids and tool_ids" in result.validation_errors[0]


@pytest.mark.asyncio
async def test_stage_workflow_update_supports_reversible_metadata(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    before = {
        "id": str(uuid4()),
        "name": "global_workflow",
        "display_name": "Global Workflow",
        "description": "Before",
        "category": "General",
        "timeout_seconds": 300,
        "execution_mode": "sync",
        "time_saved": 0,
        "value": 0,
        "tool_description": "Before tool description",
        "cache_ttl_seconds": 300,
        "tags": [],
        "access_level": "role_based",
        "organization_id": None,
    }
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(return_value=(200, before)),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="workflows.update",
        resource_id=before["id"],
        payload={
            "display_name": "Updated Workflow",
            "description": "After",
            "tags": ["global"],
        },
        created_by=uuid4(),
    )

    assert result.validation_errors == []
    assert staged[0].resource_type == "workflow"
    assert staged[0].payload == {
        "organization_id": None,
        "display_name": "Updated Workflow",
        "description": "After",
        "tags": ["global"],
    }
    assert staged[0].before_state == before


@pytest.mark.asyncio
async def test_stage_workflow_update_rejects_role_and_endpoint_side_effects(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    before = {"id": str(uuid4()), "name": "global_workflow"}
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(return_value=(200, before)),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="workflows.update",
        resource_id=before["id"],
        payload={"role_ids": [], "endpoint_enabled": True},
        created_by=uuid4(),
    )

    assert result.validation_errors
    assert "endpoint_enabled" in result.validation_errors[0]
    assert "role_ids" in result.validation_errors[0]


@pytest.mark.asyncio
async def test_stage_app_update_supports_reversible_metadata(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    before = {
        "id": str(uuid4()),
        "name": "Global App",
        "slug": "global-app",
        "description": "Before",
        "icon": "layout-dashboard",
        "access_level": "authenticated",
        "organization_id": None,
        "app_model": "inline_v1",
        "repo_path": "apps/global-app",
        "role_ids": [],
        "is_published": False,
        "has_unpublished_changes": False,
    }
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(return_value=(200, {"applications": [before]})),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="apps.update",
        resource_id=before["id"],
        payload={
            "name": "Updated App",
            "slug": "updated-global-app",
            "description": None,
            "icon": None,
            "access_level": "everyone",
        },
        created_by=uuid4(),
    )

    assert result.validation_errors == []
    assert staged[0].resource_type == "application"
    assert staged[0].payload == {
        "organization_id": None,
        "name": "Updated App",
        "slug": "updated-global-app",
        "description": None,
        "icon": None,
        "access_level": "everyone",
    }
    assert staged[0].before_state == before


@pytest.mark.asyncio
async def test_stage_app_update_rejects_source_and_role_side_effects(monkeypatch):
    db = AsyncMock()
    staged: list[SolutionGlobalOperationChange] = []
    db.add = MagicMock(side_effect=staged.append)
    before = {"id": str(uuid4()), "name": "Global App", "slug": "global-app"}
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(return_value=(200, before)),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await stage_global_operation_change(
        db,
        solution_id=uuid4(),
        context=_context(),
        operation_id="apps.update",
        resource_id=before["id"],
        payload={"role_ids": [], "repo_path": "apps/other"},
        created_by=uuid4(),
    )

    assert result.validation_errors
    assert "repo_path" in result.validation_errors[0]
    assert "role_ids" in result.validation_errors[0]


def test_global_operation_inventory_is_machine_readable_and_fail_closed():
    inventory = global_operation_inventory()

    assert inventory["implemented"]["agents.create"]["apply"] == "canonical_rest"
    assert inventory["implemented"]["apps.update"]["staging"] == "durable_changeset"
    assert inventory["implemented"]["forms.create"]["apply"] == "canonical_rest"
    assert inventory["implemented"]["forms.update"]["staging"] == "durable_changeset"
    assert inventory["implemented"]["tables.create"]["apply"] == "canonical_rest"
    assert inventory["implemented"]["tables.update"]["staging"] == "durable_changeset"
    assert inventory["implemented"]["workflows.update"]["staging"] == "durable_changeset"
    assert inventory["planned"]["apps.delete"]["status"] == "fail_closed"
    assert inventory["planned"]["apps.publish"]["status"] == "fail_closed"
    assert inventory["planned"]["tables.delete"]["status"] == "fail_closed"
    assert inventory["planned"]["workflows.register"]["status"] == "fail_closed"


def test_capabilities_for_operation_ids_uses_operation_catalog_scopes() -> None:
    assert _capabilities_for_operation_ids({"forms.create"}) == (
        "builder.execute",
        "forms.readwrite",
    )
    assert _capabilities_for_operation_ids({"agents.update", "forms.update"}) == (
        "agents.readwrite",
        "builder.execute",
        "forms.readwrite",
    )
    assert _capabilities_for_operation_ids({"tables.create"}) == (
        "builder.execute",
        "tables.readwrite",
    )
    assert _capabilities_for_operation_ids({"workflows.update"}) == (
        "builder.execute",
        "workflows.readwrite",
    )
    assert _capabilities_for_operation_ids({"apps.update"}) == (
        "apps.readwrite",
        "builder.execute",
    )


@pytest.mark.asyncio
async def test_required_capabilities_adds_roles_for_agent_role_payload() -> None:
    solution_id = uuid4()
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.create",
        resource_type="agent",
        payload={
            "name": "Role agent",
            "system_prompt": "Help.",
            "role_ids": [str(uuid4())],
            "organization_id": None,
        },
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))

    capabilities = await required_capabilities_for_changes(
        db,
        solution_id=solution_id,
        approved_changes={row.id: "fingerprint"},
    )

    assert capabilities == (
        "agents.readwrite",
        "builder.execute",
        "roles.readwrite",
    )


@pytest.mark.asyncio
async def test_required_capabilities_for_form_only_does_not_require_agent_or_roles() -> None:
    solution_id = uuid4()
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="forms.update",
        resource_type="form",
        resource_id=str(uuid4()),
        payload={"name": "Updated", "organization_id": None},
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))

    capabilities = await required_capabilities_for_changes(
        db,
        solution_id=solution_id,
        approved_changes={row.id: "fingerprint"},
    )

    assert capabilities == ("builder.execute", "forms.readwrite")


@pytest.mark.asyncio
async def test_required_capabilities_for_table_only_uses_catalog_scope() -> None:
    solution_id = uuid4()
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="tables.create",
        resource_type="table",
        payload={"name": "global_table", "organization_id": None},
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))

    capabilities = await required_capabilities_for_changes(
        db,
        solution_id=solution_id,
        approved_changes={row.id: "fingerprint"},
    )

    assert capabilities == ("builder.execute", "tables.readwrite")


def test_release_payload_defaults_do_not_share_operation_dicts() -> None:
    first_apply = BuilderGlobalReleaseApplyPayload(solution_id=uuid4())
    second_apply = BuilderGlobalReleaseApplyPayload(solution_id=uuid4())
    first_apply.approved_operation_changes[uuid4()] = "fingerprint"

    first_rollback = BuilderGlobalReleaseRollbackPayload(solution_id=uuid4())
    second_rollback = BuilderGlobalReleaseRollbackPayload(solution_id=uuid4())
    first_rollback.approved_operation_changes[uuid4()] = "fingerprint"

    assert second_apply.approved_operation_changes == {}
    assert second_rollback.approved_operation_changes == {}


@pytest.mark.asyncio
async def test_apply_agent_update_detects_concurrent_live_change(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before",
        "system_prompt": "Before prompt",
        "channels": ["chat"],
        "organization_id": None,
        "is_active": True,
    }
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=resource_id,
        state="staged",
        payload={"name": "After", "organization_id": None},
        before_state=before,
        before_fingerprint="not-the-live-fingerprint",
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(return_value=(200, before)),
    )

    with pytest.raises(GlobalOperationConflict, match="changed before"):
        await apply_staged_global_operation_changes(
            db,
            solution_id=solution_id,
            context=_context(),
            requested_by=uuid4(),
        )

    assert row.state == "staged"


@pytest.mark.asyncio
async def test_apply_agent_update_uses_canonical_rest_and_marks_applied(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before",
        "system_prompt": "Before prompt",
        "channels": ["chat"],
        "organization_id": None,
        "is_active": True,
    }
    after = {**before, "name": "After"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=resource_id,
        state="staged",
        payload={"name": "After", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    rest = AsyncMock(side_effect=[(200, before), (200, after)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    results = await apply_staged_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
    )

    assert [result.operation_id for result in results] == ["agents.update"]
    assert row.state == "applied"
    assert row.applied_state == after
    assert rest.await_args_list[1].args[1:3] == (
        "PUT",
        f"/api/agents/{resource_id}",
    )
    assert db.commit.await_count >= 2


@pytest.mark.asyncio
async def test_apply_form_update_uses_canonical_patch_and_marks_applied(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before form",
        "description": None,
        "confirmation_markdown": "Thanks",
        "workflow_id": None,
        "launch_workflow_id": None,
        "default_launch_params": None,
        "allowed_query_params": None,
        "form_schema": {"fields": [{"name": "email", "type": "text"}]},
        "access_level": "authenticated",
        "organization_id": None,
        "role_ids": [],
        "is_active": True,
    }
    after = {**before, "name": "After form"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="forms.update",
        resource_type="form",
        resource_id=resource_id,
        state="staged",
        payload={"name": "After form", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    rest = AsyncMock(side_effect=[(200, before), (200, after)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    results = await apply_staged_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
    )

    assert [result.operation_id for result in results] == ["forms.update"]
    assert row.state == "applied"
    assert row.applied_state == after
    assert rest.await_args_list[1].args[1:3] == (
        "PATCH",
        f"/api/forms/{resource_id}",
    )


@pytest.mark.asyncio
async def test_apply_table_update_uses_canonical_patch_and_marks_applied(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "before_table",
        "description": None,
        "schema": {"columns": [{"name": "email", "type": "string"}]},
        "organization_id": None,
        "policies": None,
    }
    after = {**before, "name": "after_table"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="tables.update",
        resource_type="table",
        resource_id=resource_id,
        state="staged",
        payload={"name": "after_table", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    rest = AsyncMock(side_effect=[(200, before), (200, after)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    results = await apply_staged_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
    )

    assert [result.operation_id for result in results] == ["tables.update"]
    assert row.state == "applied"
    assert row.applied_state == after
    assert rest.await_args_list[1].args[1:3] == (
        "PATCH",
        f"/api/tables/{resource_id}",
    )


@pytest.mark.asyncio
async def test_apply_app_update_uses_export_read_and_canonical_patch(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before App",
        "slug": "before-app",
        "description": "Before",
        "icon": "layout",
        "access_level": "authenticated",
        "organization_id": None,
        "app_model": "inline_v1",
        "repo_path": "apps/before-app",
        "role_ids": [],
    }
    after = {**before, "name": "After App"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="apps.update",
        resource_type="application",
        resource_id=resource_id,
        state="staged",
        payload={"name": "After App", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    rest = AsyncMock(side_effect=[(200, {"applications": [before]}), (200, after)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    results = await apply_staged_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
    )

    assert [result.operation_id for result in results] == ["apps.update"]
    assert row.state == "applied"
    assert row.applied_state == after
    assert rest.await_args_list[0].args[1:3] == (
        "GET",
        "/api/applications",
    )
    assert rest.await_args_list[1].args[1:3] == (
        "PATCH",
        f"/api/applications/{resource_id}",
    )


@pytest.mark.asyncio
async def test_apply_agent_update_rejects_unsupported_fields_before_mutation(
    monkeypatch,
):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before",
        "system_prompt": "Before prompt",
        "channels": ["chat"],
        "organization_id": None,
        "is_active": True,
    }
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=resource_id,
        state="staged",
        payload={"description": "Unsupported rollback field", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    rest = AsyncMock(return_value=(200, before))
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )

    with pytest.raises(GlobalOperationChangeError, match="unsupported field"):
        await apply_staged_global_operation_changes(
            db,
            solution_id=solution_id,
            context=_context(),
            requested_by=uuid4(),
        )

    assert row.state == "staged"
    rest.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_claims_rows_with_platform_job_id_and_duplicate_returns_batch(
    monkeypatch,
):
    solution_id = uuid4()
    apply_job_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before",
        "system_prompt": "Before prompt",
        "channels": ["chat"],
        "organization_id": None,
        "is_active": True,
    }
    after = {**before, "name": "After"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=resource_id,
        state="staged",
        payload={"name": "After", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(side_effect=[(200, before), (200, after)]),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    first = await apply_staged_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        apply_job_id=apply_job_id,
        approved_changes={row.id: operation_change_review_fingerprint(row)},
    )
    assert first[0].id == row.id
    assert row.apply_job_id == apply_job_id
    assert row.state == "applied"

    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    second = await apply_staged_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        apply_job_id=apply_job_id,
        approved_changes={row.id: operation_change_review_fingerprint(row)},
    )

    assert [result.id for result in second] == [row.id]


@pytest.mark.asyncio
async def test_apply_uses_only_approved_change_ids_and_excludes_late_stage(
    monkeypatch,
):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before",
        "system_prompt": "Before prompt",
        "channels": ["chat"],
        "organization_id": None,
        "is_active": True,
    }
    after = {**before, "name": "After"}
    from src.services.builder.global_operation_changes import _fingerprint

    approved = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=resource_id,
        state="staged",
        payload={"name": "After", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    approved.validation_errors = []
    late = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.create",
        resource_type="agent",
        state="staged",
        payload={"name": "Late", "organization_id": None},
        validation_errors=[],
    )
    late.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([approved])])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        AsyncMock(side_effect=[(200, before), (200, after)]),
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    results = await apply_staged_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        apply_job_id=uuid4(),
        approved_changes={
            approved.id: operation_change_review_fingerprint(approved),
        },
    )

    assert [result.id for result in results] == [approved.id]
    assert approved.state == "applied"
    assert late.state == "staged"


@pytest.mark.asyncio
async def test_apply_rejects_tampered_approved_change_before_rest(monkeypatch):
    solution_id = uuid4()
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.create",
        resource_type="agent",
        state="staged",
        payload={"name": "Reviewed", "organization_id": None},
        validation_errors=[],
    )
    row.validation_errors = []
    approved_fingerprint = operation_change_review_fingerprint(row)
    row.payload = {"name": "Tampered", "organization_id": None}
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_scalars([]), _scalars([row])])
    rest = AsyncMock()
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )

    with pytest.raises(GlobalOperationConflict, match="changed after review"):
        await apply_staged_global_operation_changes(
            db,
            solution_id=solution_id,
            context=_context(),
            requested_by=uuid4(),
            apply_job_id=uuid4(),
            approved_changes={row.id: approved_fingerprint},
        )

    rest.assert_not_awaited()


@pytest.mark.asyncio
async def test_interrupted_apply_without_fingerprint_is_visible_and_discardable(
    monkeypatch,
):
    solution_id = uuid4()
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.create",
        resource_type="agent",
        payload={"name": "Ambiguous", "organization_id": None},
        state="applying",
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    recovered = await recover_interrupted_global_operation_changes(
        db,
        solution_id=solution_id,
    )
    assert recovered == 1
    assert row.state == "failed"
    assert row.validation_errors

    db.scalars = AsyncMock(return_value=_scalars([row]))
    listed = await list_staged_global_operation_changes(db, solution_id=solution_id)
    assert [item.id for item in listed] == [row.id]

    db.get = AsyncMock(return_value=row)
    discarded = await discard_staged_global_operation_change(
        db,
        solution_id=solution_id,
        change_id=row.id,
        requested_by=uuid4(),
    )
    assert discarded.state == "discarded"


@pytest.mark.asyncio
async def test_list_applied_changes_returns_only_latest_apply_batch():
    solution_id = uuid4()
    old_batch = uuid4()
    latest_batch = uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=latest_batch)
    older = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=str(uuid4()),
        state="applied",
        apply_job_id=old_batch,
        payload={},
        validation_errors=[],
    )
    latest = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=str(uuid4()),
        state="applied",
        apply_job_id=latest_batch,
        payload={},
        validation_errors=[],
    )
    older.validation_errors = []
    latest.validation_errors = []
    db.scalars = AsyncMock(return_value=_scalars([latest]))

    result = await list_applied_global_operation_changes(
        db,
        solution_id=solution_id,
    )

    assert [item.id for item in result] == [latest.id]
    assert result[0].apply_job_id == latest_batch


@pytest.mark.asyncio
async def test_rollback_agent_update_restores_before_state_when_unchanged(
    monkeypatch,
):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before",
        "system_prompt": "Before prompt",
        "channels": ["chat"],
        "organization_id": None,
        "is_active": True,
    }
    after = {**before, "name": "After"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=resource_id,
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "After", "organization_id": None},
        before_state=before,
        applied_state=after,
        applied_fingerprint=_fingerprint(after),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    rest = AsyncMock(side_effect=[(200, after), (200, before)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await rollback_applied_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        rollback_job_id=uuid4(),
        approved_changes={row.id: operation_change_applied_fingerprint(row)},
    )

    assert [item.id for item in result] == [row.id]
    assert row.state == "rolled_back"
    assert rest.await_args_list[1].args[1:3] == (
        "PUT",
        f"/api/agents/{resource_id}",
    )


@pytest.mark.asyncio
async def test_rollback_form_update_restores_before_state_when_unchanged(
    monkeypatch,
):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "Before form",
        "description": None,
        "confirmation_markdown": "Thanks",
        "workflow_id": None,
        "launch_workflow_id": None,
        "default_launch_params": None,
        "allowed_query_params": None,
        "form_schema": {"fields": [{"name": "email", "type": "text"}]},
        "access_level": "authenticated",
        "organization_id": None,
        "role_ids": [],
        "is_active": True,
    }
    after = {**before, "name": "After form"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="forms.update",
        resource_type="form",
        resource_id=resource_id,
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "After form", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        applied_state=after,
        applied_fingerprint=_fingerprint(after),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    rest = AsyncMock(side_effect=[(200, after), (200, before)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await rollback_applied_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        rollback_job_id=uuid4(),
        approved_changes={row.id: operation_change_applied_fingerprint(row)},
    )

    assert [item.id for item in result] == [row.id]
    assert row.state == "rolled_back"
    assert rest.await_args_list[1].args[1:3] == (
        "PATCH",
        f"/api/forms/{resource_id}",
    )
    assert rest.await_args_list[1].kwargs["json_body"]["description"] is None
    assert "role_ids" not in rest.await_args_list[1].kwargs["json_body"]


@pytest.mark.asyncio
async def test_rollback_form_create_deactivates_before_purge(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    after = {
        "id": resource_id,
        "name": "Created form",
        "organization_id": None,
        "is_active": True,
    }
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="forms.create",
        resource_type="form",
        resource_id=resource_id,
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "Created form", "organization_id": None},
        before_state=None,
        applied_state=after,
        applied_fingerprint=_fingerprint(after),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    db.scalar = AsyncMock(return_value=0)
    rest = AsyncMock(side_effect=[(200, after), (200, {**after, "is_active": False}), (204, None)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await rollback_applied_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        rollback_job_id=uuid4(),
        approved_changes={row.id: operation_change_applied_fingerprint(row)},
    )

    assert [item.id for item in result] == [row.id]
    assert row.state == "rolled_back"
    assert rest.await_args_list[1].args[1:3] == (
        "PATCH",
        f"/api/forms/{resource_id}",
    )
    assert rest.await_args_list[1].kwargs["json_body"] == {
        "is_active": False,
        "organization_id": None,
    }
    assert rest.await_args_list[2].args[1:3] == (
        "DELETE",
        f"/api/forms/{resource_id}?purge=true",
    )


@pytest.mark.asyncio
async def test_rollback_table_create_hard_deletes(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    after = {
        "id": resource_id,
        "name": "created_table",
        "organization_id": None,
        "policies": None,
    }
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="tables.create",
        resource_type="table",
        resource_id=resource_id,
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "created_table", "organization_id": None},
        before_state=None,
        applied_state=after,
        applied_fingerprint=_fingerprint(after),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    db.scalar = AsyncMock(return_value=0)
    rest = AsyncMock(side_effect=[(200, after), (204, None)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await rollback_applied_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        rollback_job_id=uuid4(),
        approved_changes={row.id: operation_change_applied_fingerprint(row)},
    )

    assert [item.id for item in result] == [row.id]
    assert row.state == "rolled_back"
    assert rest.await_args_list[1].args[1:3] == (
        "DELETE",
        f"/api/tables/{resource_id}",
    )


@pytest.mark.asyncio
async def test_rollback_table_create_rejects_document_dependent_data(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    after = {
        "id": resource_id,
        "name": "created_table",
        "organization_id": None,
        "policies": None,
    }
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="tables.create",
        resource_type="table",
        resource_id=resource_id,
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "created_table", "organization_id": None},
        before_state=None,
        applied_state=after,
        applied_fingerprint=_fingerprint(after),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    db.scalar = AsyncMock(return_value=1)
    rest = AsyncMock(return_value=(200, after))
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )

    with pytest.raises(GlobalOperationConflict, match="documents=1"):
        await rollback_applied_global_operation_changes(
            db,
            solution_id=solution_id,
            context=_context(),
            requested_by=uuid4(),
            rollback_job_id=uuid4(),
            approved_changes={row.id: operation_change_applied_fingerprint(row)},
        )

    assert rest.await_count == 1


@pytest.mark.asyncio
async def test_rollback_agent_create_rejects_inbound_delegation(monkeypatch):
    solution_id = uuid4()
    resource_id = str(uuid4())
    after = {
        "id": resource_id,
        "name": "created_agent",
        "organization_id": None,
    }
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.create",
        resource_type="agent",
        resource_id=resource_id,
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "created_agent", "organization_id": None},
        before_state=None,
        applied_state=after,
        applied_fingerprint=_fingerprint(after),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    db.scalar = AsyncMock(side_effect=[0, 0, 0, 1, 0])
    rest = AsyncMock(return_value=(200, after))
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )

    with pytest.raises(GlobalOperationConflict, match="inbound_delegations=1"):
        await rollback_applied_global_operation_changes(
            db,
            solution_id=solution_id,
            context=_context(),
            requested_by=uuid4(),
            rollback_job_id=uuid4(),
            approved_changes={row.id: operation_change_applied_fingerprint(row)},
        )

    assert rest.await_count == 1


@pytest.mark.asyncio
async def test_rollback_table_update_restores_before_state_when_unchanged(
    monkeypatch,
):
    solution_id = uuid4()
    resource_id = str(uuid4())
    before = {
        "id": resource_id,
        "name": "before_table",
        "description": None,
        "schema": {"columns": [{"name": "email", "type": "string"}]},
        "organization_id": None,
        "policies": None,
    }
    after = {**before, "name": "after_table"}
    from src.services.builder.global_operation_changes import _fingerprint

    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="tables.update",
        resource_type="table",
        resource_id=resource_id,
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "after_table", "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        applied_state=after,
        applied_fingerprint=_fingerprint(after),
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    rest = AsyncMock(side_effect=[(200, after), (200, before)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await rollback_applied_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        rollback_job_id=uuid4(),
        approved_changes={row.id: operation_change_applied_fingerprint(row)},
    )

    assert [item.id for item in result] == [row.id]
    assert row.state == "rolled_back"
    assert rest.await_args_list[1].args[1:3] == (
        "PATCH",
        f"/api/tables/{resource_id}",
    )
    assert rest.await_args_list[1].kwargs["json_body"]["schema"] == before["schema"]
    assert rest.await_args_list[1].kwargs["json_body"]["policies"] is None


@pytest.mark.asyncio
async def test_rollback_rejects_tampered_applied_change_before_rest(monkeypatch):
    solution_id = uuid4()
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.create",
        resource_type="agent",
        resource_id=str(uuid4()),
        state="applied",
        apply_job_id=uuid4(),
        payload={"name": "Created", "organization_id": None},
        applied_state={"id": "agent-1", "name": "Created"},
        applied_fingerprint="reviewed",
        validation_errors=[],
    )
    row.validation_errors = []
    approved = operation_change_applied_fingerprint(row)
    row.applied_fingerprint = "tampered"
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))
    rest = AsyncMock()
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )

    with pytest.raises(GlobalOperationConflict, match="changed after review"):
        await rollback_applied_global_operation_changes(
            db,
            solution_id=solution_id,
            context=_context(),
            requested_by=uuid4(),
            rollback_job_id=uuid4(),
            approved_changes={row.id: approved},
        )

    rest.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_replays_completed_batch_by_rollback_job_id():
    solution_id = uuid4()
    rollback_job_id = uuid4()
    row = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=str(uuid4()),
        state="rolled_back",
        apply_job_id=uuid4(),
        rollback_job_id=rollback_job_id,
        payload={"name": "After", "organization_id": None},
        before_state={"id": "agent-1", "name": "Before"},
        before_fingerprint="before",
        applied_state={"id": "agent-1", "name": "After"},
        applied_fingerprint="after",
        validation_errors=[],
    )
    row.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([row]))

    result = await rollback_applied_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        rollback_job_id=rollback_job_id,
        approved_changes={row.id: "not checked for completed replay"},
    )

    assert [item.id for item in result] == [row.id]


@pytest.mark.asyncio
async def test_rollback_preflights_entire_batch_before_mutation(monkeypatch):
    solution_id = uuid4()
    apply_job_id = uuid4()
    first_id = str(uuid4())
    second_id = str(uuid4())
    from src.services.builder.global_operation_changes import _fingerprint

    first_after = {"id": first_id, "name": "First after", "organization_id": None}
    second_after = {"id": second_id, "name": "Second after", "organization_id": None}
    stale_second = {**second_after, "name": "Externally edited"}
    first = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=first_id,
        state="applied",
        apply_job_id=apply_job_id,
        payload={"name": "First after", "organization_id": None},
        before_state={"id": first_id, "name": "First before", "organization_id": None},
        applied_state=first_after,
        applied_fingerprint=_fingerprint(first_after),
        validation_errors=[],
    )
    second = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=second_id,
        state="applied",
        apply_job_id=apply_job_id,
        payload={"name": "Second after", "organization_id": None},
        before_state={"id": second_id, "name": "Second before", "organization_id": None},
        applied_state=second_after,
        applied_fingerprint=_fingerprint(second_after),
        validation_errors=[],
    )
    first.validation_errors = []
    second.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([first, second]))
    rest = AsyncMock(side_effect=[(200, first_after), (200, stale_second)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )

    with pytest.raises(GlobalOperationConflict, match="Live agent changed"):
        await rollback_applied_global_operation_changes(
            db,
            solution_id=solution_id,
            context=_context(),
            requested_by=uuid4(),
            rollback_job_id=uuid4(),
            approved_changes={
                first.id: operation_change_applied_fingerprint(first),
                second.id: operation_change_applied_fingerprint(second),
            },
        )

    assert rest.await_count == 2
    assert all(call.args[1] == "GET" for call in rest.await_args_list)


@pytest.mark.asyncio
async def test_rollback_reentry_skips_same_job_completed_rows_and_finishes_remaining(
    monkeypatch,
):
    solution_id = uuid4()
    rollback_job_id = uuid4()
    apply_job_id = uuid4()
    first_id = str(uuid4())
    second_id = str(uuid4())
    from src.services.builder.global_operation_changes import _fingerprint

    first_before = {"id": first_id, "name": "First before", "organization_id": None}
    first_after = {"id": first_id, "name": "First after", "organization_id": None}
    second_before = {"id": second_id, "name": "Second before", "organization_id": None}
    second_after = {"id": second_id, "name": "Second after", "organization_id": None}
    first = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=first_id,
        state="rolled_back",
        apply_job_id=apply_job_id,
        rollback_job_id=rollback_job_id,
        payload={"name": "First after", "organization_id": None},
        before_state=first_before,
        applied_state=first_after,
        applied_fingerprint=_fingerprint(first_after),
        validation_errors=[],
    )
    second = SolutionGlobalOperationChange(
        id=uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=second_id,
        state="applied",
        apply_job_id=apply_job_id,
        payload={"name": "Second after", "organization_id": None},
        before_state=second_before,
        applied_state=second_after,
        applied_fingerprint=_fingerprint(second_after),
        validation_errors=[],
    )
    first.validation_errors = []
    second.validation_errors = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=_scalars([first, second]))
    rest = AsyncMock(side_effect=[(200, second_after), (200, second_before)])
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes._rest_json",
        rest,
    )
    monkeypatch.setattr(
        "src.services.builder.global_operation_changes.emit_audit",
        AsyncMock(),
    )

    result = await rollback_applied_global_operation_changes(
        db,
        solution_id=solution_id,
        context=_context(),
        requested_by=uuid4(),
        rollback_job_id=rollback_job_id,
        approved_changes={
            first.id: "already completed in this job",
            second.id: operation_change_applied_fingerprint(second),
        },
    )

    assert {item.id for item in result} == {first.id, second.id}
    assert second.state == "rolled_back"
    assert second.rollback_job_id == rollback_job_id
    assert rest.await_count == 2
