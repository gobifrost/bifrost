"""The workspace import planner deliberately projects install packages first."""
from __future__ import annotations

import hashlib
from uuid import UUID

import pytest


def test_solution_config_declaration_fields_have_workspace_projection_review() -> None:
    """A new declaration field must prompt review of the manual workspace mapping."""
    from bifrost.manifest import ManifestSolutionConfigSchema

    assert set(ManifestSolutionConfigSchema.model_fields) == {
        "id", "key", "type", "required", "description", "default", "position",
    }


def test_solution_package_projection_exposes_config_fields_without_secret_default() -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection, WorkspaceBundlePlanner
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(
            name="Customer operations",
            config_schemas=[{
                "id": "API_KEY", "key": "API_KEY", "type": "secret",
                "default": "not-a-secret", "required": True, "position": 3,
            }],
        ),
        preview_id=UUID(int=7),
    )

    config = projection.manifest.configs["API_KEY"]
    assert config.config_type == "secret"
    assert config.value is None
    assert (config.required, config.position) == (True, 3)
    planned = WorkspaceBundlePlanner(None, preview_id=UUID(int=7)).plan_sync(projection)
    assert planned.preview.config_schemas[0]["requires_input"] is True
    from src.services.solutions.workspace_bundle_import import (
        WorkspaceBundleDecisionError,
        require_workspace_config_values,
    )
    from src.models.contracts.solutions import WorkspaceBundleDecision

    with pytest.raises(WorkspaceBundleDecisionError, match="API_KEY"):
        require_workspace_config_values(planned.preview, [], {})
    require_workspace_config_values(planned.preview, [], {"API_KEY": "entered"})
    conflict = planned.preview.model_copy(update={
        "items": [planned.preview.items[0].model_copy(update={"classification": "conflict"})],
    })
    require_workspace_config_values(
        conflict,
        [WorkspaceBundleDecision(item_id=conflict.items[0].id, action="keep")],
        {},
    )
    assert projection.config_schemas == ({
        "key": "API_KEY", "type": "secret", "required": True, "description": None,
    },)


def test_declared_file_location_becomes_scoped_root_policy() -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection
    from src.services.solutions.zip_install import PreviewResult

    organization_id = UUID(int=8)
    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(
            file_locations=["shared"],
            config_schemas=[{"key": "ARCHIVE_KEY", "type": "string"}],
        ),
        preview_id=UUID(int=7), organization_id=organization_id,
    )

    policy = next(iter(projection.manifest.file_policies.values()))
    assert (policy.location, policy.path, policy.organization_id) == (
        "shared", "", str(organization_id),
    )
    assert policy.policies == [{"$ref": "admin_bypass"}]
    assert projection.manifest.configs["ARCHIVE_KEY"].organization_id == str(organization_id)


def test_created_entities_receive_stable_target_ids() -> None:
    from src.services.solutions.workspace_bundle_plan import (
        SolutionPackageWorkspaceProjection,
        WorkspaceBundlePlanner,
    )
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(workflows=[{
            "id": "11111111-1111-1111-1111-111111111111", "name": "run",
            "path": "workflows/run.py", "function_name": "run",
        }]),
        preview_id=UUID(int=7),
    )

    first = WorkspaceBundlePlanner(None, preview_id=UUID(int=7)).plan_sync(projection)
    second = WorkspaceBundlePlanner(None, preview_id=UUID(int=7)).plan_sync(projection)

    assert [(item.id, item.target_id) for item in first.preview.items] == [
        (item.id, item.target_id) for item in second.preview.items
    ]


def test_preview_recognizes_existing_scoped_definitions_and_remapped_references() -> None:
    from src.services.solutions.workspace_bundle_plan import (
        SolutionPackageWorkspaceProjection,
        WorkspaceBundlePlanner,
    )
    from src.services.solutions.zip_install import PreviewResult

    org = UUID(int=8)
    workflow_source = UUID(int=11)
    workflow_target = UUID(int=12)
    agent_source = UUID(int=13)
    agent_target = UUID(int=14)
    event_source = UUID(int=15)
    event_target = UUID(int=16)
    config_target = UUID(int=17)
    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(
            workflows=[{
                "id": str(workflow_source), "name": "run", "path": "workflows/run.py",
                "function_name": "run",
            }],
            agents=[{
                "id": str(agent_source), "name": "helper", "tool_ids": [str(workflow_source)],
            }],
            events=[{
                "id": str(event_source), "name": "trigger", "source_type": "topic",
                "subscriptions": [{"id": str(UUID(int=19)), "workflow_id": str(workflow_source)}],
            }],
            config_schemas=[{"key": "API_URL", "type": "string", "default": "https://example.test"}],
        ),
        preview_id=UUID(int=7), organization_id=org,
    )
    workflow = next(iter(projection.manifest.workflows.values()))
    agent = next(iter(projection.manifest.agents.values()))
    event = next(iter(projection.manifest.events.values()))
    config = projection.manifest.configs["API_URL"]
    workflow_snapshot = workflow.model_dump(mode="json", exclude={"id"})
    agent_snapshot = agent.model_dump(mode="json", exclude={"id"})
    agent_snapshot["tool_ids"] = [str(workflow_target)]
    event_snapshot = event.model_dump(mode="json", exclude={"id"})
    event_snapshot["subscriptions"][0]["workflow_id"] = str(workflow_target)
    event_snapshot["subscriptions"][0]["id"] = str(UUID(int=20))
    config_snapshot = config.model_dump(mode="json", exclude={"id"})
    config_snapshot["value"] = {"value": "https://example.test"}
    existing = {
        ("workflow", ("workflows/run.py", "run")): (workflow_target, workflow_snapshot, False),
        ("agent", ("helper",)): (agent_target, agent_snapshot, False),
        ("event", ("trigger",)): (event_target, event_snapshot, False),
        ("config", ("API_URL", None, org)): (config_target, config_snapshot, True),
    }
    preview = WorkspaceBundlePlanner(None, preview_id=UUID(int=7), organization_id=org)._build_plan(
        projection, existing, {},
    ).preview
    assert {item.kind: item.classification for item in preview.items} == {
        "workflow": "unchanged", "agent": "unchanged", "event": "unchanged", "config": "unchanged",
    }
    assert preview.config_schemas[0]["requires_input"] is False


def test_table_preview_treats_the_import_seed_policy_as_unchanged() -> None:
    from bifrost.manifest import ManifestPolicy
    from shared.policies.probe import make_seed_admin_bypass
    from src.services.solutions.workspace_bundle_plan import (
        SolutionPackageWorkspaceProjection,
        WorkspaceBundlePlanner,
    )
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(tables=[{"id": str(UUID(int=10)), "name": "orders", "schema": {"columns": []}}]),
        preview_id=UUID(int=7),
    )
    table = next(iter(projection.manifest.tables.values()))
    existing = table.model_dump(mode="json", exclude={"id"})
    existing["policies"] = [
        ManifestPolicy.model_validate(policy).model_dump(mode="json")
        for policy in make_seed_admin_bypass()["policies"]
    ]
    preview = WorkspaceBundlePlanner(None, preview_id=UUID(int=7))._build_plan(
        projection, {("table", ("orders",)): (UUID(int=11), existing, False)}, {},
    ).preview
    assert preview.items[0].classification == "unchanged"


def test_kept_conflicts_remain_in_source_to_target_reference_map() -> None:
    from src.services.solutions.workspace_bundle_plan import WorkspaceBundlePlanner
    from src.models.contracts.solutions import WorkspaceBundleItem

    source = UUID("11111111-1111-1111-1111-111111111111")
    target = UUID("22222222-2222-2222-2222-222222222222")
    result = WorkspaceBundlePlanner.reference_map([
        WorkspaceBundleItem(
            id="workflow:run", kind="workflow", name="run",
            classification="conflict", source_id=source, target_id=target,
        )
    ])

    assert result == {source: target}


def test_projection_imports_claims_and_keeps_portable_role_names() -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(
            claims=[{
                "id": "11111111-1111-1111-1111-111111111111", "name": "claim",
                "type": "list",
                "query": {"table": "things", "where": {}, "select": "id"},
            }],
            workflows=[{
                "id": "22222222-2222-2222-2222-222222222222", "name": "run",
                "path": "workflows/run.py", "function_name": "run", "role_names": ["Operators"],
            }],
        ),
        preview_id=UUID(int=7),
    )

    claim = projection.manifest.claims["11111111-1111-1111-1111-111111111111"]
    assert claim.organization_id is None
    workflow = next(iter(projection.manifest.workflows.values()))
    assert workflow.role_names == ["Operators"]


def test_projection_strips_raw_role_uuids() -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(workflows=[{
            "id": "22222222-2222-2222-2222-222222222222", "name": "run",
            "path": "workflows/run.py", "function_name": "run", "roles": [],
        }]),
        preview_id=UUID(int=7),
    )

    workflow = next(iter(projection.manifest.workflows.values()))
    assert workflow.roles == []


def test_planner_lists_integration_shells() -> None:
    from uuid import UUID as _UUID

    from src.services.solutions.workspace_bundle_plan import (
        SolutionPackageWorkspaceProjection,
        WorkspaceBundlePlanner,
    )
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(
            connection_schemas=[
                {"integration_name": "acme", "template": {}, "position": 0},
            ],
            workflows=[{
                "id": "22222222-2222-2222-2222-222222222222", "name": "run",
                "path": "workflows/run.py", "function_name": "run",
                "role_names": ["Viewers"],
            }],
        ),
        preview_id=UUID(int=7),
    )

    planned = WorkspaceBundlePlanner(None, preview_id=UUID(int=7)).plan_sync(projection)
    shell = next(item for item in planned.preview.items if item.kind == "integration")
    assert shell.name == "acme"
    assert shell.classification == "create"
    assert shell.match_key == "acme"

    planned_known = WorkspaceBundlePlanner(None, preview_id=UUID(int=7))._build_plan(
        projection, {},
        {},
        existing_integrations={"acme": _UUID(int=9)},
    )
    shell_known = next(item for item in planned_known.preview.items if item.kind == "integration")
    assert shell_known.classification == "unchanged"
    assert shell_known.target_id == _UUID(int=9)


def test_planner_includes_hashed_source_files_and_detects_conflicts(tmp_path) -> None:
    from src.services.solutions.workspace_bundle_plan import (
        SolutionPackageWorkspaceProjection,
        WorkspaceBundlePlanner,
    )
    from src.services.solutions.zip_install import PreviewResult

    source = tmp_path / "modules" / "customer.py"
    source.parent.mkdir()
    source.write_text("value = 1\n")
    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(name="P"), preview_id=UUID(int=7), work_dir=tmp_path,
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    planned = WorkspaceBundlePlanner(None, preview_id=UUID(int=7)).plan_sync(
        projection, existing_file_hashes={"modules/customer.py": "different"},
    )
    item = next(item for item in planned.preview.items if item.kind == "file")

    assert item.classification == "conflict"
    assert planned.file_hashes == {"modules/customer.py": source_hash}
    assert planned.destination_file_hashes == {"modules/customer.py": "different"}

    unknown_destination = WorkspaceBundlePlanner(None, preview_id=UUID(int=7)).plan_sync(
        projection, existing_file_hashes={"modules/customer.py": None},
    )
    assert next(item for item in unknown_destination.preview.items if item.kind == "file").classification == "conflict"


def test_planner_excludes_secret_and_generated_source_files(tmp_path) -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection, WorkspaceBundlePlanner
    from src.services.solutions.zip_install import PreviewResult

    (tmp_path / ".env.production").write_text("TOKEN=secret")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("ignored")
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "kept.py").write_text("kept")
    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(name="P"), preview_id=UUID(int=7), work_dir=tmp_path,
    )

    planned = WorkspaceBundlePlanner(None, preview_id=UUID(int=7)).plan_sync(projection)

    assert planned.file_hashes == {"modules/kept.py": hashlib.sha256(b"kept").hexdigest()}


def test_planner_passes_large_incoming_paths_to_lookup_lazily(tmp_path, monkeypatch) -> None:
    """Previewing many paths must batch paths rather than retain a path list."""
    from src.services.solutions.workspace_bundle_plan import (
        SolutionPackageWorkspaceProjection,
        WorkspaceBundlePlanner,
    )
    from src.services.solutions.zip_install import PreviewResult
    from src.services.solutions import workspace_bundle_plan as plan_module

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(name="P"), preview_id=UUID(int=7), work_dir=tmp_path,
    )
    paths = (tmp_path / "modules" / f"file-{index}.py" for index in range(10_000))
    monkeypatch.setattr(plan_module, "iter_repo_files", lambda _root: paths)

    incoming = WorkspaceBundlePlanner._incoming_file_paths(projection)

    assert not isinstance(incoming, list)
    assert next(incoming) == "modules/file-0.py"
