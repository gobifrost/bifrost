"""Surface-accounting and generated-reference tripwires."""

from pathlib import Path

from src.main import app
from src.services.operation_inventory import build_operation_inventory


API_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = API_ROOT.parent


def test_every_observed_surface_is_classified_with_a_reason() -> None:
    inventory = build_operation_inventory(app, REPO_ROOT)
    assert inventory["counts"] == {
        "cli": 135,
        "manifest": 16,
        "mcp": 101,
        "native_builder": 10,
        "rest": 658,
        "sdk": 19,
    }
    for surface, rows in inventory["uncataloged"].items():
        assert rows, f"expected {surface} inventory coverage"
        for row in rows:
            assert row["status"]
            assert row["reason"]


def test_catalog_vertical_slices_report_rest_cli_and_mcp_parity() -> None:
    inventory = build_operation_inventory(app, REPO_ROOT)
    operations = {
        row["operation"]["operation_id"]: row
        for row in inventory["catalog_operations"]
    }
    for operation_id, row in operations.items():
        observed = row["observed"]
        assert observed["rest"]["status"] == "exact_parity"
        assert observed["cli"]["status"] == "exact_parity"
        assert observed["mcp"] == {
            "status": "exact_parity",
            "name": row["operation"]["mcp"]["name"],
        }
        assert observed["native_builder"]["status"] == "missing_surface"


def test_generated_operation_files_are_fresh() -> None:
    import importlib.util

    generator_path = API_ROOT / "scripts" / "operation_catalog" / "generate.py"
    spec = importlib.util.spec_from_file_location("operation_catalog_generate", generator_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.INVENTORY_PATH.read_text(encoding="utf-8") == module.render_inventory()
    assert module.OPERATIONS_PATH.read_text(encoding="utf-8") == module.render_operations()
