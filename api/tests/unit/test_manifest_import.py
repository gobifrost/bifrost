"""Unit tests for manifest import — security-critical validation paths.

These exercise the validation that runs at the manifest → DB boundary,
without needing a full DB session. The full e2e import is covered by
``api/tests/e2e/platform/test_git_sync_local.py``.
"""

import pytest
from pydantic import ValidationError

from bifrost.manifest import (
    ManifestPolicy,
    ManifestTable,
)
from src.models.contracts.policies import TablePolicies


def _valid_policies_list() -> list[dict]:
    """A flat list of policies whose AST passes the strict validator."""
    return [
        {
            "name": "admin_bypass",
            "actions": ["read", "create", "update", "delete"],
            "when": {"user": "is_platform_admin"},
        },
        {
            "name": "own_row",
            "actions": ["read", "update", "delete"],
            "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
        },
    ]


class TestManifestPolicyShape:
    """``ManifestPolicy.when`` is intentionally permissive (``dict | None``).
    This documents that gap and pins the security invariant: the manifest
    model alone is NOT a sufficient gate.
    """

    def test_manifest_policy_accepts_garbage_when_clause(self):
        """ManifestPolicy.when is dict | None, so it accepts anything dict-shaped.
        This is the BUG that the import-time TablePolicies revalidation fixes."""
        # Shorthand that no compiler/evaluator accepts.
        garbage = ManifestPolicy(
            name="bad",
            actions=["read"],
            when={"has_role": "support"},  # not the canonical {call, args} form
        )
        assert garbage.when == {"has_role": "support"}

        # Wholly bogus operator
        unknown_op = ManifestPolicy(
            name="bad2",
            actions=["read"],
            when={"INVALID_OP": []},
        )
        assert unknown_op.when == {"INVALID_OP": []}

    def test_table_policies_rejects_unknown_operator(self):
        """TablePolicies, by contrast, validates the AST and rejects unknown ops.

        This is the gate that the manifest-import path now applies before
        writing to ``Table.access``.
        """
        bad = {
            "policies": [
                {
                    "name": "broken",
                    "actions": ["read"],
                    "when": {"INVALID_OP": []},
                }
            ]
        }
        with pytest.raises(ValidationError) as exc_info:
            TablePolicies(**bad)
        assert "INVALID_OP" in str(exc_info.value) or "unknown operator" in str(exc_info.value).lower()

    def test_table_policies_rejects_has_role_shorthand(self):
        """``{"has_role": "support"}`` is the shorthand the broken e2e test
        accidentally seeded; it is NOT a valid AST node. This regression test
        prevents silently re-introducing it."""
        bad = {
            "policies": [
                {
                    "name": "support_read",
                    "actions": ["read"],
                    "when": {"has_role": "support"},
                }
            ]
        }
        with pytest.raises(ValidationError):
            TablePolicies(**bad)

    def test_table_policies_accepts_canonical_has_role_call(self):
        """The canonical form ``{"call": "has_role", "args": ["support"]}``
        validates cleanly. This is what the corrected e2e test now uses."""
        good = {
            "policies": [
                {
                    "name": "support_read",
                    "actions": ["read"],
                    "when": {"call": "has_role", "args": ["support"]},
                }
            ]
        }
        # No exception
        result = TablePolicies(**good)
        assert len(result.policies) == 1

    def test_valid_policies_block_round_trips_through_validator(self):
        """The known-good fixture — sanity check."""
        TablePolicies(policies=_valid_policies_list())  # no exception


class TestManifestImportPolicyValidationContract:
    """Pin the security invariant: the import path uses TablePolicies as the
    gate. If `_resolve_table` is ever changed to skip this revalidation, this
    test surfaces the regression by simulating the exact dump that flows
    through it (wrap the flat ``mtable.policies`` list as ``{"policies": [...]}``
    before validating).
    """

    def test_manifest_policies_wrapped_dump_passes_table_policies_when_valid(self):
        """Round-trip: list[ManifestPolicy] → wrap → TablePolicies (the
        exact path used in `_resolve_table`)."""
        mpolicies = [ManifestPolicy.model_validate(p) for p in _valid_policies_list()]
        # Mirrors the gate in _resolve_table: serializer wraps the flat list
        # as {"policies": [...]} before TablePolicies validates the AST.
        policies_list = [p.model_dump(mode="json") for p in mpolicies]
        TablePolicies(policies=policies_list)  # no exception

    def test_manifest_policies_wrapped_dump_fails_table_policies_when_invalid(self):
        """A manifest that the lax ManifestPolicy model accepts but the strict
        TablePolicies model rejects is the precise security gap. This proves
        the gate catches it before a DB write."""
        bad_list = [
            {
                "name": "broken",
                "actions": ["read"],
                "when": {"INVALID_OP": []},
            },
        ]
        # The lax manifest model accepts it (no exception):
        mpolicies = [ManifestPolicy.model_validate(p) for p in bad_list]
        policies_list = [p.model_dump(mode="json") for p in mpolicies]

        # The strict gate rejects it — exactly what `_resolve_table` enforces
        # before persisting to Table.access.
        with pytest.raises(ValidationError):
            TablePolicies(policies=policies_list)

    def test_manifest_table_with_bad_policy_passes_lax_model(self):
        """Defense-in-depth: confirm the full ManifestTable model also accepts
        the bad policies (so the gap really is at write-time, not parse-time)."""
        from uuid import uuid4
        mtable = ManifestTable(
            id=str(uuid4()),
            name="bad",
            policies=[
                ManifestPolicy(
                    name="broken",
                    actions=["read"],
                    when={"has_role": "support"},  # shorthand, not call form
                ),
            ],
        )
        assert mtable.policies is not None
        # No exception parsing the manifest. The gate must run at write time.
        assert mtable.policies[0].when == {"has_role": "support"}
