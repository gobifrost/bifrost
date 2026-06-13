"""Ref rewriting for the v1→v2 migration rename step.

The dependency walker FINDS references; the rewriter REWRITES them when a
workflow is renamed to the org convention ({domain}_{verb}_{noun}). A rename
changes a workflow's ``path::function_name`` (file moved to flat ``workflows/``,
function renamed) and optionally its bare ``name`` — so every string ref keyed
on those must be rewritten atomically or the migrated app/forms break.

These cover the pure string core (source rewriting + ref-string remap). The
DB-applying half (``apply_workflow_renames``) is covered by an e2e test.
"""
from __future__ import annotations

from src.services.solutions.ref_rewriter import (
    WorkflowRename,
    remap_ref_string,
    rewrite_source_refs,
)

# A rename: old file/function → new flat path + convention name.
RENAME = WorkflowRename(
    old_path="legacy/orders/sync.py",
    old_function_name="run",
    old_name="sync_orders",
    new_path="workflows/orders_sync_records.py",
    new_function_name="orders_sync_records",
    new_name="orders_sync_records",
)


def test_rewrite_pathfn_ref_in_tsx() -> None:
    src = 'const q = useWorkflow("legacy/orders/sync.py::run");'
    out = rewrite_source_refs(src, [RENAME])
    assert out == 'const q = useWorkflow("workflows/orders_sync_records.py::orders_sync_records");'


def test_rewrite_bare_name_ref_in_tsx() -> None:
    src = "useWorkflowQuery('sync_orders')"
    out = rewrite_source_refs(src, [RENAME])
    assert out == "useWorkflowQuery('orders_sync_records')"


def test_rewrite_leaves_unrelated_refs_untouched() -> None:
    src = 'useWorkflow("other/thing.py::go"); useWorkflow("sync_orders_extra");'
    out = rewrite_source_refs(src, [RENAME])
    # "sync_orders_extra" must NOT be rewritten by the "sync_orders" rename —
    # whole-token match only, no substring bleed.
    assert out == src


def test_rewrite_handles_both_quote_styles_and_hooks() -> None:
    src = (
        'useWorkflow("legacy/orders/sync.py::run")\n'
        "useWorkflowMutation('sync_orders')\n"
    )
    out = rewrite_source_refs(src, [RENAME])
    assert "workflows/orders_sync_records.py::orders_sync_records" in out
    assert "useWorkflowMutation('orders_sync_records')" in out
    assert "sync_orders" not in out


def test_remap_ref_string_pathfn() -> None:
    assert remap_ref_string("legacy/orders/sync.py::run", [RENAME]) == (
        "workflows/orders_sync_records.py::orders_sync_records"
    )


def test_remap_ref_string_bare_name() -> None:
    assert remap_ref_string("sync_orders", [RENAME]) == "orders_sync_records"


def test_remap_ref_string_passthrough_for_unknown() -> None:
    # A UUID or unrelated ref passes straight through (FK refs survive renames).
    uid = "550e8400-e29b-41d4-a716-446655440000"
    assert remap_ref_string(uid, [RENAME]) == uid
    assert remap_ref_string("unrelated_name", [RENAME]) == "unrelated_name"


def test_multiple_renames_applied() -> None:
    r2 = WorkflowRename(
        old_path="legacy/x.py", old_function_name="f", old_name="old_two",
        new_path="workflows/domain_do_thing.py", new_function_name="domain_do_thing",
        new_name="domain_do_thing",
    )
    src = 'useWorkflow("sync_orders"); useWorkflow("legacy/x.py::f");'
    out = rewrite_source_refs(src, [RENAME, r2])
    assert "orders_sync_records" in out
    assert "workflows/domain_do_thing.py::domain_do_thing" in out
