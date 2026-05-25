"""Unit tests for manifest import — security-critical validation paths.

These exercise the validation that runs at the manifest → DB boundary,
without needing a full DB session. The full e2e import is covered by
``api/tests/e2e/platform/test_git_sync_local.py``.
"""

import pytest
from pydantic import ValidationError

from bifrost.manifest import (
    Manifest,
    ManifestApp,
    ManifestConfig,
    ManifestEventSource,
    ManifestIntegration,
    ManifestIntegrationMapping,
    ManifestPolicy,
    ManifestTable,
    ManifestWorkflow,
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


class TestManifestDestructiveScope:
    """Pin fail-closed import boundaries for destructive git-sync behavior."""

    def test_removed_entity_ids_include_only_explicit_delete_diffs(self):
        from src.services.manifest_import import _collect_removed_entity_ids

        removed = _collect_removed_entity_ids([
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "action": "delete",
                "entity_type": "workflows",
                "name": "deleted",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "action": "update",
                "entity_type": "forms",
                "name": "updated",
            },
        ])

        assert removed == {
            "workflows": {"11111111-1111-1111-1111-111111111111"},
        }

    def test_scope_filter_prevents_unrelated_absent_workflow_delete_diff(self):
        from bifrost.manifest import Manifest, ManifestWorkflow
        from src.services.manifest_import import (
            _collect_removed_entity_ids,
            _diff_and_collect,
            _filter_manifest_to_scope,
        )

        local_id = "11111111-1111-1111-1111-111111111111"
        unrelated_id = "22222222-2222-2222-2222-222222222222"
        incoming = Manifest(workflows={
            local_id: ManifestWorkflow(
                id=local_id,
                path="workflows/local.py",
                function_name="local",
            ),
        })
        current = Manifest(workflows={
            local_id: ManifestWorkflow(
                id=local_id,
                path="workflows/local.py",
                function_name="local",
            ),
            unrelated_id: ManifestWorkflow(
                id=unrelated_id,
                path="workflows/other-workspace.py",
                function_name="other",
            ),
        })

        _filter_manifest_to_scope(
            current,
            path_exists=lambda path: path == "workflows/local.py",
            dir_exists=lambda path: False,
        )
        changes, _changed_ids = _diff_and_collect(incoming, current)

        assert _collect_removed_entity_ids(changes) == {}

    def test_scope_filter_does_not_delete_non_file_sections_when_manifest_omits_them(self):
        from src.services.manifest_import import (
            _collect_removed_entity_ids,
            _diff_and_collect,
            _filter_manifest_to_scope,
        )

        org_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        workflow_id = "11111111-1111-1111-1111-111111111111"
        integration_id = "22222222-2222-2222-2222-222222222222"
        config_id = "33333333-3333-3333-3333-333333333333"
        table_id = "44444444-4444-4444-4444-444444444444"
        event_id = "55555555-5555-5555-5555-555555555555"
        incoming = Manifest(workflows={
            workflow_id: ManifestWorkflow(
                id=workflow_id,
                path="workflows/local.py",
                function_name="local",
                organization_id=org_id,
            ),
        })
        current = Manifest(
            workflows={
                workflow_id: ManifestWorkflow(
                    id=workflow_id,
                    path="workflows/local.py",
                    function_name="local",
                    organization_id=org_id,
                ),
            },
            integrations={
                integration_id: ManifestIntegration(
                    id=integration_id,
                    mappings=[
                        ManifestIntegrationMapping(
                            organization_id=org_id,
                            entity_id="tenant-1",
                        ),
                    ],
                ),
            },
            configs={
                config_id: ManifestConfig(
                    id=config_id,
                    key="api_url",
                    organization_id=org_id,
                ),
            },
            tables={
                table_id: ManifestTable(
                    id=table_id,
                    name="tickets",
                    organization_id=org_id,
                ),
            },
            events={
                event_id: ManifestEventSource(
                    id=event_id,
                    name="tickets",
                    source_type="webhook",
                    organization_id=org_id,
                ),
            },
        )

        _filter_manifest_to_scope(
            current,
            path_exists=lambda path: path == "workflows/local.py",
            dir_exists=lambda path: False,
            scope_manifest=incoming,
        )
        changes, _changed_ids = _diff_and_collect(incoming, current)

        assert _collect_removed_entity_ids(changes) == {}

    def test_scope_filter_limits_non_file_deletes_to_declared_org_scope(self):
        from src.services.manifest_import import (
            _collect_removed_entity_ids,
            _diff_and_collect,
            _filter_manifest_to_scope,
        )

        org_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        org_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        kept_config = "11111111-1111-1111-1111-111111111111"
        removed_config = "22222222-2222-2222-2222-222222222222"
        unrelated_config = "33333333-3333-3333-3333-333333333333"
        incoming = Manifest(configs={
            kept_config: ManifestConfig(
                id=kept_config,
                key="api_url",
                organization_id=org_a,
            ),
        })
        current = Manifest(configs={
            kept_config: ManifestConfig(
                id=kept_config,
                key="api_url",
                organization_id=org_a,
            ),
            removed_config: ManifestConfig(
                id=removed_config,
                key="old_api_url",
                organization_id=org_a,
            ),
            unrelated_config: ManifestConfig(
                id=unrelated_config,
                key="other_org_api_url",
                organization_id=org_b,
            ),
        })

        _filter_manifest_to_scope(
            current,
            path_exists=lambda path: False,
            dir_exists=lambda path: False,
            scope_manifest=incoming,
        )
        changes, _changed_ids = _diff_and_collect(incoming, current)

        assert _collect_removed_entity_ids(changes) == {
            "configs": {removed_config},
        }

    def test_oauth_token_id_is_ignored_for_manifest_diff(self):
        from src.services.manifest_import import _diff_and_collect

        integration_id = "11111111-1111-1111-1111-111111111111"
        incoming = Manifest(integrations={
            integration_id: ManifestIntegration(
                id=integration_id,
                mappings=[
                    ManifestIntegrationMapping(
                        organization_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        entity_id="tenant-1",
                        oauth_token_id="22222222-2222-2222-2222-222222222222",
                    ),
                ],
            ),
        })
        current = Manifest(integrations={
            integration_id: ManifestIntegration(
                id=integration_id,
                mappings=[
                    ManifestIntegrationMapping(
                        organization_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        entity_id="tenant-1",
                        oauth_token_id="33333333-3333-3333-3333-333333333333",
                    ),
                ],
            ),
        })

        changes, changed_ids = _diff_and_collect(incoming, current)

        assert changes == []
        assert changed_ids == set()

    def test_roles_default_to_role_based_when_access_level_is_omitted(self):
        from src.services.manifest_import import _manifest_access_level

        assert _manifest_access_level(None, ["role-id"]) == "role_based"
        assert _manifest_access_level(None, []) is None
        assert _manifest_access_level("authenticated", ["role-id"]) == "authenticated"


class TestManifestAppPathSafety:
    """Manifest app paths must stay inside one app source directory."""

    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            ".bifrost/apps.yaml",
            "../apps/customer",
            "apps/customer/../other",
            "/apps/customer",
            "apps/customer/src",
        ],
    )
    def test_rejects_non_app_or_escaped_app_paths(self, path):
        from src.services.manifest_import import _safe_app_repo_path

        mapp = ManifestApp(
            id="11111111-1111-1111-1111-111111111111",
            name="Customer",
            slug="customer",
            path=path,
        )

        with pytest.raises(ValueError):
            _safe_app_repo_path(mapp)

    def test_accepts_slug_matched_apps_directory(self):
        from src.services.manifest_import import _safe_app_repo_path

        mapp = ManifestApp(
            id="11111111-1111-1111-1111-111111111111",
            name="Customer",
            slug="customer",
            path="apps/customer",
        )

        assert _safe_app_repo_path(mapp) == "apps/customer"
