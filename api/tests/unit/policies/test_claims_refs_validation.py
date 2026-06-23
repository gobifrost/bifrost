"""Table policy saves reject claim references outside the table org."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.models.contracts.policies import PolicyRuleRef, TablePolicies
from src.routers.tables import _validate_policy_claim_refs, _validate_table_policy_claim_refs


def test_unknown_claim_reference_rejected():
    expr = {"in": [{"row": "x"}, {"claims": "no_such_claim"}]}
    with pytest.raises(ValueError) as exc:
        _validate_policy_claim_refs(expr, known_claim_names={"allowed_campus_ids"})

    assert "no_such_claim" in str(exc.value)


def test_known_claim_reference_ok():
    expr = {"in": [{"row": "x"}, {"claims": "allowed_campus_ids"}]}

    _validate_policy_claim_refs(expr, known_claim_names={"allowed_campus_ids"})


@pytest.mark.asyncio
async def test_policy_rule_ref_skipped_in_claim_validation():
    """A $ref entry in TablePolicies.policies must not cause AttributeError (no .when)."""
    # Build a TablePolicies payload that mixes a real Policy and a PolicyRuleRef.
    payload = TablePolicies.model_validate(
        {
            "policies": [
                {"$ref": "nonexistent_rule"},
            ]
        }
    )
    assert isinstance(payload.policies[0], PolicyRuleRef)

    # Mock db.execute().scalars().all() to return an empty known-claims set.
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_mock
    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)

    # Must complete without AttributeError — the $ref entry has no .when attribute.
    await _validate_table_policy_claim_refs(db, organization_id=None, policies=payload)
