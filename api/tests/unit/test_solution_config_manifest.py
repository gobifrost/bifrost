"""configs.yaml round-trip: declarations only, never a value."""
import pathlib
import textwrap

from bifrost.commands.solution import _collect_config_schemas, _collect_file_locations


def test_collect_config_schemas_reads_declarations(tmp_path: pathlib.Path) -> None:
    bdir = tmp_path / ".bifrost"
    bdir.mkdir()
    (bdir / "configs.yaml").write_text(textwrap.dedent("""
        configs:
          STRIPE_KEY:
            id: 11111111-1111-1111-1111-111111111111
            key: STRIPE_KEY
            type: secret
            required: true
            description: Stripe secret key
          REGION:
            id: 22222222-2222-2222-2222-222222222222
            key: REGION
            type: string
            required: false
            default: us-east
            description: Region
    """))
    entries = _collect_config_schemas(tmp_path)
    by_key = {e["key"]: e for e in entries}
    assert by_key["STRIPE_KEY"]["required"] is True
    assert by_key["STRIPE_KEY"]["type"] == "secret"
    assert "value" not in by_key["STRIPE_KEY"]
    assert by_key["REGION"]["default"] == "us-east"


def test_collect_config_schemas_missing_file_returns_empty(tmp_path: pathlib.Path) -> None:
    assert _collect_config_schemas(tmp_path) == []


def test_collect_file_locations_reads_top_level_locations(tmp_path: pathlib.Path) -> None:
    bdir = tmp_path / ".bifrost"
    bdir.mkdir()
    (bdir / "files.yaml").write_text(textwrap.dedent("""
        locations:
          - reports
          - invoices
    """))

    assert _collect_file_locations(tmp_path) == ["reports", "invoices"]


def test_collect_file_locations_missing_file_returns_empty(tmp_path: pathlib.Path) -> None:
    assert _collect_file_locations(tmp_path) == []
