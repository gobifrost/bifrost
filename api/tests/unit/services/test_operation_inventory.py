"""Surface-accounting and generated-reference tripwires."""

from pathlib import Path

from src.main import app
from src.services.operation_inventory import build_operation_inventory


API_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = API_ROOT.parent


def test_every_observed_surface_is_classified_with_a_reason() -> None:
    inventory = build_operation_inventory(app, REPO_ROOT)
    # R1a snapshot against current main. There is no Builder runtime on main
    # yet (native_builder is a Builder-phase surface), and cli/mcp counts
    # reflect main's pre-rename tool/command surface: R1b lands each domain's
    # MCP thin-wrapper rewrite and tool-name migration, closing that parity
    # domain by domain.
    assert inventory["counts"] == {
        "cli": 155,
        "manifest": 16,
        "mcp": 94,
        "rest": 665,
        "sdk": 21,
    }
    for surface, rows in inventory["uncataloged"].items():
        assert rows, f"expected {surface} inventory coverage"
        for row in rows:
            assert row["status"]
            assert row["reason"]


def test_organization_listing_is_available_to_application_sdks() -> None:
    inventory = build_operation_inventory(app, REPO_ROOT)
    operation = next(
        row for row in inventory["catalog_operations"]
        if row["operation"]["operation_id"] == "organizations.list"
    )

    assert operation["observed"]["sdk"] == {
        "status": "exact_parity",
        "binding": {"method": "GET", "path": "/api/organizations"},
    }
    assert "sdk" not in operation["operation"]["exclusions"]


def test_documentation_mcp_tool_is_dispositioned_not_pending() -> None:
    """The docs reader never enters the catalog.

    It returns generated platform documentation and owns no entity or state,
    so reporting it as catalog work still to do would overstate the remaining
    surface. (Main has no Builder runtime yet, so there is no Builder-local
    tool set to disposition alongside it.)
    """
    inventory = build_operation_inventory(app, REPO_ROOT)
    dispositioned = {
        row["name"]: row
        for row in inventory["uncataloged"]["mcp"]
        if row["status"] == "transport_only"
    }

    assert set(dispositioned) == {"get_docs"}
    for row in dispositioned.values():
        assert "has not entered" not in row["reason"], row


def test_catalog_operations_report_exact_rest_parity() -> None:
    """Every catalog operation's REST binding matches a real main route.

    R1a only adds `operation_route` metadata to existing REST decorators, so
    REST is the one surface where every catalog operation must already show
    exact parity. CLI/MCP naming parity is each domain's R1b migration.
    """
    inventory = build_operation_inventory(app, REPO_ROOT)
    operations = {
        row["operation"]["operation_id"]: row for row in inventory["catalog_operations"]
    }
    for operation_id, row in operations.items():
        assert row["observed"]["rest"]["status"] == "exact_parity", operation_id


def test_generated_operation_files_are_fresh() -> None:
    import importlib.util

    generator_path = API_ROOT / "scripts" / "operation_catalog" / "generate.py"
    spec = importlib.util.spec_from_file_location(
        "operation_catalog_generate", generator_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert (
        module.INVENTORY_PATH.read_text(encoding="utf-8") == module.render_inventory()
    )
    assert (
        module.OPERATIONS_PATH.read_text(encoding="utf-8") == module.render_operations()
    )
