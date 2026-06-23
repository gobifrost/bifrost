"""Tests for FileMetadata and FilePolicy ORM models."""


def test_file_metadata_columns_and_indexes() -> None:
    from src.models.orm.file_metadata import FileMetadata

    columns = {c.name for c in FileMetadata.__table__.columns}

    assert {
        "id",
        "organization_id",
        "location",
        "path",
        "s3_key",
        "content_type",
        "size_bytes",
        "sha256",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    } <= columns
    assert [c.name for c in FileMetadata.__table__.primary_key.columns] == ["id"]
    index_names = {idx.name for idx in FileMetadata.__table__.indexes}
    assert "ix_file_metadata_organization_id" in index_names
    assert "uq_file_metadata_org_location_path" in index_names
    assert "uq_file_metadata_global_location_path" in index_names


def test_file_policy_columns_and_indexes() -> None:
    from src.models.orm.file_metadata import FilePolicy

    columns = {c.name for c in FilePolicy.__table__.columns}

    assert {
        "id",
        "organization_id",
        "location",
        "path",
        "policies",
        "created_by",
        "created_at",
        "updated_at",
    } <= columns
    assert [c.name for c in FilePolicy.__table__.primary_key.columns] == ["id"]
    index_names = {idx.name for idx in FilePolicy.__table__.indexes}
    assert "ix_file_policies_organization_id" in index_names
    assert "uq_file_policies_org_location_path" in index_names
    assert "uq_file_policies_global_location_path" in index_names
