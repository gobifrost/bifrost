"""ORM contract for live _repo/ table-name uniqueness."""

from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from src.models.orm.tables import Table, is_repo_table_name_conflict


def test_table_model_declares_repo_name_indexes_without_orphan_provenance() -> None:
    indexes = {index.name: index for index in Table.__table__.indexes}

    assert indexes["ix_tables_org_name_unique"].unique is True
    assert indexes["ix_tables_global_name_unique"].unique is True
    assert indexes["ix_tables_solution_name_unique"].unique is True
    assert str(indexes["ix_tables_org_name_unique"].dialect_options["postgresql"]["where"]) == (
        "organization_id IS NOT NULL AND solution_id IS NULL"
    )
    assert str(indexes["ix_tables_global_name_unique"].dialect_options["postgresql"]["where"]) == (
        "organization_id IS NULL AND solution_id IS NULL"
    )
    assert str(indexes["ix_tables_solution_name_unique"].dialect_options["postgresql"]["where"]) == (
        "solution_id IS NOT NULL"
    )


def test_repo_table_unique_index_violation_is_identified() -> None:
    index_error = IntegrityError(
        "INSERT",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(constraint_name="ix_tables_org_name_unique")
        ),
    )
    unrelated_error = IntegrityError(
        "INSERT",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(constraint_name="tables_organization_id_fkey")
        ),
    )
    asyncpg_error = IntegrityError(
        "INSERT",
        {},
        SimpleNamespace(constraint_name="ix_tables_global_name_unique"),
    )
    asyncpg_adapter_error = IntegrityError(
        "INSERT",
        {},
        SimpleNamespace(
            __cause__=SimpleNamespace(
                constraint_name="ix_tables_org_name_unique"
            )
        ),
    )

    assert is_repo_table_name_conflict(index_error)
    assert is_repo_table_name_conflict(asyncpg_error)
    assert is_repo_table_name_conflict(asyncpg_adapter_error)
    assert not is_repo_table_name_conflict(unrelated_error)
