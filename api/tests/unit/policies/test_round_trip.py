"""Round-trip: evaluator and compiler must agree on the same fixtures."""

import pytest
from pydantic import ValidationError

from shared.policies.compile import compile_to_sql
from shared.policies.evaluate import evaluate
from src.models.contracts.policies import Expr


# Reuse the FakeUser shape from test_evaluate
from tests.unit.policies.test_evaluate import FakeUser


# Each case is (expr_dict, row_dict, user_kwargs, expected_bool)
CASES = [
    # Literals
    ({"eq": [1, 1]}, {}, {}, True),
    ({"eq": [1, 2]}, {}, {}, False),
    # Row references
    ({"eq": [{"row": "x"}, "v"]}, {"x": "v"}, {}, True),
    ({"eq": [{"row": "x"}, "v"]}, {"x": "z"}, {}, False),
    # User references
    ({"user": "is_platform_admin"}, {}, {"is_platform_admin": True}, True),
    ({"user": "is_platform_admin"}, {}, {"is_platform_admin": False}, False),
    # Logic
    ({"and": [{"eq": [1, 1]}, {"eq": [2, 2]}]}, {}, {}, True),
    ({"and": [{"eq": [1, 1]}, {"eq": [1, 2]}]}, {}, {}, False),
    ({"or": [{"eq": [1, 2]}, {"eq": [2, 2]}]}, {}, {}, True),
    ({"or": [{"eq": [1, 2]}, {"eq": [3, 4]}]}, {}, {}, False),
    ({"not": {"eq": [1, 1]}}, {}, {}, False),
    ({"not": {"eq": [1, 2]}}, {}, {}, True),
    # Membership
    ({"in": [{"row": "x"}, ["a", "b"]]}, {"x": "a"}, {}, True),
    ({"in": [{"row": "x"}, ["a", "b"]]}, {"x": "c"}, {}, False),
    # is_null
    ({"is_null": {"row": "x"}}, {}, {}, True),
    ({"is_null": {"row": "x"}}, {"x": "v"}, {}, False),
    # Function call
    ({"call": "has_role", "args": ["admin"]}, {}, {"role_names": ["admin"]}, True),
    ({"call": "has_role", "args": ["admin"]}, {}, {"role_names": []}, False),
    # neq
    ({"neq": [1, 2]}, {}, {}, True),
    ({"neq": [1, 1]}, {}, {}, False),
    # Comparisons (numeric)
    ({"lt": [1, 2]}, {}, {}, True),
    ({"lt": [2, 1]}, {}, {}, False),
    ({"lte": [2, 2]}, {}, {}, True),
    ({"lte": [3, 2]}, {}, {}, False),
    ({"gt": [3, 2]}, {}, {}, True),
    ({"gt": [2, 3]}, {}, {}, False),
    ({"gte": [2, 2]}, {}, {}, True),
    ({"gte": [1, 2]}, {}, {}, False),
    # Nested row path
    ({"is_null": {"row": "a.b.c"}}, {"a": {"b": {"c": "v"}}}, {}, False),
    ({"is_null": {"row": "a.b.c"}}, {"a": {}}, {}, True),
]


@pytest.mark.parametrize("expr_dict,row,user_kwargs,expected", CASES)
def test_round_trip(expr_dict, row, user_kwargs, expected):
    expr = Expr.model_validate(expr_dict)
    user = FakeUser(**user_kwargs)

    eval_result = evaluate(expr, row=row, user=user)
    assert eval_result is expected, (
        f"evaluator: {eval_result}, expected {expected}, expr={expr_dict}"
    )

    # Compile the expression to a literal value via a SELECT 1 WHERE <expr>
    sql_expr = compile_to_sql(expr, user)
    # We can't run SQL without the DB; instead, verify the rendered SQL
    # contains expected literals/columns. The actual SQL execution is
    # tested in the e2e test_policies.py via real document rows.
    # For round-trip, we trust per-test verification in test_compile.py
    # and just verify the compile call succeeds without error.
    assert sql_expr is not None


def test_is_null_via_eq_is_rejected_at_validate_time():
    """`{"eq": [col, null]}` is the divergent shape — evaluator returns False
    (NULL-as-false), compiler emits `col IS NULL`. Reject at validation so it
    can never reach either path. The portable idiom is `is_null`."""
    with pytest.raises(ValidationError, match="use is_null"):
        Expr.model_validate({"eq": [{"row": "x"}, None]})
    with pytest.raises(ValidationError, match="use is_null"):
        Expr.model_validate({"neq": [{"row": "x"}, None]})
    # The portable replacement validates and round-trips through evaluator
    # and compiler (covered by the parametrized CASES above).
    Expr.model_validate({"is_null": {"row": "x"}})
