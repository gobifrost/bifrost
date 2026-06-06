"""configs.yaml round-trip: declarations only, never a value."""
import pathlib
import textwrap

from bifrost.commands.solution import _collect_config_schemas


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
