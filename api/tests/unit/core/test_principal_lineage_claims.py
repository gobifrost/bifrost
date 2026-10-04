"""The principal reads the run-lineage claims minted on execution and bridge tokens."""

from __future__ import annotations

from uuid import uuid4

from src.core.principal import run_lineage_fields


def test_lineage_claims_become_principal_fields() -> None:
    run_user, started_by, root, workflow = uuid4(), uuid4(), uuid4(), uuid4()
    payload = {
        "engine_run_user_id": str(run_user),
        "engine_started_by_user_id": str(started_by),
        "engine_root_execution_id": str(root),
        "engine_workflow_id": str(workflow),
    }

    assert run_lineage_fields(payload) == {
        "run_user_id": run_user,
        "started_by_user_id": started_by,
        "root_execution_id": root,
        "workflow_id": workflow,
    }


def test_a_token_without_lineage_claims_has_none() -> None:
    assert run_lineage_fields({"sub": str(uuid4())}) == {
        "run_user_id": None,
        "started_by_user_id": None,
        "root_execution_id": None,
        "workflow_id": None,
    }
