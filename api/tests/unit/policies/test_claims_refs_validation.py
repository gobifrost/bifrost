"""Table policy saves reject claim references outside the table org."""

import pytest

from src.routers.tables import _validate_policy_claim_refs


def test_unknown_claim_reference_rejected():
    expr = {"in": [{"row": "x"}, {"claims": "no_such_claim"}]}
    with pytest.raises(ValueError) as exc:
        _validate_policy_claim_refs(expr, known_claim_names={"allowed_campus_ids"})

    assert "no_such_claim" in str(exc.value)


def test_known_claim_reference_ok():
    expr = {"in": [{"row": "x"}, {"claims": "allowed_campus_ids"}]}

    _validate_policy_claim_refs(expr, known_claim_names={"allowed_campus_ids"})
