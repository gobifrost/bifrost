"""Tests for FileIndex ORM model."""


def test_file_index_model_exists():
    """FileIndex model can be imported."""
    from src.models.orm.file_index import FileIndex
    assert FileIndex.__tablename__ == "file_index"


def test_file_index_columns():
    """FileIndex has expected columns."""
    from src.models.orm.file_index import FileIndex
    columns = {c.name for c in FileIndex.__table__.columns}
    assert "path" in columns
    assert "content" in columns
    assert "content_hash" in columns
    assert "updated_at" in columns


def test_file_index_primary_key():
    """Path is the primary key."""
    from src.models.orm.file_index import FileIndex
    pk_cols = [c.name for c in FileIndex.__table__.primary_key.columns]
    assert pk_cols == ["path"]


def test_solution_file_index_is_keyed_by_install_and_path():
    """Solution source rows are keyed per install, so equal relative paths never collide."""
    from src.models.orm.file_index import SolutionFileIndex
    pk_cols = [c.name for c in SolutionFileIndex.__table__.primary_key.columns]
    assert pk_cols == ["solution_id", "path"]
    (fk,) = SolutionFileIndex.__table__.c.solution_id.foreign_keys
    assert (fk.target_fullname, fk.ondelete) == ("solutions.id", "CASCADE")
