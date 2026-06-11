"""Unit tests for the shared MCP org-cascade helper.

The cascade is pure org→global for every principal — ``is_external`` plays
no part here (external access is governed by access_level, not scope).
"""

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from src.models import Application
from src.services.mcp_server.tools._org_scope import apply_mcp_org_scope


def _sql(query) -> str:
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    _, _, where = sql.partition("WHERE")
    return where


def _ctx(*, is_platform_admin=False, org_id=...):
    return SimpleNamespace(
        is_platform_admin=is_platform_admin,
        org_id=uuid4() if org_id is ... else org_id,
    )


def test_platform_admin_no_filter():
    # No WHERE clause at all -> no org filter applied.
    sql = _sql(apply_mcp_org_scope(select(Application), Application, _ctx(is_platform_admin=True)))
    assert sql.strip() == ""


def test_regular_user_org_plus_global():
    sql = _sql(apply_mcp_org_scope(select(Application), Application, _ctx()))
    assert "organization_id IS NULL" in sql
    assert "organization_id =" in sql


def test_regular_user_no_org_global_only():
    sql = _sql(apply_mcp_org_scope(select(Application), Application, _ctx(org_id=None)))
    assert "organization_id IS NULL" in sql


def test_string_org_id_coerced():
    org = uuid4()
    ctx = _ctx(org_id=str(org))
    sql = _sql(apply_mcp_org_scope(select(Application), Application, ctx))
    # rendered UUID literal has dashes stripped by the PG UUID type.
    assert org.hex in sql
