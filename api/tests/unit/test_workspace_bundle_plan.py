"""The workspace import planner deliberately projects install packages first."""
from __future__ import annotations

import hashlib
from uuid import UUID


def test_solution_package_projection_normalizes_schema_without_coercing_it() -> None:
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
    assert config.value == "not-a-secret"
    assert "Solution config declarations import as global workspace configs; required and position are not retained." in projection.warnings


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


def test_projection_omits_package_only_claims_and_role_bindings_with_warnings() -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(
            claims=[{"id": "11111111-1111-1111-1111-111111111111", "name": "claim", "type": "string"}],
            workflows=[{
                "id": "22222222-2222-2222-2222-222222222222", "name": "run",
                "path": "workflows/run.py", "function_name": "run", "role_names": ["Operators"],
            }],
        ),
        preview_id=UUID(int=7),
    )

    assert projection.manifest.claims == {}
    assert any("Custom claims" in warning for warning in projection.warnings)
    assert any("Role bindings" in warning for warning in projection.warnings)


def test_projection_warns_when_package_explicitly_declares_empty_roles() -> None:
    from src.services.solutions.workspace_bundle_plan import SolutionPackageWorkspaceProjection
    from src.services.solutions.zip_install import PreviewResult

    projection = SolutionPackageWorkspaceProjection.from_preview(
        PreviewResult(workflows=[{
            "id": "22222222-2222-2222-2222-222222222222", "name": "run",
            "path": "workflows/run.py", "function_name": "run", "roles": [],
        }]),
        preview_id=UUID(int=7),
    )

    assert any("Role bindings" in warning for warning in projection.warnings)


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
