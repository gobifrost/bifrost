"""claims.yaml round-trip into Solution deploy bundles."""

import pathlib
import textwrap

from bifrost.commands.solution import _collect_claims


def test_collect_claims_reads_definitions(tmp_path: pathlib.Path) -> None:
    bdir = tmp_path / ".bifrost"
    bdir.mkdir()
    (bdir / "claims.yaml").write_text(textwrap.dedent("""
        claims:
          11111111-1111-1111-1111-111111111111:
            id: 11111111-1111-1111-1111-111111111111
            name: allowed_campus_ids
            description: Campus grants
            type: list
            query:
              table: memberships
              select: campus_id
    """))

    entries = _collect_claims(tmp_path)

    assert entries == [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "allowed_campus_ids",
            "description": "Campus grants",
            "type": "list",
            "query": {"table": "memberships", "select": "campus_id"},
        }
    ]


def test_collect_claims_missing_file_returns_empty(tmp_path: pathlib.Path) -> None:
    assert _collect_claims(tmp_path) == []
