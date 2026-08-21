"""Forward migration coverage for global Builder operation changes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260823_global_operation_changesets.py"
)
SPEC = importlib.util.spec_from_file_location(
    "global_operation_changes_migration",
    MIGRATION_PATH,
)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_revision_chains_after_artifact_workspace_tombstones() -> None:
    assert MIGRATION.revision == "20260823_global_op_changes"
    assert MIGRATION.down_revision == "20260822_artifact_tombstones"
    assert len(MIGRATION.revision) <= 32


def test_migration_defines_global_operation_changes_table() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "solution_global_operation_changes" in source
    assert "operation_id" in source
    assert "before_fingerprint" in source
    assert "ck_solution_global_operation_changes_state" in source
