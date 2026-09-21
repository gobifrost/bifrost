"""Saved matrix helpers: cell planning, profile override, dedupe keys."""
from uuid import uuid4

import pytest

from src.services.agent_evaluations import quotas as eval_quotas
from src.services.agent_evaluations.executions import (
    apply_profile_override,
    build_dedupe_key,
    plan_matrix_cells,
)
from src.services.llm.base import LLMConfig


def test_plan_matrix_cells_pairs_every_candidate_with_same_profile_baseline():
    candidate = uuid4()
    profiles = [uuid4(), uuid4()]
    cells = plan_matrix_cells([candidate], profiles)
    assert cells == [
        (None, profiles[0]),
        (None, profiles[1]),
        (candidate, profiles[0]),
        (candidate, profiles[1]),
    ]


def test_plan_matrix_cells_baseline_only_without_candidates():
    profile = uuid4()
    assert plan_matrix_cells([], [profile]) == [(None, profile)]


def test_plan_matrix_cells_rejects_oversized_fan_out(monkeypatch):
    monkeypatch.setattr(eval_quotas, "MAX_MATRIX_CELLS", 2)
    with pytest.raises(eval_quotas.QuotaExceeded):
        plan_matrix_cells([uuid4()], [uuid4(), uuid4()])


def _selected_config() -> LLMConfig:
    return LLMConfig(
        provider="openai",
        model="selected-model-v2",
        api_key="live-secret-never-frozen",
        endpoint="https://selected.example/v1",
        openai_transport="responses",
        anthropic_prompt_cache_supported=True,
        default_max_tokens=4096,
        extra_params={"reasoning": {"effort": "high"}},
    )


def test_apply_profile_override_copies_without_mutating():
    snapshot = {"model": {"provider": "openai", "model": "gpt-x"}}
    profile_id = uuid4()
    frozen = apply_profile_override(
        snapshot, profile_id, model_config=_selected_config()
    )
    assert frozen["model"]["profile_id"] == str(profile_id)
    assert frozen["model"]["provider"] == "openai"
    assert "profile_id" not in snapshot["model"]


def test_apply_profile_override_freezes_full_runtime_config():
    """The executor pins provider/model/endpoint/etc. from the snapshot.

    Stamping only profile_id would run the selected credentials against
    the original model/endpoint, so admission freezes every non-secret
    selected value on both sides while keeping the Agent token override.
    """
    snapshot = {
        "model": {
            "profile_id": "original-profile",
            "provider": "anthropic",
            "model": "original-model",
            "endpoint": "https://original.example/v1",
            "openai_transport": "chat_completions",
            "anthropic_prompt_cache_supported": False,
            "default_max_tokens": 1024,
            "llm_max_tokens": 777,
            "extra_params": {"old": True},
        }
    }
    profile_id = uuid4()
    frozen = apply_profile_override(
        snapshot, profile_id, model_config=_selected_config()
    )
    model = frozen["model"]
    assert model["profile_id"] == str(profile_id)
    assert model["provider"] == "openai"
    assert model["model"] == "selected-model-v2"
    assert model["endpoint"] == "https://selected.example/v1"
    assert model["openai_transport"] == "responses"
    assert model["anthropic_prompt_cache_supported"] is True
    assert model["default_max_tokens"] == 4096
    assert model["extra_params"] == {"reasoning": {"effort": "high"}}
    # Agent-level token override survives; secrets never freeze.
    assert model["llm_max_tokens"] == 777
    assert "api_key" not in model
    assert "provider_connection_id" not in model
    # Input untouched.
    assert snapshot["model"]["model"] == "original-model"
    assert snapshot["model"]["endpoint"] == "https://original.example/v1"


def test_dedupe_key_distinguishes_profiles():
    suite_id = uuid4()
    candidate_id = uuid4()
    profile_a, profile_b = uuid4(), uuid4()
    assert build_dedupe_key(suite_id, 1, candidate_id) == build_dedupe_key(
        suite_id, 1, candidate_id
    )
    assert build_dedupe_key(
        suite_id, 1, candidate_id, profile_a
    ) != build_dedupe_key(suite_id, 1, candidate_id, profile_b)
    assert build_dedupe_key(suite_id, 1, None, profile_a) != build_dedupe_key(
        suite_id, 1, candidate_id, profile_a
    )


def test_dedupe_key_includes_repetitions():
    suite_id = uuid4()
    assert build_dedupe_key(suite_id, 1, None) == build_dedupe_key(
        suite_id, 1, None, None, None
    )
    assert build_dedupe_key(suite_id, 1, None, None, 1) != build_dedupe_key(
        suite_id, 1, None, None, 2
    )
    assert build_dedupe_key(suite_id, 1, None, None, None) != build_dedupe_key(
        suite_id, 1, None, None, 1
    )


MIGRATION_PATH = (
    __import__("pathlib").Path(__file__).resolve().parents[4]
    / "alembic"
    / "versions"
    / "20260919_matrix_cells.py"
)


def _load_matrix_cells_migration():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_matrix_cells_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_membership_backfill_sql_targets_matrix_members(monkeypatch):
    """The backfill statement aggregates execution.matrix_id per matrix."""
    migration = _load_matrix_cells_migration()
    sql = " ".join(migration.BACKFILL_MEMBERSHIP_SQL.split())
    assert "UPDATE agent_evaluation_matrixes" in sql
    assert "agent_evaluation_executions" in sql
    assert "jsonb_agg" in sql
    assert "matrix_id" in sql
    assert "cell_execution_ids" in sql
    # upgrade() actually runs the backfill (Docker-proven below).
    import inspect

    assert "BACKFILL_MEMBERSHIP_SQL" in inspect.getsource(migration.upgrade)


@pytest.mark.asyncio
async def test_membership_backfill_recovers_pre_migration_rows(
    db_session, seed_agent, seed_user
):
    """Pre-migration rows (matrix_id set, membership empty) regain GET/cancel."""
    from sqlalchemy import text

    from src.models.orm.agent_evaluations import (
        AgentEvaluationExecution,
        AgentEvaluationMatrix,
        AgentEvaluationSuite,
    )

    migration = _load_matrix_cells_migration()
    suite = AgentEvaluationSuite(
        id=uuid4(), org_id=None, agent_id=seed_agent.id, name="backfill-suite"
    )
    db_session.add(suite)
    await db_session.flush()
    matrix = AgentEvaluationMatrix(
        id=uuid4(),
        suite_id=suite.id,
        suite_version=1,
        candidate_ids=[],
        profile_ids=[],
        cell_execution_ids=[],
    )
    db_session.add(matrix)
    await db_session.flush()
    execution_ids = [uuid4(), uuid4()]
    for execution_id in execution_ids:
        db_session.add(
            AgentEvaluationExecution(
                id=execution_id,
                suite_id=suite.id,
                suite_version=1,
                status="queued",
                matrix_id=matrix.id,
            )
        )
    await db_session.flush()

    await db_session.execute(text(migration.BACKFILL_MEMBERSHIP_SQL))

    await db_session.refresh(matrix)
    assert sorted(matrix.cell_execution_ids) == sorted(
        str(execution_id) for execution_id in execution_ids
    )
