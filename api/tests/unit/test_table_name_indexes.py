"""ORM contract for live _repo/ table-name uniqueness."""

from src.models.orm.tables import Table


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
