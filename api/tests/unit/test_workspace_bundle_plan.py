"""The workspace import planner deliberately projects install packages first."""
from __future__ import annotations

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
