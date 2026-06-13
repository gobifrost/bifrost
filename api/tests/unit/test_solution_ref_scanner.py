from __future__ import annotations

from src.services.solutions.ref_scanner import (
    scan_config_refs,
    scan_table_refs,
    scan_workflow_refs,
)


def test_scan_table_refs_python_and_tsx() -> None:
    py = 'rows = await sdk.tables.get("orders")\nx = await tables.get("customers", "id-1")'
    tsx = 'const t = useTable("inventory");'
    assert scan_table_refs(py) == {"orders", "customers"}
    assert scan_table_refs(tsx) == {"inventory"}


def test_scan_config_refs() -> None:
    src = 'k = await config.get("API_KEY")\nz = await sdk.config.get("TIMEOUT", default=5)'
    assert scan_config_refs(src) == {"API_KEY", "TIMEOUT"}


def test_scan_workflow_refs_tsx() -> None:
    tsx = 'const { run } = useWorkflow("workflows/main.py::handler");'
    assert scan_workflow_refs(tsx) == {"workflows/main.py::handler"}


def test_scan_workflow_refs_query_and_mutation_hooks() -> None:
    # useWorkflowQuery / useWorkflowMutation take a bare name OR a path::fn ref.
    src = (
        'const a = useWorkflowQuery("get_clients");\n'
        'const b = useWorkflowMutation("workflows/m.py::save");'
    )
    assert scan_workflow_refs(src) == {"get_clients", "workflows/m.py::save"}


def test_scanners_ignore_unrelated_strings() -> None:
    # A bare string or a similarly-named symbol must NOT be picked up.
    src = 'name = "orders"\nimport environment\nconfigure("x")'
    assert scan_table_refs(src) == set()
    assert scan_config_refs(src) == set()
    assert scan_workflow_refs(src) == set()


def test_scan_handles_single_and_double_quotes() -> None:
    assert scan_table_refs("tables.get('q')") == {"q"}
    assert scan_config_refs('config.get("q")') == {"q"}
