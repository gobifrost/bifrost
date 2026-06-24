"""
TDD: Solution.status column + removal of orphan provenance columns.

Tests:
- Solution.status defaults to "active" at construction time (no flush needed).
- Table/Config/FileMetadata/FilePolicy no longer carry the orphan provenance
  columns (origin_solution_slug, origin_solution_id, orphaned_at).
"""

from src.models.orm.config import Config
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table


def test_solution_has_status_default_active():
    s = Solution(slug="x", name="X")
    assert s.status == "active"


def test_orphan_columns_removed():
    for M in (Table, Config, FileMetadata, FilePolicy):
        cols = set(M.__table__.columns.keys())
        assert "origin_solution_slug" not in cols, M.__name__
        assert "origin_solution_id" not in cols, M.__name__
        assert "orphaned_at" not in cols, M.__name__
