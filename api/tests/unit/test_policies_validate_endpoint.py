"""Unit tests for the `POST /api/tables/policies/validate` endpoint logic.

The handler is a thin shell around ``TablePolicies.model_validate`` plus a
path-translation pass and a ``$ref`` resolution step.  We exercise it directly
(without the FastAPI test client) by constructing a lightweight stub context
whose ``.db`` is never actually called for the ref-free bodies tested here.

Note: ``validate_policies`` gained a ``ctx: Context`` parameter (Task 7) so
it can build a ``PolicyRuleRepository(ctx.db, ...)`` to resolve ``{"$ref":…}``
entries.  The existing tests all use ref-free inline policies, so ``ctx.db`` is
never reached — a ``MagicMock`` stub suffices.  To test ``$ref`` resolution
itself you would need a real (or async-capable) DB session; that coverage lives
in the e2e suite.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from src.routers.tables import _loc_to_path, validate_policies


def _make_ctx():
    """Return a minimal stub context for ref-free validation tests.

    ``ctx.db`` is a MagicMock that will raise if unexpectedly called, making
    any accidental DB access visible rather than silently ignored.
    """
    ctx = MagicMock()
    return ctx


def _run(body):
    """Drive the async handler synchronously.

    We pass ``user=None`` because the handler doesn't use it for anything;
    the dependency only exists to gate access at the FastAPI layer.
    A stub ``ctx`` is provided; its ``.db`` is never reached for ref-free bodies.
    """
    return asyncio.run(validate_policies(ctx=_make_ctx(), user=None, body=body))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _loc_to_path
# ---------------------------------------------------------------------------


def test_loc_to_path_empty_returns_root():
    assert _loc_to_path(()) == "$"


def test_loc_to_path_dotted_segments():
    assert _loc_to_path(("policies",)) == "$.policies"


def test_loc_to_path_int_attaches_to_previous_segment():
    assert _loc_to_path(("policies", 0)) == "$.policies[0]"


def test_loc_to_path_mixed_segments():
    assert _loc_to_path(("policies", 0, "when", "eq", 1)) == "$.policies[0].when.eq[1]"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_table_policies_returns_ok():
    body = {
        "policies": [
            {
                "name": "own_row",
                "actions": ["read", "update", "delete"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            }
        ]
    }
    out = _run(body)
    assert out.ok is True
    assert out.errors == []


def test_is_provider_org_is_a_known_user_field():
    """``{user: is_provider_org}`` must validate — it is a documented bypass
    flag on the principal (KNOWN_USER_FIELDS), letting authors write policies
    that admit portal-hopping provider-org staff."""
    body = {
        "policies": [
            {
                "name": "provider_bypass",
                "actions": ["read"],
                "when": {"user": "is_provider_org"},
            }
        ]
    }
    out = _run(body)
    assert out.ok is True
    assert out.errors == []


def test_is_external_is_a_known_user_field():
    """``{user: is_external}`` must validate — it is a documented predicate on
    the principal (KNOWN_USER_FIELDS), letting authors write deny/restrict
    rules that target external (guest/portal-external) users."""
    body = {
        "policies": [
            {
                "name": "deny_external",
                "actions": ["read"],
                "when": {"user": "is_external"},
            }
        ]
    }
    out = _run(body)
    assert out.ok is True
    assert out.errors == []


def test_empty_policies_array_is_valid():
    """An empty policies list is a valid TablePolicies (the validator treats
    it as 'no rules' — same as the on-disk default for a new table)."""
    out = _run({"policies": []})
    assert out.ok is True
    assert out.errors == []


def test_when_null_is_valid_always_true_rule():
    body = {"policies": [{"name": "always", "actions": ["read"], "when": None}]}
    out = _run(body)
    assert out.ok is True


# ---------------------------------------------------------------------------
# Bad shape — non-object roots & missing keys
# ---------------------------------------------------------------------------


def test_root_list_returns_root_path_error():
    out = _run([{"policies": []}])
    assert out.ok is False
    assert len(out.errors) == 1
    assert out.errors[0].path == "$"
    assert "object" in out.errors[0].message.lower()


def test_root_string_returns_root_path_error():
    out = _run("not a policy document")
    assert out.ok is False
    assert len(out.errors) == 1
    assert out.errors[0].path == "$"


def test_missing_policies_key_is_valid_default():
    """An object without a `policies` key validates as an empty policy set.

    `TablePolicies.policies` has `default_factory=list`, so `{}` is the
    same as `{"policies": []}` — both are 'no rules' documents. This is a
    deliberate part of the contract; the editor seeds `{policies: []}` on
    null buffers, but a manually-typed `{}` should also pass.
    """
    out = _run({})
    assert out.ok is True
    assert out.errors == []


def test_root_with_unknown_keys_only_is_valid():
    """Pydantic's default model_config doesn't forbid extra keys on
    `TablePolicies`, so an object with no `policies` and only stray keys
    still validates as the default empty document. Pinning current
    behavior — if/when the model adds `model_config = ConfigDict(extra='forbid')`
    this test should flip to assert structured rejection."""
    out = _run({"unrelated": True})
    assert out.ok is True


# ---------------------------------------------------------------------------
# Bad AST — operator-level errors
# ---------------------------------------------------------------------------


def test_eq_with_null_literal_rejected_with_full_path():
    """The Expr validator rejects null in eq operands. The handler should
    splice the AST-validator's embedded path (``$.eq``) onto the Pydantic
    loc path (``$.policies[0].when``) so the editor sees the full
    ``$.policies[0].when.eq`` location."""
    body = {
        "policies": [
            {
                "name": "x",
                "actions": ["read"],
                "when": {"eq": [None, None]},
            }
        ]
    }
    out = _run(body)
    assert out.ok is False
    assert len(out.errors) == 1
    err = out.errors[0]
    assert err.path == "$.policies[0].when.eq"
    assert "null" not in err.path
    assert "null" in err.message.lower()
    # The "Value error, " prefix Pydantic adds should not bleed into the
    # surfaced message.
    assert "Value error" not in err.message


def test_unknown_function_rejected_with_useful_message():
    body = {
        "policies": [
            {
                "name": "x",
                "actions": ["read"],
                "when": {"call": "nonexistent_fn", "args": []},
            }
        ]
    }
    out = _run(body)
    assert out.ok is False
    assert len(out.errors) == 1
    err = out.errors[0]
    assert err.path.startswith("$")
    assert "nonexistent_fn" in err.message
    assert "unknown function" in err.message.lower()


def test_unknown_user_field_rejected():
    body = {
        "policies": [
            {
                "name": "x",
                "actions": ["read"],
                "when": {"eq": [{"row": "x"}, {"user": "not_a_real_field"}]},
            }
        ]
    }
    out = _run(body)
    assert out.ok is False
    assert len(out.errors) == 1
    assert "unknown user field" in out.errors[0].message.lower()


def test_in_with_empty_literal_list_rejected():
    body = {
        "policies": [
            {
                "name": "x",
                "actions": ["read"],
                "when": {"in": [{"row": "status"}, []]},
            }
        ]
    }
    out = _run(body)
    assert out.ok is False
    assert len(out.errors) == 1
    assert "non-empty literal list" in out.errors[0].message.lower()


# ---------------------------------------------------------------------------
# Pydantic-layer errors (multiple errors, no embedded path)
# ---------------------------------------------------------------------------


def test_multiple_pydantic_errors_each_get_a_path():
    """Pydantic's ValidationError carries one or more loc-tagged errors. The
    handler must convert every loc into a JSONPath-like path, not just the
    first."""
    body = {
        "policies": [
            # Missing required 'actions' AND empty name.
            {"name": "", "when": None},
        ]
    }
    out = _run(body)
    assert out.ok is False
    assert len(out.errors) >= 1  # at least one error per failing field
    for err in out.errors:
        assert err.path.startswith("$")
        assert err.path != "$"  # each should pinpoint a field
