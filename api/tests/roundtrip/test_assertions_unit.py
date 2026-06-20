"""Unit tests for assertions.py — pure logic, no DB, no stack required.

Covers all 8 cases required by the task brief:
1. keep-pass
2. keep-fail-on-drop
3. scrub-pass
4. leak-detector
5. canonical-order-independence
6. pair_rows raises on missing match
7. pair_rows raises on duplicate match
8. by_match_key skips a stamped org key-part
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from bifrost.field_classes import FieldClass, classify
from tests.roundtrip.assertions import (
    assert_field_roundtrip,
    assert_no_secret_leak,
    canonical,
    pair_rows,
)

# ---------------------------------------------------------------------------
# Minimal Pydantic models for testing
# ---------------------------------------------------------------------------


class _Simple(BaseModel):
    """Simple model with one CONTENT field (no predicate)."""
    name: str = Field(**classify(FieldClass.CONTENT))
    secret_val: str | None = Field(default=None, **classify(FieldClass.SECRET))


class _WithOrgKey(BaseModel):
    """Model with key (CONTENT, match_key) + org (ENVIRONMENT, match_key)."""
    key: str = Field(**classify(FieldClass.CONTENT, match_key=True))
    organization_id: str | None = Field(default=None, **classify(FieldClass.ENVIRONMENT, match_key=True))
    value: str | None = Field(default=None, **classify(FieldClass.CONTENT))


# Shared policies used across tests
_REPO_POLICY: dict[FieldClass, str] = {
    FieldClass.IDENTITY: "keep",
    FieldClass.CONTENT: "keep",
    FieldClass.ENVIRONMENT: "keep",
    FieldClass.SECRET: "scrub",
    FieldClass.REFERENCE: "keep",
}

_SOLUTION_POLICY: dict[FieldClass, str] = {
    FieldClass.IDENTITY: "stamp",
    FieldClass.CONTENT: "keep",
    FieldClass.ENVIRONMENT: "stamp",   # org is re-stamped on install
    FieldClass.SECRET: "scrub",
    FieldClass.REFERENCE: "remap",
}


# ---------------------------------------------------------------------------
# 1. keep-pass: CONTENT field unchanged → passes
# ---------------------------------------------------------------------------
def test_keep_pass() -> None:
    before = {"name": "hello"}
    after = {"name": "hello"}
    assert_field_roundtrip(_Simple, "name", before, after, _REPO_POLICY, before)


# ---------------------------------------------------------------------------
# 2. keep-fail-on-drop: CONTENT field changed → AssertionError
# ---------------------------------------------------------------------------
def test_keep_fail_on_drop() -> None:
    before = {"name": "hello"}
    after = {"name": "world"}
    with pytest.raises(AssertionError, match="changed"):
        assert_field_roundtrip(_Simple, "name", before, after, _REPO_POLICY, before)


# ---------------------------------------------------------------------------
# 3. scrub-pass: SECRET field is None in after → passes
# ---------------------------------------------------------------------------
def test_scrub_pass() -> None:
    before = {"secret_val": "s3cr3t"}
    after = {"secret_val": None}
    assert_field_roundtrip(_Simple, "secret_val", before, after, _REPO_POLICY, before)


# ---------------------------------------------------------------------------
# 4. leak-detector: assert_no_secret_leak raises when sentinel appears
# ---------------------------------------------------------------------------
def test_leak_detector_clean() -> None:
    assert_no_secret_leak('{"name": "hello"}', ["s3cr3t"])


def test_leak_detector_catches_leak() -> None:
    with pytest.raises(AssertionError, match="leaked"):
        assert_no_secret_leak('{"value": "s3cr3t"}', ["s3cr3t"])


# ---------------------------------------------------------------------------
# 5. canonical-order-independence: dicts with different key order are equal
# ---------------------------------------------------------------------------
def test_canonical_order_independence() -> None:
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert canonical(a) == canonical(b)


def test_canonical_order_mismatch_on_value() -> None:
    a = {"a": 1}
    b = {"a": 2}
    assert canonical(a) != canonical(b)


# ---------------------------------------------------------------------------
# 6. pair_rows raises on missing match
# ---------------------------------------------------------------------------
def test_pair_rows_raises_on_missing() -> None:
    before_rows = [{"id": "aaa", "name": "x"}]
    after_rows: list[dict] = []  # nothing to match against

    with pytest.raises(AssertionError, match="expected exactly 1 match"):
        pair_rows(_Simple, before_rows, after_rows, "by_id", _REPO_POLICY)


# ---------------------------------------------------------------------------
# 7. pair_rows raises on duplicate match
# ---------------------------------------------------------------------------
def test_pair_rows_raises_on_duplicate() -> None:
    before_rows = [{"id": "aaa", "name": "x"}]
    after_rows = [
        {"id": "aaa", "name": "x"},
        {"id": "aaa", "name": "x"},  # duplicate id
    ]

    with pytest.raises(AssertionError, match="expected exactly 1 match"):
        pair_rows(_Simple, before_rows, after_rows, "by_id", _REPO_POLICY)


# ---------------------------------------------------------------------------
# 8. by_match_key skips a stamped org key-part
#    Under _SOLUTION_POLICY, ENVIRONMENT is "stamp" → organization_id excluded.
#    Match must succeed on just `key`, even though org changed.
# ---------------------------------------------------------------------------
def test_by_match_key_skips_stamped_org() -> None:
    before_rows = [{"key": "db_url", "organization_id": "org-old", "value": "postgres://old"}]
    after_rows = [{"key": "db_url", "organization_id": "org-new", "value": "postgres://old"}]

    pairs = pair_rows(_WithOrgKey, before_rows, after_rows, "by_match_key", _SOLUTION_POLICY)
    assert len(pairs) == 1
    b, a = pairs[0]
    assert b["organization_id"] == "org-old"
    assert a["organization_id"] == "org-new"


def test_by_match_key_no_surviving_key_raises() -> None:
    """If ALL match-key fields are stamped, pair_rows must raise before trying to match."""
    class _AllStamped(BaseModel):
        organization_id: str = Field(**classify(FieldClass.ENVIRONMENT, match_key=True))

    before_rows = [{"organization_id": "org-a"}]
    after_rows = [{"organization_id": "org-b"}]

    with pytest.raises(AssertionError, match="no surviving match-key"):
        pair_rows(_AllStamped, before_rows, after_rows, "by_match_key", _SOLUTION_POLICY)


# ---------------------------------------------------------------------------
# Extra: by_remap strategy — exact id check
# ---------------------------------------------------------------------------
def test_by_remap_matches_expected_id() -> None:
    before_rows = [{"id": "old-id", "name": "x"}]
    after_rows = [{"id": "new-id", "name": "x"}]

    pairs = pair_rows(
        _Simple,
        before_rows,
        after_rows,
        "by_remap",
        _SOLUTION_POLICY,
        expected_id=lambda b: "new-id",
    )
    assert len(pairs) == 1


def test_by_remap_missing_raises() -> None:
    before_rows = [{"id": "old-id", "name": "x"}]
    after_rows = [{"id": "completely-different-id", "name": "x"}]

    with pytest.raises(AssertionError, match="expected exactly 1 match"):
        pair_rows(
            _Simple,
            before_rows,
            after_rows,
            "by_remap",
            _SOLUTION_POLICY,
            expected_id=lambda b: "new-id",  # maps to "new-id" which doesn't exist
        )
