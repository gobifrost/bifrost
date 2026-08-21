"""Forward migration coverage for the Agent Skill revision column."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260819_agent_skill_revision.py"
)
SPEC = importlib.util.spec_from_file_location(
    "agent_skill_revision_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_revision_chains_to_the_policy_rules_slice() -> None:
    assert MIGRATION.revision == "20260819_agent_skill_rev"
    assert MIGRATION.down_revision == "20260819_policy_rule_names"


def test_revision_id_fits_the_alembic_version_column() -> None:
    """``alembic_version.version_num`` is varchar(32)."""
    assert len(MIGRATION.revision) <= 32


def test_column_is_nullable() -> None:
    """The digest covers object-storage content a migration cannot read.

    Backfilling is therefore impossible in SQL; NULL means "not yet computed"
    and read paths tolerate it. If this ever becomes NOT NULL, the lazy
    resolve path in agent_skills.resolve_agent_skill_revision must be revisited.
    """
    import sqlalchemy as sa

    added: list[sa.Column] = []
    original = MIGRATION.op.add_column
    try:
        MIGRATION.op.add_column = lambda table, column: added.append(column)
        MIGRATION.upgrade()
    finally:
        MIGRATION.op.add_column = original

    assert len(added) == 1
    column = added[0]
    assert column.name == "skill_revision"
    assert column.nullable is True
    assert column.type.length == 64, "sha256 hex is 64 characters"
