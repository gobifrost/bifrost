"""The workspace import planner deliberately projects install packages first."""
from __future__ import annotations

import hashlib
from uuid import UUID


def test_solution_package_projection_exposes_config_fields_without_secret_default() -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection
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
    import src.services.solutions.workspace_bundle_plan as plan_module

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(name="P"), preview_id=UUID(int=7), work_dir=tmp_path,
    )
    paths = (tmp_path / "modules" / f"file-{index}.py" for index in range(10_000))
    monkeypatch.setattr(plan_module, "iter_repo_files", lambda _root: paths)

    incoming = WorkspaceBundlePlanner._incoming_file_paths(projection)

    assert not isinstance(incoming, list)
    assert next(incoming) == "modules/file-0.py"
