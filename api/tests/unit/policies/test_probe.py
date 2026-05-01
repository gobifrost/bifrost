"""Probe helper tests."""

from uuid import uuid4

from shared.policies.probe import (
    compile_read_filter,
    evaluate_action,
    is_subscribe_authorized,
    make_seed_admin_bypass,
)
from src.models.contracts.policies import Policy, TablePolicies
from tests.unit.policies.test_evaluate import FakeUser


def _admin_bypass_policy() -> Policy:
    return Policy.model_validate({
        "name": "admin_bypass",
        "actions": ["read", "create", "update", "delete"],
        "when": {"user": "is_platform_admin"},
    })


def _own_row_policy() -> Policy:
    return Policy.model_validate({
        "name": "own_row",
        "actions": ["read", "update", "delete"],
        "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
    })


# --- evaluate_action ---


def test_evaluate_action_default_deny():
    """Empty policies → deny."""
    tp = TablePolicies(policies=[])
    assert evaluate_action("read", tp, row={}, user=FakeUser()) is False


def test_evaluate_action_admin_bypass():
    tp = TablePolicies(policies=[_admin_bypass_policy()])
    admin = FakeUser(is_platform_admin=True)
    other = FakeUser(is_platform_admin=False)
    assert evaluate_action("read", tp, row={}, user=admin) is True
    assert evaluate_action("update", tp, row={}, user=admin) is True
    assert evaluate_action("read", tp, row={}, user=other) is False


def test_evaluate_action_OR_across_rules():
    """Either rule allowing → allowed."""
    tp = TablePolicies(policies=[_admin_bypass_policy(), _own_row_policy()])
    user = FakeUser(user_id=uuid4(), is_platform_admin=False)
    # Not admin, not the creator → deny
    assert evaluate_action(
        "read", tp, row={"created_by": str(uuid4())}, user=user
    ) is False
    # Not admin, is creator → allow via own_row
    assert evaluate_action(
        "read", tp, row={"created_by": str(user.user_id)}, user=user
    ) is True


def test_evaluate_action_skips_rules_for_other_actions():
    """A rule for [update] doesn't grant read."""
    tp = TablePolicies(policies=[
        Policy.model_validate({
            "name": "update_only",
            "actions": ["update"],
            "when": None,
        })
    ])
    assert evaluate_action("read", tp, row={}, user=FakeUser()) is False
    assert evaluate_action("update", tp, row={}, user=FakeUser()) is True


def test_evaluate_action_when_none_means_always():
    """A rule with `when: null` allows for the listed actions unconditionally."""
    tp = TablePolicies(policies=[
        Policy.model_validate({"name": "open_read", "actions": ["read"], "when": None})
    ])
    assert evaluate_action("read", tp, row={}, user=FakeUser()) is True


# --- compile_read_filter ---


def test_compile_read_filter_no_read_rules_returns_none():
    """No rules grant read → return None (handler must deny)."""
    tp = TablePolicies(policies=[
        Policy.model_validate({"name": "create_only", "actions": ["create"], "when": None})
    ])
    assert compile_read_filter(tp, user=FakeUser()) is None


def test_compile_read_filter_combines_with_or():
    """Two rules grant read → returned filter is their OR."""
    tp = TablePolicies(policies=[
        _admin_bypass_policy(),
        _own_row_policy(),
    ])
    f = compile_read_filter(tp, user=FakeUser())
    # Compile to SQL string for inspection
    from sqlalchemy import select
    from src.models.orm.tables import Document

    sql = str(select(Document.id).where(f).compile(compile_kwargs={"literal_binds": True}))
    assert " OR " in sql.upper()


def test_compile_read_filter_when_none_compiles_to_true():
    tp = TablePolicies(policies=[
        Policy.model_validate({"name": "open_read", "actions": ["read"], "when": None})
    ])
    f = compile_read_filter(tp, user=FakeUser())
    from sqlalchemy import select
    from src.models.orm.tables import Document

    sql = str(select(Document.id).where(f).compile(compile_kwargs={"literal_binds": True}))
    upper = sql.upper()
    assert "TRUE" in upper or "1 = 1" in upper.replace(" ", "")


# --- is_subscribe_authorized ---


def test_subscribe_authorized_when_at_least_one_read_rule_could_match():
    """If any read rule could ever match this user, allow subscribe."""
    tp = TablePolicies(policies=[_own_row_policy()])
    # Even with empty row, the rule is row-data-dependent; subscribe stays open
    # and the per-message filter gates individual messages.
    assert is_subscribe_authorized(tp, user=FakeUser()) is True


def test_subscribe_unauthorized_when_no_read_rules():
    tp = TablePolicies(policies=[
        Policy.model_validate({"name": "create_only", "actions": ["create"], "when": None})
    ])
    assert is_subscribe_authorized(tp, user=FakeUser()) is False


def test_subscribe_authorized_for_admin_bypass():
    tp = TablePolicies(policies=[_admin_bypass_policy()])
    assert is_subscribe_authorized(tp, user=FakeUser(is_platform_admin=True)) is True
    # Non-admin, no other read rule → user-level fact resolves to False at probe time
    assert is_subscribe_authorized(tp, user=FakeUser(is_platform_admin=False)) is False


# --- seed ---


def test_make_seed_admin_bypass_shape():
    seed = make_seed_admin_bypass()
    assert seed["policies"][0]["name"] == "admin_bypass"
    assert set(seed["policies"][0]["actions"]) == {"read", "create", "update", "delete"}
    assert seed["policies"][0]["when"] == {"user": "is_platform_admin"}


def test_subscribe_user_dep_false_followed_by_row_dep_allows():
    """User-only rule that resolves False does not block a later row-dep rule."""
    tp = TablePolicies(policies=[_admin_bypass_policy(), _own_row_policy()])
    # Non-admin → admin_bypass resolves False at probe time
    # own_row is row-dep → conservatively allow
    assert is_subscribe_authorized(tp, user=FakeUser(is_platform_admin=False)) is True


def test_subscribe_all_user_dep_resolving_false_denies():
    """Multiple user-only rules all resolving False → deny."""
    tp = TablePolicies(policies=[
        _admin_bypass_policy(),
        Policy.model_validate({
            "name": "support_only",
            "actions": ["read"],
            "when": {"call": "has_role", "args": ["support"]},
        }),
    ])
    assert is_subscribe_authorized(
        tp, user=FakeUser(is_platform_admin=False, role_names=["customer"])
    ) is False


def test_make_seed_admin_bypass_validates():
    """Seed must round-trip through TablePolicies validation.

    Catches schema drift at module-test time rather than at first table-create.
    """
    TablePolicies.model_validate(make_seed_admin_bypass())  # must not raise


def test_compile_read_filter_single_rule_no_or_wrap():
    """Single read rule returns the fragment as-is, not wrapped in sa_or."""
    from sqlalchemy import select
    from src.models.orm.tables import Document

    tp = TablePolicies(policies=[_admin_bypass_policy()])
    f = compile_read_filter(tp, user=FakeUser(is_platform_admin=True))
    assert f is not None
    sql = str(select(Document.id).where(f).compile(compile_kwargs={"literal_binds": True}))
    # Single fragment → no OR wrapping
    assert " OR " not in sql.upper()
