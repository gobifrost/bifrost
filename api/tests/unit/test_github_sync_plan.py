from src.models.contracts.github import (
    EntityChange,
    SyncResult,
    WorkspaceFileChange,
    WorkspaceSyncPlan,
)


def test_workspace_sync_plan_round_trips() -> None:
    plan = WorkspaceSyncPlan(
        base_sha="base",
        merge_sha="merge",
        pending_deletes=[
            EntityChange(action="removed", entity_type="workflow", name="legacy")
        ],
        entity_changes=[
            EntityChange(action="updated", entity_type="app", name="portal")
        ],
        file_changes=[
            WorkspaceFileChange(
                path="apps/portal/App.tsx", action="update", sha256="a" * 64
            ),
            WorkspaceFileChange(path="workflows/legacy.py", action="delete"),
        ],
    )

    assert WorkspaceSyncPlan.model_validate_json(plan.model_dump_json()) == plan


def test_action_required_sync_result_is_not_successful() -> None:
    result = SyncResult(requires_action="confirm_deletes")

    assert result.success is False
    assert result.requires_action == "confirm_deletes"
