"""Forward migration coverage for artifact workspace tombstones."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260822_artifact_tombstones.py"
)
SPEC = importlib.util.spec_from_file_location(
    "artifact_workspace_tombstone_migration",
    MIGRATION_PATH,
)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_revision_chains_after_builder_organization_target() -> None:
    assert MIGRATION.revision == "20260822_artifact_tombstones"
    assert MIGRATION.down_revision == "20260821_builder_org_target"
    assert len(MIGRATION.revision) <= 32


def test_migration_defines_logical_workspace_tombstone_table() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "artifact_workspace_tombstones" in source
    assert "workspace_id" in source
    assert "logical_path" in source
    assert "uq_artifact_workspace_tombstones_path" in source
    assert "ondelete=\"SET NULL\"" in source
