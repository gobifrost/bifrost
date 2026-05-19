"""Tests for the table-domain bindings to the shared engine."""
from __future__ import annotations

from sqlalchemy.sql import ColumnElement

from shared.policies.ast import PolicyDocument
from shared.table_policies import (
    RowResolver,
    TableBinding,
    TablePolicies,
    compile_read_filter,
    make_seed_admin_bypass,
)
from src.models.orm.tables import Document


class _User:
    user_id = "u-1"
    email = "u@x"
    organization_id = None
    is_platform_admin = True
    role_ids: list = []
    role_names: list = []


def test_row_resolver_namespace():
    assert RowResolver().namespace == "row"


def test_row_resolver_dot_path():
    r = RowResolver()
    assert r.resolve("a", {"a": 1}) == 1
    assert r.resolve("a.b", {"a": {"b": 2}}) == 2
    assert r.resolve("missing", {}) is None
    assert r.resolve("a.b", None) is None


def test_table_binding_column_mapped():
    b = TableBinding()
    col = b.resolve_reference("created_by")
    assert col is Document.created_by


def test_table_binding_jsonb_fallback():
    b = TableBinding()
    col = b.resolve_reference("some_data_field")
    s = str(col)
    assert "data" in s.lower()


def test_make_seed_admin_bypass_uses_table_actions():
    seed = make_seed_admin_bypass()
    assert seed["policies"][0]["actions"] == ["read", "create", "update", "delete"]


def test_compile_read_filter_returns_none_for_empty():
    doc = PolicyDocument()
    assert compile_read_filter(doc, user=_User()) is None


def test_compile_read_filter_or_across_rules():
    doc = PolicyDocument.model_validate({
        "policies": [
            {"name": "a", "actions": ["read"], "when": {"user": "is_platform_admin"}},
            {"name": "b", "actions": ["read"], "when": {"eq": [{"row": "owner_id"}, {"user": "user_id"}]}},
        ],
    })
    sql = compile_read_filter(doc, user=_User())
    assert sql is not None
    assert isinstance(sql, ColumnElement)


def test_table_policies_re_exports_policy_document():
    """TablePolicies is an alias of PolicyDocument."""
    assert TablePolicies is PolicyDocument
