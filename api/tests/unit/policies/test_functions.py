"""Tests for the policy function registry."""

from uuid import uuid4

from shared.policies.functions import FUNCTIONS


class _FakeUser:
    """Minimal Principal stand-in for tests."""

    def __init__(self, role_names=None, role_ids=None):
        self.role_names = role_names or []
        self.role_ids = role_ids or []


def test_has_role_evaluate_matches_by_name():
    fn = FUNCTIONS["has_role"]
    user = _FakeUser(role_names=["admin", "viewer"])
    assert fn.evaluate(["admin"], user, row={}) is True
    assert fn.evaluate(["editor"], user, row={}) is False


def test_has_role_evaluate_matches_by_uuid():
    fn = FUNCTIONS["has_role"]
    role_uuid = uuid4()
    user = _FakeUser(role_ids=[role_uuid])
    assert fn.evaluate([str(role_uuid)], user, row={}) is True
    assert fn.evaluate([str(uuid4())], user, row={}) is False


def test_has_role_compile_resolves_at_compile_time():
    fn = FUNCTIONS["has_role"]
    user = _FakeUser(role_names=["admin"])
    # Compile must resolve the call to a literal True/False, not defer.
    result = fn.compile(["admin"], user)
    assert result is True
    result = fn.compile(["other"], user)
    assert result is False


def test_function_def_arg_types_documented():
    fn = FUNCTIONS["has_role"]
    assert fn.arg_types == [str]


def test_unknown_function_not_in_registry():
    assert "manages" not in FUNCTIONS
    assert "lookup_in_db" not in FUNCTIONS
