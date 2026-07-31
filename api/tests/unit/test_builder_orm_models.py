"""Metadata-level checks on the private Solution builder ORM models.

Pure SQLAlchemy inspection — no DB. Guards the wiring the builder depends on:
cascade behavior on delete, the self-referential revision lineage, server
defaults for status columns, and timezone-aware timestamps.
"""

from __future__ import annotations

from sqlalchemy import Table

from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)


def _fk_targets(table: Table, column: str) -> set[tuple[str, str | None]]:
    """(referenced "table.column", ondelete) pairs for one column's foreign keys."""
    return {
        (fk.target_fullname, fk.ondelete)
        for fk in table.foreign_keys
        if fk.parent.name == column
    }


def test_table_names() -> None:
    assert SolutionBuilderProject.__tablename__ == "solution_builder_projects"
    assert SolutionSourceRevision.__tablename__ == "solution_source_revisions"
    assert SolutionBuilderSession.__tablename__ == "solution_builder_sessions"
    assert SolutionBuilderTurn.__tablename__ == "solution_builder_turns"


def test_project_is_keyed_by_solution_and_cascades() -> None:
    table = SolutionBuilderProject.__table__
    assert [c.name for c in table.primary_key.columns] == ["solution_id"]
    assert _fk_targets(table, "solution_id") == {("solutions.id", "CASCADE")}


def test_project_revision_pointers_are_nullable_and_use_alter() -> None:
    table = SolutionBuilderProject.__table__
    for column in (
        "current_revision_id",
        "deployed_revision_id",
        "promotion_revision_id",
    ):
        assert table.columns[column].nullable
        assert _fk_targets(table, column) == {("solution_source_revisions.id", "SET NULL")}

    named = {fk.constraint.name for fk in table.foreign_keys if fk.use_alter}
    assert named == {
        "fk_solution_builder_projects_current_revision_id",
        "fk_solution_builder_projects_deployed_revision_id",
        "fk_solution_builder_projects_promotion_revision_id",
    }


def test_project_promotion_status_defaults_to_none() -> None:
    column = SolutionBuilderProject.__table__.columns["promotion_status"]
    assert column.default is not None and column.default.arg == "none"
    assert column.server_default is not None and column.server_default.arg == "none"
    assert not column.nullable


def test_revision_lineage_is_self_referential() -> None:
    table = SolutionSourceRevision.__table__
    assert _fk_targets(table, "solution_id") == {("solutions.id", "CASCADE")}
    for column in ("parent_revision_id", "restored_from_revision_id"):
        assert table.columns[column].nullable
        assert _fk_targets(table, column) == {("solution_source_revisions.id", "SET NULL")}


def test_revision_soft_references_survive_owner_deletion() -> None:
    table = SolutionSourceRevision.__table__
    assert _fk_targets(table, "conversation_id") == {("conversations.id", "SET NULL")}
    assert _fk_targets(table, "created_by") == {("users.id", "SET NULL")}


def test_revision_content_identity_columns_are_required() -> None:
    columns = SolutionSourceRevision.__table__.columns
    assert not columns["source_sha256"].nullable
    assert columns["source_sha256"].type.length == 64
    assert not columns["size_bytes"].nullable
    assert columns["summary"].nullable


def test_revision_history_index_declared() -> None:
    names = {i.name: [c.name for c in i.columns] for i in SolutionSourceRevision.__table__.indexes}
    assert names["ix_solution_source_revisions_solution_created"] == ["solution_id", "created_at"]


def test_session_cascades_from_every_owner() -> None:
    table = SolutionBuilderSession.__table__
    assert _fk_targets(table, "solution_id") == {("solutions.id", "CASCADE")}
    assert _fk_targets(table, "conversation_id") == {("conversations.id", "CASCADE")}
    assert _fk_targets(table, "user_id") == {("users.id", "CASCADE")}
    for column in ("solution_id", "conversation_id", "user_id"):
        assert not table.columns[column].nullable


def test_turn_wiring() -> None:
    table = SolutionBuilderTurn.__table__
    assert _fk_targets(table, "session_id") == {("solution_builder_sessions.id", "CASCADE")}
    assert _fk_targets(table, "requested_by") == {("users.id", "SET NULL")}
    for column in ("base_revision_id", "output_revision_id"):
        assert _fk_targets(table, column) == {("solution_source_revisions.id", "SET NULL")}


def test_turn_job_ids_carry_no_foreign_keys() -> None:
    table = SolutionBuilderTurn.__table__
    for column in ("build_job_id", "deploy_job_id"):
        assert table.columns[column].nullable
        assert _fk_targets(table, column) == set()


def test_turn_status_defaults_to_queued() -> None:
    column = SolutionBuilderTurn.__table__.columns["status"]
    assert column.default is not None and column.default.arg == "queued"
    assert column.server_default is not None and column.server_default.arg == "queued"
    assert not column.nullable


def test_all_datetime_columns_are_timezone_aware() -> None:
    models = (
        SolutionBuilderProject,
        SolutionSourceRevision,
        SolutionBuilderSession,
        SolutionBuilderTurn,
    )
    for model in models:
        for column in model.__table__.columns:
            if hasattr(column.type, "timezone"):
                assert column.type.timezone, f"{model.__tablename__}.{column.name}"


def test_created_at_has_python_and_server_default() -> None:
    models = (
        SolutionBuilderProject,
        SolutionSourceRevision,
        SolutionBuilderSession,
        SolutionBuilderTurn,
    )
    for model in models:
        column = model.__table__.columns["created_at"]
        assert column.default is not None and callable(column.default.arg)
        assert column.server_default is not None
        assert not column.nullable


def test_updated_at_refreshes_on_update() -> None:
    for model in (SolutionBuilderProject, SolutionBuilderSession):
        column = model.__table__.columns["updated_at"]
        assert column.onupdate is not None and callable(column.onupdate.arg)
