"""Unit tests for helpers extracted from api/src/routers/claims.py."""

from shared.claims.registry import referenced_claim_names


def test_referenced_claim_names_handles_raw_policy_when_dict():
    # Simulates how _tables_referencing_claim reads from Table.access JSONB:
    # access['policies'][i]['when'] is a plain dict, not a Pydantic model.
    when = {
        "and": [
            {"in": [{"row": "campus_id"}, {"claims": "allowed_campus_ids"}]},
            {"eq": [{"row": "x"}, "y"]},
        ]
    }
    assert "allowed_campus_ids" in referenced_claim_names(when)


def test_referenced_claim_names_handles_none_when():
    # admin_bypass-style policy may have when=None or be absent.
    assert referenced_claim_names(None) == set()
