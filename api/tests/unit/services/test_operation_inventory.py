"""Surface-accounting and generated-reference tripwires."""

from pathlib import Path

from src.main import app
from src.services.operation_inventory import build_operation_inventory


API_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = API_ROOT.parent


def test_every_observed_surface_is_classified_with_a_reason() -> None:
    inventory = build_operation_inventory(app, REPO_ROOT)
    assert inventory["counts"] == {
        "cli": 140,
        "manifest": 16,
        # 104 after the policy-rule slice added get / update / list-usages.
        "mcp": 104,
        "native_builder": 10,
        # 662 after configs.get added GET /api/config/{config_id} and
        # policy.rules.get added GET /api/policy-rules/{domain}/{name}.
        "rest": 662,
        "sdk": 19,
    }
    for surface, rows in inventory["uncataloged"].items():
        assert rows, f"expected {surface} inventory coverage"
        for row in rows:
            assert row["status"]
            assert row["reason"]


def test_builder_local_mcp_tools_are_dispositioned_not_pending() -> None:
    """Builder workspace primitives and the docs reader never enter the catalog.

    They act on a Builder session's scratch workspace or on generated content
    rather than on a platform entity, so reporting them as catalog work still
    to do would overstate the remaining surface.
    """
    inventory = build_operation_inventory(app, REPO_ROOT)
    dispositioned = {
        row["name"]: row
        for row in inventory["uncataloged"]["mcp"]
        if row["status"] == "transport_only"
    }

    assert set(dispositioned) == {
        "apply_patch",
        "delete_file",
        "get_docs",
        "list_files",
        "make_directory",
        "read_file",
        "read_skill_asset",
        "search_text",
        "test_solution_build",
        "validate_solution",
        "write_file",
    }
    for row in dispositioned.values():
        assert "has not entered" not in row["reason"], row


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
        expected_builder_status = (
            "intentionally_unsupported"
            if row["operation"]["exclusions"].get("native_builder")
            else "missing_surface"
        )
        assert observed["native_builder"]["status"] == expected_builder_status


def test_generated_operation_files_are_fresh() -> None:
    import importlib.util

    generator_path = API_ROOT / "scripts" / "operation_catalog" / "generate.py"
    spec = importlib.util.spec_from_file_location("operation_catalog_generate", generator_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.INVENTORY_PATH.read_text(encoding="utf-8") == module.render_inventory()
    assert module.OPERATIONS_PATH.read_text(encoding="utf-8") == module.render_operations()
