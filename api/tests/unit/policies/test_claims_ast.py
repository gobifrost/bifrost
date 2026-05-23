"""AST validation for the {claims: <name>} reference."""
import pytest
from pydantic import ValidationError

from src.models.contracts.policies import Expr


def test_claims_reference_validates_in_rhs():
    # The whole point: this should NOT raise.
    Expr({"in": [{"row": "campus_id"}, {"claims": "allowed_campus_ids"}]})


def test_claims_reference_value_must_be_nonempty_string():
    with pytest.raises(ValidationError):
        Expr({"in": [{"row": "x"}, {"claims": ""}]})
    with pytest.raises(ValidationError):
        Expr({"in": [{"row": "x"}, {"claims": 123}]})  # type: ignore[dict-item]


def test_in_rhs_still_accepts_literal_list():
    Expr({"in": [{"row": "x"}, ["a", "b"]]})


def test_in_rhs_rejects_unknown_dict_shape():
    with pytest.raises(ValidationError):
        Expr({"in": [{"row": "x"}, {"unknown": "y"}]})


def test_in_rhs_rejects_empty_literal_list_still():
    with pytest.raises(ValidationError):
        Expr({"in": [{"row": "x"}, []]})


def test_eq_does_not_yet_accept_claims_rhs():
    # Scalar claims via eq/lt/etc. is future work — must reject for v1.
    with pytest.raises(ValidationError):
        Expr({"eq": [{"row": "x"}, {"claims": "some_scalar"}]})


def test_claims_reference_anywhere_else_rejected():
    # Top-level operator dict can't be {claims: ...}
    with pytest.raises(ValidationError):
        Expr({"claims": "x"})
    # Inside `and`/`or` operand position — not allowed (claims aren't bools).
    with pytest.raises(ValidationError):
        Expr({"and": [{"claims": "x"}, {"claims": "y"}]})


def test_claims_in_complex_policy():
    # Real-world shape — should validate.
    Expr({
        "and": [
            {"in": [{"row": "campus_id"}, {"claims": "allowed_campus_ids"}]},
            {"in": [{"row": "doc_type_id"}, {"claims": "allowed_doc_type_ids"}]},
        ]
    })
