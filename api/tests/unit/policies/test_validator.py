"""Validator tests for the policy AST."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.policies import Expr, Policy, TablePolicies


def _expr(d: dict) -> Expr:
    return Expr.model_validate(d)


# --- Operator shape ---


def test_eq_requires_two_operands():
    _expr({"eq": [{"row": "x"}, "v"]})  # OK
    with pytest.raises(ValidationError):
        _expr({"eq": [{"row": "x"}]})


def test_eq_rejects_none_literal():
    """eq/neq with a None literal is ambiguous between IS NULL and NULL-as-false.
    Use is_null instead."""
    with pytest.raises(ValidationError, match="use is_null"):
        _expr({"eq": [{"row": "x"}, None]})
    with pytest.raises(ValidationError, match="use is_null"):
        _expr({"neq": [{"row": "x"}, None]})
    # eq with None on left side is also rejected (symmetric)
    with pytest.raises(ValidationError, match="use is_null"):
        _expr({"eq": [None, {"row": "x"}]})


def test_and_requires_at_least_two_operands():
    _expr({"and": [{"eq": [{"row": "x"}, 1]}, {"eq": [{"row": "y"}, 2]}]})
    with pytest.raises(ValidationError):
        _expr({"and": []})
    with pytest.raises(ValidationError):
        _expr({"and": [{"eq": [{"row": "x"}, 1]}]})


def test_not_requires_one_operand():
    _expr({"not": {"eq": [{"row": "x"}, 1]}})
    with pytest.raises(ValidationError):
        _expr({"not": []})


def test_in_requires_non_empty_literal_list():
    _expr({"in": [{"row": "status"}, ["a", "b"]]})
    with pytest.raises(ValidationError):
        _expr({"in": [{"row": "status"}, []]})
    # Right side must be a literal list, not a reference
    with pytest.raises(ValidationError):
        _expr({"in": [{"row": "status"}, {"row": "values"}]})


def test_is_null_requires_one_operand():
    _expr({"is_null": {"row": "manager_user_id"}})
    with pytest.raises(ValidationError):
        _expr({"is_null": [{"row": "x"}, {"row": "y"}]})


# --- References ---


def test_user_reference_must_be_known_field():
    _expr({"eq": [{"user": "user_id"}, "x"]})
    _expr({"eq": [{"user": "is_platform_admin"}, True]})
    with pytest.raises(ValidationError):
        _expr({"eq": [{"user": "social_security_number"}, "x"]})


def test_row_reference_can_be_arbitrary_field_name():
    _expr({"eq": [{"row": "user_id"}, "x"]})
    _expr({"eq": [{"row": "manager_user_id"}, "x"]})
    _expr({"eq": [{"row": "metadata.priority"}, "x"]})
    # Empty reference is invalid
    with pytest.raises(ValidationError):
        _expr({"eq": [{"row": ""}, "x"]})


# --- Functions ---


def test_call_must_target_registered_function():
    _expr({"call": "has_role", "args": ["admin"]})
    with pytest.raises(ValidationError):
        _expr({"call": "manages", "args": ["x"]})
    with pytest.raises(ValidationError):
        _expr({"call": "lookup_in_db", "args": ["x"]})


def test_call_validates_arg_arity_and_types():
    _expr({"call": "has_role", "args": ["admin"]})  # OK
    with pytest.raises(ValidationError):
        _expr({"call": "has_role", "args": []})  # too few
    with pytest.raises(ValidationError):
        _expr({"call": "has_role", "args": ["admin", "viewer"]})  # too many


# --- Top-level Policy ---


def test_policy_requires_at_least_one_action():
    Policy(name="x", actions=["read"])
    with pytest.raises(ValidationError):
        Policy(name="x", actions=[])


def test_policy_actions_limited_to_known_set():
    with pytest.raises(ValidationError):
        Policy(name="x", actions=["query"])  # not a real action


def test_policy_when_can_be_none():
    p = Policy(name="x", actions=["read"], when=None)
    assert p.when is None


def test_policy_when_validates_nested_expression():
    Policy(
        name="x",
        actions=["read"],
        when=Expr.model_validate({"eq": [{"row": "y"}, 1]}),
    )
    with pytest.raises(ValidationError):
        Policy(
            name="x",
            actions=["read"],
            when=Expr.model_validate({"call": "manages", "args": ["x"]}),
        )


def test_table_policies_default_empty():
    tp = TablePolicies()
    assert tp.policies == []


def test_validator_rejects_deeply_nested_expression():
    """Recursion is bounded at 64 levels; deeper raises a clear error."""
    expr_dict = {"not": True}
    for _ in range(70):
        expr_dict = {"not": expr_dict}
    with pytest.raises(ValidationError, match="nested too deeply"):
        Expr.model_validate(expr_dict)


def test_validator_error_includes_path():
    """Error message includes a JSON path to the bad node."""
    bad = {
        "and": [
            {"eq": [{"row": "x"}, 1]},
            {"eq": [{"row": "y"}]},  # missing operand at and[1]
        ]
    }
    with pytest.raises(ValidationError, match=r"\$\.and\[1\]\.eq"):
        Expr.model_validate(bad)


def test_policy_round_trips():
    """JSON serialization round-trips through model_dump/validate."""
    role_id = str(uuid4())
    raw = {
        "policies": [
            {
                "name": "admin_bypass",
                "description": "Platform admins can do anything",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "owner_can_edit_open",
                "actions": ["update"],
                "when": {
                    "and": [
                        {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
                        {"eq": [{"row": "status"}, "open"]},
                    ]
                },
            },
            {
                "name": "role_gated",
                "actions": ["update"],
                "when": {"call": "has_role", "args": [role_id]},
            },
        ]
    }
    tp = TablePolicies.model_validate(raw)
    rt = tp.model_dump(mode="json")
    assert rt["policies"][0]["actions"] == ["read", "create", "update", "delete"]
    assert rt["policies"][1]["when"]["and"][0]["eq"][0] == {"row": "created_by"}
    assert rt["policies"][2]["when"]["args"] == [role_id]
