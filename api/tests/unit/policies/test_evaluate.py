"""Pure-function evaluator tests."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from shared.policies.evaluate import evaluate
from shared.table_policies import RowResolver
from src.models.contracts.policies import Expr


@dataclass
class FakeUser:
    user_id: UUID = field(default_factory=uuid4)
    email: str = "u@example.com"
    organization_id: UUID | None = None
    is_platform_admin: bool = False
    role_ids: list[UUID] = field(default_factory=list)
    role_names: list[str] = field(default_factory=list)


def _e(d: dict) -> Expr:
    return Expr.model_validate(d)


# --- Literals and references ---


def test_eq_literal_literal():
    assert evaluate(_e({"eq": [1, 1]}), ctx={}, user=FakeUser(), resolver=RowResolver()) is True
    assert evaluate(_e({"eq": [1, 2]}), ctx={}, user=FakeUser(), resolver=RowResolver()) is False


def test_eq_row_reference():
    expr = _e({"eq": [{"row": "x"}, 5]})
    assert evaluate(expr, ctx={"x": 5}, user=FakeUser(), resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"x": 6}, user=FakeUser(), resolver=RowResolver()) is False
    assert evaluate(expr, ctx={}, user=FakeUser(), resolver=RowResolver()) is False  # missing → null → ne


def test_eq_user_reference():
    uid = uuid4()
    user = FakeUser(user_id=uid)
    expr = _e({"eq": [{"row": "owner"}, {"user": "user_id"}]})
    assert evaluate(expr, ctx={"owner": str(uid)}, user=user, resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"owner": str(uuid4())}, user=user, resolver=RowResolver()) is False


def test_user_is_platform_admin():
    expr = _e({"user": "is_platform_admin"})
    assert evaluate(expr, ctx={}, user=FakeUser(is_platform_admin=True), resolver=RowResolver()) is True
    assert evaluate(expr, ctx={}, user=FakeUser(is_platform_admin=False), resolver=RowResolver()) is False


# --- Logic ---


def test_and_short_circuits_on_false():
    expr = _e({
        "and": [
            {"eq": [1, 2]},  # false
            {"eq": [{"row": "missing"}, 1]},  # would be false too
        ]
    })
    assert evaluate(expr, ctx={}, user=FakeUser(), resolver=RowResolver()) is False


def test_and_all_true():
    expr = _e({"and": [{"eq": [1, 1]}, {"eq": [2, 2]}]})
    assert evaluate(expr, ctx={}, user=FakeUser(), resolver=RowResolver()) is True


def test_or_short_circuits_on_true():
    expr = _e({"or": [{"eq": [1, 1]}, {"eq": [{"row": "x"}, 99]}]})
    assert evaluate(expr, ctx={}, user=FakeUser(), resolver=RowResolver()) is True


def test_not():
    assert evaluate(_e({"not": {"eq": [1, 1]}}), ctx={}, user=FakeUser(), resolver=RowResolver()) is False
    assert evaluate(_e({"not": {"eq": [1, 2]}}), ctx={}, user=FakeUser(), resolver=RowResolver()) is True


# --- Comparisons ---


def test_lt_lte_gt_gte_numbers():
    user = FakeUser()
    assert evaluate(_e({"lt": [1, 2]}), ctx={}, user=user, resolver=RowResolver()) is True
    assert evaluate(_e({"lt": [2, 2]}), ctx={}, user=user, resolver=RowResolver()) is False
    assert evaluate(_e({"lte": [2, 2]}), ctx={}, user=user, resolver=RowResolver()) is True
    assert evaluate(_e({"gt": [3, 2]}), ctx={}, user=user, resolver=RowResolver()) is True
    assert evaluate(_e({"gte": [2, 2]}), ctx={}, user=user, resolver=RowResolver()) is True


def test_neq():
    assert evaluate(_e({"neq": [1, 2]}), ctx={}, user=FakeUser(), resolver=RowResolver()) is True
    assert evaluate(_e({"neq": [1, 1]}), ctx={}, user=FakeUser(), resolver=RowResolver()) is False


# --- Membership ---


def test_in_membership():
    user = FakeUser()
    expr = _e({"in": [{"row": "status"}, ["draft", "review"]]})
    assert evaluate(expr, ctx={"status": "draft"}, user=user, resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"status": "done"}, user=user, resolver=RowResolver()) is False
    assert evaluate(expr, ctx={}, user=user, resolver=RowResolver()) is False  # missing


# --- is_null ---


def test_is_null_missing_field():
    expr = _e({"is_null": {"row": "absent"}})
    assert evaluate(expr, ctx={}, user=FakeUser(), resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"absent": "x"}, user=FakeUser(), resolver=RowResolver()) is False


def test_is_null_explicit_null():
    expr = _e({"is_null": {"row": "x"}})
    assert evaluate(expr, ctx={"x": None}, user=FakeUser(), resolver=RowResolver()) is True


def test_not_is_null_pattern():
    """Common idiom: 'is set' check."""
    expr = _e({"not": {"is_null": {"row": "manager_user_id"}}})
    user = FakeUser()
    assert evaluate(expr, ctx={"manager_user_id": "abc"}, user=user, resolver=RowResolver()) is True
    assert evaluate(expr, ctx={}, user=user, resolver=RowResolver()) is False


# --- Calls ---


def test_has_role_match_by_name():
    expr = _e({"call": "has_role", "args": ["admin"]})
    assert evaluate(expr, ctx={}, user=FakeUser(role_names=["admin"]), resolver=RowResolver()) is True
    assert evaluate(expr, ctx={}, user=FakeUser(role_names=["viewer"]), resolver=RowResolver()) is False


def test_has_role_match_by_uuid_string():
    role_id = uuid4()
    expr = _e({"call": "has_role", "args": [str(role_id)]})
    assert evaluate(expr, ctx={}, user=FakeUser(role_ids=[role_id]), resolver=RowResolver()) is True


# --- Realistic policy scenarios ---


def test_owner_can_edit_open_policy():
    """User owns row AND status is open."""
    uid = uuid4()
    user = FakeUser(user_id=uid)
    policy = _e({
        "and": [
            {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            {"eq": [{"row": "status"}, "open"]},
        ]
    })
    # Owner, open: allow
    assert evaluate(policy, ctx={"created_by": str(uid), "status": "open"}, user=user, resolver=RowResolver()) is True
    # Owner, done: deny
    assert evaluate(policy, ctx={"created_by": str(uid), "status": "done"}, user=user, resolver=RowResolver()) is False
    # Other, open: deny
    assert evaluate(policy, ctx={"created_by": str(uuid4()), "status": "open"}, user=user, resolver=RowResolver()) is False


def test_manager_reads_reports_policy():
    """Manager can read rows where ROW.manager_user_id == USER.user_id."""
    mgr_id = uuid4()
    mgr = FakeUser(user_id=mgr_id)
    policy = _e({"eq": [{"row": "manager_user_id"}, {"user": "user_id"}]})
    assert evaluate(policy, ctx={"manager_user_id": str(mgr_id)}, user=mgr, resolver=RowResolver()) is True
    assert evaluate(policy, ctx={"manager_user_id": str(uuid4())}, user=mgr, resolver=RowResolver()) is False


def test_admin_bypass_policy():
    """Platform admin shortcut."""
    policy = _e({"user": "is_platform_admin"})
    assert evaluate(policy, ctx={"any": "row"}, user=FakeUser(is_platform_admin=True), resolver=RowResolver()) is True
    assert evaluate(policy, ctx={"any": "row"}, user=FakeUser(is_platform_admin=False), resolver=RowResolver()) is False


def test_own_org_policy():
    """User can see rows in their own org."""
    org_id = uuid4()
    user = FakeUser(organization_id=org_id)
    policy = _e({"eq": [{"row": "organization_id"}, {"user": "organization_id"}]})
    assert evaluate(policy, ctx={"organization_id": str(org_id)}, user=user, resolver=RowResolver()) is True
    assert evaluate(policy, ctx={"organization_id": str(uuid4())}, user=user, resolver=RowResolver()) is False


# --- Type semantics ---


def test_string_eq_with_uuid_value_from_user():
    """UUID values from user namespace stringify before compare."""
    uid = uuid4()
    user = FakeUser(user_id=uid)
    expr = _e({"eq": [{"row": "user_id"}, {"user": "user_id"}]})
    # Row has the UUID as a string (JSONB extraction yields string)
    assert evaluate(expr, ctx={"user_id": str(uid)}, user=user, resolver=RowResolver()) is True


def test_null_propagates_in_eq():
    """eq([missing, anything]) is false (does not error)."""
    expr = _e({"eq": [{"row": "x"}, "value"]})
    assert evaluate(expr, ctx={}, user=FakeUser(), resolver=RowResolver()) is False


def test_boolean_field_eq():
    """Boolean fields compare correctly."""
    expr = _e({"eq": [{"row": "enabled"}, True]})
    user = FakeUser()
    assert evaluate(expr, ctx={"enabled": True}, user=user, resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"enabled": False}, user=user, resolver=RowResolver()) is False


# --- Top-level boolean coercion ---


def test_bare_literal_top_level_coerces_to_bool():
    """A top-level bare literal coerces to bool (truthy → True)."""
    user = FakeUser()
    assert evaluate(_e({"eq": ["yes", "yes"]}), ctx={}, user=user, resolver=RowResolver()) is True
    # A reference resolving to a non-empty string is truthy
    expr = _e({"eq": [{"row": "x"}, "v"]})
    assert evaluate(expr, ctx={"x": "v"}, user=user, resolver=RowResolver()) is True


# --- row=None contract (DELETE events) ---


def test_row_none_resolves_refs_to_none():
    """ctx=None makes every {"row": ...} reference resolve to None."""
    user = FakeUser()
    expr = _e({"eq": [{"row": "x"}, "v"]})
    # Null left side -> NULL-as-false
    assert evaluate(expr, ctx=None, user=user, resolver=RowResolver()) is False
    # User-only refs still work
    expr_user = _e({"user": "is_platform_admin"})
    assert evaluate(expr_user, ctx=None, user=FakeUser(is_platform_admin=True), resolver=RowResolver()) is True


# --- is_null on nested paths ---


def test_is_null_nested_missing_path():
    """is_null on a nested path where an intermediate is missing."""
    expr = _e({"is_null": {"row": "a.b.c"}})
    assert evaluate(expr, ctx={}, user=FakeUser(), resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"a": None}, user=FakeUser(), resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"a": "scalar"}, user=FakeUser(), resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"a": {"b": {"c": "v"}}}, user=FakeUser(), resolver=RowResolver()) is False


# --- in-membership with mixed types ---


def test_in_membership_mixed_types():
    """`in` with a list containing both an int and a string matches by Python equality."""
    expr = _e({"in": [{"row": "x"}, [1, "1"]]})
    user = FakeUser()
    # String "1" matches the literal "1" in the list
    assert evaluate(expr, ctx={"x": "1"}, user=user, resolver=RowResolver()) is True
    # Int 1 matches the literal 1 in the list
    assert evaluate(expr, ctx={"x": 1}, user=user, resolver=RowResolver()) is True
    assert evaluate(expr, ctx={"x": 2}, user=user, resolver=RowResolver()) is False


# --- eq against literal None is rejected at validate time ---


def test_eq_against_literal_none_rejected():
    """eq/neq with a literal None is rejected at construction. Evaluator and SQL
    pushdown disagree on its meaning, so the only safe shape is `is_null`."""
    user = FakeUser()
    with pytest.raises(ValidationError, match="use is_null"):
        _e({"eq": [{"row": "x"}, None]})
    # The correct shape:
    assert evaluate(_e({"is_null": {"row": "x"}}), ctx={"x": None}, user=user, resolver=RowResolver()) is True
