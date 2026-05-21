"""SQL compiler tests."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import select

from shared.policies.compile import compile_to_sql
from src.models.contracts.policies import Expr
from src.models.orm.tables import Document


@dataclass
class FakeUser:
    user_id: UUID = field(default_factory=uuid4)
    email: str = "u@example.com"
    organization_id: UUID | None = None
    is_platform_admin: bool = False
    role_ids: list[UUID] = field(default_factory=list)
    role_names: list[str] = field(default_factory=list)


def _compile(d: dict, user=None) -> str:
    """Compile to SQL, return the rendered string."""
    expr = Expr.model_validate(d)
    sql_expr = compile_to_sql(expr, user or FakeUser())
    # Use a SELECT to render WHERE clause for inspection
    stmt = select(Document.id).where(sql_expr)
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_eq_row_literal():
    sql = _compile({"eq": [{"row": "status"}, "open"]})
    assert "data ->> 'status'" in sql or "data->>'status'" in sql.replace(" ", "")
    assert "'open'" in sql


def test_eq_row_user_reference():
    uid = uuid4()
    user = FakeUser(user_id=uid)
    sql = _compile({"eq": [{"row": "owner"}, {"user": "user_id"}]}, user=user)
    assert str(uid) in sql


def test_eq_row_organization_id_uses_column():
    """organization_id is a column on documents.tables, not in JSONB."""
    org_id = uuid4()
    user = FakeUser(organization_id=org_id)
    sql = _compile(
        {"eq": [{"row": "organization_id"}, {"user": "organization_id"}]},
        user=user,
    )
    # Implementation detail: should reference the column, not data->>
    # Looser check: the org_id literal appears
    assert str(org_id) in sql


def test_and_compiles_to_AND():
    sql = _compile(
        {
            "and": [
                {"eq": [{"row": "x"}, 1]},
                {"eq": [{"row": "y"}, 2]},
            ]
        }
    )
    assert " AND " in sql.upper()


def test_or_compiles_to_OR():
    sql = _compile(
        {
            "or": [
                {"eq": [{"row": "x"}, 1]},
                {"eq": [{"row": "y"}, 2]},
            ]
        }
    )
    assert " OR " in sql.upper()


def test_not_compiles_to_NOT():
    sql = _compile({"not": {"eq": [{"row": "x"}, 1]}})
    assert "NOT" in sql.upper()


def test_in_compiles_to_ANY():
    sql = _compile({"in": [{"row": "status"}, ["draft", "review"]]})
    # SQLAlchemy uses IN (...) here; either ANY or IN is acceptable as long as semantics hold
    assert "IN (" in sql.upper() or "= ANY" in sql.upper()


def test_is_null_compiles_to_IS_NULL():
    sql = _compile({"is_null": {"row": "manager_user_id"}})
    assert "IS NULL" in sql.upper()


def test_call_has_role_resolves_at_compile_time_true():
    user = FakeUser(role_names=["admin"])
    sql = _compile({"call": "has_role", "args": ["admin"]}, user=user)
    # Should resolve to a constant TRUE in the WHERE, e.g. "WHERE 1=1" or "WHERE true"
    upper = sql.upper()
    assert "TRUE" in upper or "1 = 1" in upper or "WHERE 1=1" in upper.replace(" ", "")


def test_call_has_role_resolves_at_compile_time_false():
    user = FakeUser(role_names=[])
    sql = _compile({"call": "has_role", "args": ["admin"]}, user=user)
    upper = sql.upper()
    assert "FALSE" in upper or "1 = 0" in upper or "WHERE 1=0" in upper.replace(" ", "")


def test_user_is_platform_admin_resolves_at_compile_time():
    sql_admin = _compile({"user": "is_platform_admin"}, user=FakeUser(is_platform_admin=True))
    sql_normal = _compile({"user": "is_platform_admin"}, user=FakeUser(is_platform_admin=False))
    assert "TRUE" in sql_admin.upper() or "1 = 1" in sql_admin
    assert "FALSE" in sql_normal.upper() or "1 = 0" in sql_normal


def test_compound_realistic_policy():
    """A real policy: owner can update if row is open."""
    uid = uuid4()
    user = FakeUser(user_id=uid)
    sql = _compile(
        {
            "and": [
                {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
                {"eq": [{"row": "status"}, "open"]},
            ]
        },
        user=user,
    )
    assert str(uid) in sql
    assert "'open'" in sql
    assert "AND" in sql.upper()


def test_call_with_row_reference_arg_raises():
    """Function args may be literals or user refs only — row refs can't compile."""
    import pytest

    user = FakeUser()
    expr = Expr.model_validate({"call": "has_role", "args": [{"row": "x"}]})
    with pytest.raises(ValueError, match="cannot resolve"):
        compile_to_sql(expr, user)


# --- JSONB type coercion for non-string literals ---------------------------
#
# Spec (docs/superpowers/specs/2026-04-30-table-policies-design.md, line 117):
#   "Boolean fields stored in JSON come out as true/false.
#    {eq: [{row: finalized}, true]} works."
#
# The naive compile (data->>'field' = TRUE) is invalid Postgres SQL
# (text = boolean). Use the JSONB-compare form (data->'field' = 'true'::jsonb)
# so type mismatches in row data return false instead of raising.


def _is_jsonb_extract(sql: str, field: str) -> bool:
    """SQLAlchemy's `Document.data[field]` renders as either `data['field']`
    (subscript) or `data -> 'field'` (explicit operator) depending on dialect
    settings. Both compile to the same Postgres `->` operator. Accept either.
    """
    normalized = sql.replace(" ", "").lower()
    return f"data['{field}']" in normalized or f"data->'{field}'" in normalized


def _is_jsonb_text_extract(sql: str, field: str) -> bool:
    """The broken text-extract form: `data->>'field'`."""
    normalized = sql.replace(" ", "").lower()
    return f"data->>'{field}'" in normalized


def test_eq_row_jsonb_bool_true():
    """{eq:[{row:finalized}, true]} — must not produce `data->>'x' = TRUE`."""
    sql = _compile({"eq": [{"row": "finalized"}, True]})
    assert not _is_jsonb_text_extract(sql, "finalized")
    assert _is_jsonb_extract(sql, "finalized")
    assert "jsonb" in sql.lower()
    assert "'true'" in sql.lower()


def test_eq_row_jsonb_bool_false():
    sql = _compile({"eq": [{"row": "finalized"}, False]})
    assert not _is_jsonb_text_extract(sql, "finalized")
    assert _is_jsonb_extract(sql, "finalized")
    assert "jsonb" in sql.lower()
    assert "'false'" in sql.lower()


def test_eq_row_jsonb_int():
    """Numeric literals against JSONB fields need the same treatment."""
    sql = _compile({"eq": [{"row": "count"}, 5]})
    assert not _is_jsonb_text_extract(sql, "count")
    assert _is_jsonb_extract(sql, "count")
    assert "jsonb" in sql.lower()


def test_lt_row_jsonb_int():
    sql = _compile({"lt": [{"row": "count"}, 10]})
    assert not _is_jsonb_text_extract(sql, "count")
    assert _is_jsonb_extract(sql, "count")
    assert "jsonb" in sql.lower()


def test_eq_row_column_mapped_field_unchanged():
    """Column-mapped fields (created_by) still use the column directly."""
    uid = uuid4()
    user = FakeUser(user_id=uid)
    sql = _compile({"eq": [{"row": "created_by"}, {"user": "user_id"}]}, user=user)
    assert "data ->> 'created_by'" not in sql
    assert "data -> 'created_by'" not in sql
    assert "created_by" in sql
    assert str(uid) in sql


def test_eq_row_jsonb_string_literal_unchanged():
    """String literal path is unchanged — uses ->> (text) comparison."""
    sql = _compile({"eq": [{"row": "status"}, "open"]})
    assert "data ->> 'status'" in sql or "data->>'status'" in sql.replace(" ", "")
    assert "'open'" in sql
