"""TDD tests for solution_id column on FileMetadata & FilePolicy."""

from src.models.orm.file_metadata import FileMetadata, FilePolicy


def test_solution_columns_present():
    """solution_id column must exist on both models (orphan-provenance columns were removed)."""
    for M in (FileMetadata, FilePolicy):
        assert "solution_id" in M.__table__.columns, f"{M.__name__}.solution_id missing"


def test_solution_partial_unique_file_metadata():
    """uq_file_metadata_solution_location_path unique index must exist."""
    names = {i.name for i in FileMetadata.__table__.indexes if i.unique}
    assert "uq_file_metadata_solution_location_path" in names


def test_solution_partial_unique_file_policies():
    """uq_file_policies_solution_location_path unique index must exist."""
    names = {i.name for i in FilePolicy.__table__.indexes if i.unique}
    assert "uq_file_policies_solution_location_path" in names


def test_existing_unique_indexes_exclude_solution_rows():
    """
    The org and global partial-unique predicates must include 'solution_id IS NULL'
    so that solution rows are not ambiguous against org/global rows.
    All three index tiers must be mutually exclusive:
      - org tier:      organization_id IS NOT NULL AND solution_id IS NULL
      - global tier:   organization_id IS NULL AND solution_id IS NULL
      - solution tier: solution_id IS NOT NULL
    """
    for M, prefix in (
        (FileMetadata, "uq_file_metadata"),
        (FilePolicy, "uq_file_policies"),
    ):
        idx_map = {i.name: i for i in M.__table__.indexes if i.unique}

        # Check org index predicate includes solution_id IS NULL
        org_idx = idx_map.get(f"{prefix}_org_location_path")
        assert org_idx is not None, f"{prefix}_org_location_path not found"
        org_where = str(org_idx.dialect_options.get("postgresql", {}).get("where", ""))
        assert "solution_id IS NULL" in org_where, (
            f"{prefix}_org_location_path predicate must include 'solution_id IS NULL', got: {org_where}"
        )
        assert "organization_id IS NOT NULL" in org_where, (
            f"{prefix}_org_location_path predicate must include 'organization_id IS NOT NULL', got: {org_where}"
        )

        # Check global index predicate includes solution_id IS NULL
        global_idx = idx_map.get(f"{prefix}_global_location_path")
        assert global_idx is not None, f"{prefix}_global_location_path not found"
        global_where = str(global_idx.dialect_options.get("postgresql", {}).get("where", ""))
        assert "solution_id IS NULL" in global_where, (
            f"{prefix}_global_location_path predicate must include 'solution_id IS NULL', got: {global_where}"
        )
        assert "organization_id IS NULL" in global_where, (
            f"{prefix}_global_location_path predicate must include 'organization_id IS NULL', got: {global_where}"
        )

        # Check solution index predicate
        solution_idx = idx_map.get(f"{prefix}_solution_location_path")
        assert solution_idx is not None, f"{prefix}_solution_location_path not found"
        solution_where = str(solution_idx.dialect_options.get("postgresql", {}).get("where", ""))
        assert "solution_id IS NOT NULL" in solution_where, (
            f"{prefix}_solution_location_path predicate must include 'solution_id IS NOT NULL', got: {solution_where}"
        )
