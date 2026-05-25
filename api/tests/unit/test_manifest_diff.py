"""Tests for manifest diff logic (_diff_and_collect).

Validates round-trip fidelity (YAML → parse → diff should show no changes),
correct detection of add/update/delete, and safe handling for UI-managed fields
like oauth_token_id that are set in the platform UI but not in local YAML.
"""
import copy
import pytest
from uuid import uuid4

import yaml

from bifrost.manifest import (
    Manifest,
    ManifestOrganization,
    ManifestRole,
    ManifestWorkflow,
    ManifestForm,
    ManifestAgent,
    ManifestApp,
    ManifestIntegration,
    ManifestIntegrationConfigSchema,
    ManifestIntegrationMapping,
    ManifestOAuthProvider,
    ManifestConfig,
    ManifestTable,
    ManifestEventSource,
    ManifestEventSubscription,
    serialize_manifest_dir,
    parse_manifest_dir,
)
from src.services.manifest_import import _diff_and_collect


# =============================================================================
# Fixture: kitchen-sink manifest with all entity types fully populated
# =============================================================================

ORG_ID = str(uuid4())
ROLE_ID_A = str(uuid4())
ROLE_ID_B = str(uuid4())
WF_ID = str(uuid4())
FORM_ID = str(uuid4())
AGENT_ID = str(uuid4())
APP_ID = str(uuid4())
INTEG_ID = str(uuid4())
CONFIG_STR_ID = str(uuid4())
CONFIG_INT_ID = str(uuid4())
CONFIG_BOOL_ID = str(uuid4())
CONFIG_JSON_ID = str(uuid4())
CONFIG_SECRET_ID = str(uuid4())
TABLE_ID = str(uuid4())
EVENT_ID = str(uuid4())
SUB_WF_ID = str(uuid4())
SUB_AGENT_ID = str(uuid4())


@pytest.fixture
def kitchen_sink_manifest() -> Manifest:
    """Build a manifest with every entity type fully populated."""
    return Manifest(
        organizations=[
            ManifestOrganization(id=ORG_ID, name="AcmeCorp", is_active=True),
        ],
        roles=[
            ManifestRole(id=ROLE_ID_A, name="admin"),
            ManifestRole(id=ROLE_ID_B, name="viewer"),
        ],
        workflows={
            WF_ID: ManifestWorkflow(
                id=WF_ID,
                name="onboard_user",
                path="workflows/onboard.py",
                function_name="onboard_user",
                type="workflow",
                organization_id=ORG_ID,
                roles=[ROLE_ID_A],
                access_level="role_based",
                endpoint_enabled=True,
                timeout_seconds=600,
                description="Onboard a new user",
                category="HR",
                tags=["onboarding", "hr"],
            ),
        },
        forms={
            FORM_ID: ManifestForm(
                id=FORM_ID,
                name="feedback_form",
                path="forms/feedback.form.yaml",
                organization_id=ORG_ID,
                roles=[ROLE_ID_A, ROLE_ID_B],
                access_level="role_based",
            ),
        },
        agents={
            AGENT_ID: ManifestAgent(
                id=AGENT_ID,
                name="support_agent",
                path="agents/support.agent.yaml",
                organization_id=ORG_ID,
                roles=[ROLE_ID_A],
                access_level="authenticated",
                max_iterations=10,
                max_token_budget=50000,
            ),
        },
        apps={
            APP_ID: ManifestApp(
                id=APP_ID,
                path="apps/dashboard",
                slug="dashboard",
                name="Dashboard",
                description="Main dashboard",
                dependencies={"react": "^18.0.0", "chart.js": "^4.0.0"},
                organization_id=ORG_ID,
                roles=[ROLE_ID_A, ROLE_ID_B],
                access_level="role_based",
            ),
        },
        integrations={
            INTEG_ID: ManifestIntegration(
                id=INTEG_ID,
                name="HaloPSA",
                entity_id="tenant_id",
                entity_id_name="Tenant ID",
                default_entity_id=None,
                config_schema=[
                    ManifestIntegrationConfigSchema(
                        key="api_url",
                        type="string",
                        required=True,
                        description="HaloPSA API endpoint",
                        position=0,
                    ),
                    ManifestIntegrationConfigSchema(
                        key="client_secret",
                        type="secret",
                        required=True,
                        description="OAuth client secret",
                        position=1,
                    ),
                ],
                oauth_provider=ManifestOAuthProvider(
                    provider_name="halopsa",
                    display_name="HaloPSA OAuth",
                    oauth_flow_type="client_credentials",
                    client_id="my-client-id-123",
                    authorization_url=None,
                    token_url="https://halopsa.example.com/auth/token",
                    scopes=["all"],
                ),
                mappings=[
                    ManifestIntegrationMapping(
                        organization_id=ORG_ID,
                        entity_id="tenant-001",
                        entity_name="Acme Tenant",
                        oauth_token_id="token-uuid-set-by-ui",
                    ),
                ],
            ),
        },
        configs={
            CONFIG_STR_ID: ManifestConfig(
                id=CONFIG_STR_ID,
                integration_id=INTEG_ID,
                key="api_url",
                config_type="string",
                description="API endpoint URL",
                organization_id=ORG_ID,
                value="https://api.halopsa.example.com",
            ),
            CONFIG_INT_ID: ManifestConfig(
                id=CONFIG_INT_ID,
                key="max_retries",
                config_type="int",
                value=3,
            ),
            CONFIG_BOOL_ID: ManifestConfig(
                id=CONFIG_BOOL_ID,
                key="debug_mode",
                config_type="bool",
                value=False,
            ),
            CONFIG_JSON_ID: ManifestConfig(
                id=CONFIG_JSON_ID,
                key="feature_flags",
                config_type="json",
                value={"enable_v2": True, "beta_users": ["alice"]},
            ),
            CONFIG_SECRET_ID: ManifestConfig(
                id=CONFIG_SECRET_ID,
                integration_id=INTEG_ID,
                key="client_secret",
                config_type="secret",
                description="OAuth secret",
                organization_id=ORG_ID,
                value=None,  # secrets always null in manifest
            ),
        },
        tables={
            TABLE_ID: ManifestTable(
                id=TABLE_ID,
                name="audit_log",
                description="Track all actions",
                organization_id=ORG_ID,
                **{"schema": {"columns": [{"name": "action", "type": "text"}]}},  # type: ignore[arg-type]
            ),
        },
        events={
            EVENT_ID: ManifestEventSource(
                id=EVENT_ID,
                name="ticket_watcher",
                source_type="webhook",
                organization_id=ORG_ID,
                is_active=True,
                cron_expression=None,
                timezone=None,
                schedule_enabled=None,
                adapter_name="halopsa",
                webhook_integration_id=INTEG_ID,
                webhook_config={"verify_signature": True},
                subscriptions=[
                    ManifestEventSubscription(
                        id=SUB_WF_ID,
                        target_type="workflow",
                        workflow_id=WF_ID,
                        event_type="ticket.created",
                        filter_expression="$.priority == 'high'",
                        input_mapping={"ticket_id": "$.id"},
                        is_active=True,
                    ),
                    ManifestEventSubscription(
                        id=SUB_AGENT_ID,
                        target_type="agent",
                        agent_id=AGENT_ID,
                        event_type="ticket.updated",
                        is_active=True,
                    ),
                ],
            ),
        },
    )


# =============================================================================
# Group A: Round-trip fidelity (confirm false-positive root cause)
# =============================================================================


class TestRoundTripFidelity:
    """Tests that serializing a manifest to YAML and parsing it back
    produces an identical diff — i.e., no false-positive changes."""

    def test_yaml_round_trip_no_diff(self, kitchen_sink_manifest: Manifest):
        """serialize → YAML → parse → diff should show zero changes.

        If this fails, we've confirmed the false-positive bug: the diff
        comparison flags entities as changed even though they're identical
        after a YAML round-trip.
        """
        files = serialize_manifest_dir(kitchen_sink_manifest)
        restored = parse_manifest_dir(files)

        changes, changed_ids = _diff_and_collect(restored, kitchen_sink_manifest)

        assert changes == [], (
            f"Round-tripped manifest should produce no diff, but got: {changes}"
        )
        assert changed_ids == set()

    def test_yaml_round_trip_symmetric(self, kitchen_sink_manifest: Manifest):
        """Diff should also be empty when original is incoming and restored is current."""
        files = serialize_manifest_dir(kitchen_sink_manifest)
        restored = parse_manifest_dir(files)

        changes, changed_ids = _diff_and_collect(kitchen_sink_manifest, restored)

        assert changes == [], (
            f"Symmetric round-trip should produce no diff, but got: {changes}"
        )
        assert changed_ids == set()

    def test_ui_managed_oauth_token_id_does_not_cause_diff(self, kitchen_sink_manifest: Manifest):
        """Local manifest without oauth_token_id vs DB manifest with it set.

        This is the scenario: user defines integration in YAML without
        oauth_token_id, then connects OAuth in the UI. On next startup,
        local YAML has oauth_token_id=None, DB has it populated.
        The token link is environment-owned and must not trigger a manifest
        import update.
        """
        # "Local" manifest: no oauth_token_id
        local = copy.deepcopy(kitchen_sink_manifest)
        local.integrations[INTEG_ID].mappings[0].oauth_token_id = None

        # "DB" manifest: oauth_token_id set by UI
        db_side = kitchen_sink_manifest  # has oauth_token_id="token-uuid-set-by-ui"

        changes, changed_ids = _diff_and_collect(local, db_side)

        assert changes == []
        assert changed_ids == set()

    def test_secret_config_round_trip(self, kitchen_sink_manifest: Manifest):
        """Config with config_type=secret, value=None on both sides → no diff."""
        # Both sides have the secret config with value=None
        other = copy.deepcopy(kitchen_sink_manifest)

        # Isolate to just the secret config
        secret_only_a = Manifest(configs={CONFIG_SECRET_ID: kitchen_sink_manifest.configs[CONFIG_SECRET_ID]})
        secret_only_b = Manifest(configs={CONFIG_SECRET_ID: other.configs[CONFIG_SECRET_ID]})

        changes, _ = _diff_and_collect(secret_only_a, secret_only_b)
        assert changes == [], f"Secret configs with value=None should not diff: {changes}"

    def test_type_coercion_int_vs_string(self):
        """Config value 42 (int from YAML) vs "42" (string) — document behavior."""
        config_id = str(uuid4())
        config_a = ManifestConfig(id=config_id, key="retries", config_type="int", value=42)
        config_b = ManifestConfig(id=config_id, key="retries", config_type="int", value="42")

        manifest_a = Manifest(configs={config_id: config_a})
        manifest_b = Manifest(configs={config_id: config_b})

        changes, _ = _diff_and_collect(manifest_a, manifest_b)

        # Document: int vs string representation of same value DOES cause a diff
        # because model_dump produces different JSON (42 vs "42")
        assert len(changes) == 1, "int vs string should be detected as different"
        assert changes[0]["action"] == "update"

    def test_ui_managed_fields_no_diff_after_merge(self, kitchen_sink_manifest: Manifest):
        """After merging server manifest into local YAML, UI-managed fields
        should not cause false positives on subsequent startup."""
        # Simulate: user creates integration in YAML without oauth_token_id
        local = copy.deepcopy(kitchen_sink_manifest)
        local.integrations[INTEG_ID].mappings[0].oauth_token_id = None

        # Simulate: server returns manifest with oauth_token_id set
        # We merge server manifest into local → local now has oauth_token_id
        merged = copy.deepcopy(local)
        merged.integrations[INTEG_ID].mappings[0].oauth_token_id = "token-uuid-set-by-ui"

        # On NEXT startup, local (with merged data) vs DB should show no diff
        changes, _ = _diff_and_collect(merged, kitchen_sink_manifest)
        assert changes == [], (
            f"After merging server data, should have no diff: {changes}"
        )

    def test_double_round_trip_stability(self, kitchen_sink_manifest: Manifest):
        """serialize → parse → serialize → parse should be identical to single round-trip."""
        files1 = serialize_manifest_dir(kitchen_sink_manifest)
        restored1 = parse_manifest_dir(files1)
        files2 = serialize_manifest_dir(restored1)
        restored2 = parse_manifest_dir(files2)

        changes, _ = _diff_and_collect(restored1, restored2)
        assert changes == [], f"Double round-trip should be stable: {changes}"


# =============================================================================
# Group B: Correct diff detection (validate existing functionality)
# =============================================================================


class TestDiffDetection:
    """Validate that the diff correctly identifies add/update/delete/keep."""

    def test_new_entity_detected(self, kitchen_sink_manifest: Manifest):
        """Entity in incoming only → action='add'."""
        new_wf_id = str(uuid4())
        incoming = copy.deepcopy(kitchen_sink_manifest)
        incoming.workflows[new_wf_id] = ManifestWorkflow(
            id=new_wf_id,
            name="new_workflow",
            path="workflows/new.py",
            function_name="new_workflow",
        )
        current = kitchen_sink_manifest

        changes, changed_ids = _diff_and_collect(incoming, current)

        wf_adds = [c for c in changes if c["entity_type"] == "workflows" and c["action"] == "add"]
        assert len(wf_adds) == 1
        assert wf_adds[0]["name"] == "new_workflow"
        assert new_wf_id in changed_ids

    def test_deleted_entity_detected(self, kitchen_sink_manifest: Manifest):
        """Entity in current only → action='delete'."""
        incoming = copy.deepcopy(kitchen_sink_manifest)
        del incoming.workflows[WF_ID]

        changes, changed_ids = _diff_and_collect(incoming, kitchen_sink_manifest)

        wf_deletes = [c for c in changes if c["entity_type"] == "workflows" and c["action"] == "delete"]
        assert len(wf_deletes) == 1
        assert wf_deletes[0]["name"] == "onboard_user"
        assert WF_ID in changed_ids

    def test_real_update_detected(self, kitchen_sink_manifest: Manifest):
        """Entity with changed name → action='update'."""
        incoming = copy.deepcopy(kitchen_sink_manifest)
        incoming.workflows[WF_ID].name = "renamed_workflow"

        changes, changed_ids = _diff_and_collect(incoming, kitchen_sink_manifest)

        wf_updates = [c for c in changes if c["entity_type"] == "workflows" and c["action"] == "update"]
        assert len(wf_updates) == 1
        assert wf_updates[0]["name"] == "renamed_workflow"
        assert WF_ID in changed_ids

    def test_identical_entities_no_diff(self, kitchen_sink_manifest: Manifest):
        """Identical manifests should produce zero changes."""
        other = copy.deepcopy(kitchen_sink_manifest)

        changes, changed_ids = _diff_and_collect(kitchen_sink_manifest, other)

        assert changes == [], f"Identical manifests should have no diff: {changes}"
        assert changed_ids == set()

    def test_integration_cascade_marks_configs(self, kitchen_sink_manifest: Manifest):
        """When an integration changes, dependent config IDs should be in changed_ids."""
        incoming = copy.deepcopy(kitchen_sink_manifest)
        incoming.integrations[INTEG_ID].name = "HaloPSA_v2"

        _, changed_ids = _diff_and_collect(incoming, kitchen_sink_manifest)

        # Integration itself should be changed
        assert INTEG_ID in changed_ids

        # Configs that reference this integration should also be in changed_ids
        assert CONFIG_STR_ID in changed_ids, "String config referencing changed integration should be in changed_ids"
        assert CONFIG_SECRET_ID in changed_ids, "Secret config referencing changed integration should be in changed_ids"

        # Configs NOT referencing this integration should NOT be affected
        assert CONFIG_INT_ID not in changed_ids
        assert CONFIG_BOOL_ID not in changed_ids

    def test_organization_add(self):
        """New organization detected as add."""
        org_id = str(uuid4())
        incoming = Manifest(organizations=[ManifestOrganization(id=org_id, name="NewOrg")])
        current = Manifest()

        changes, _ = _diff_and_collect(incoming, current)
        assert len(changes) == 1
        assert changes[0]["action"] == "add"
        assert changes[0]["entity_type"] == "organizations"

    def test_organization_delete(self):
        """Removed organization detected as delete."""
        org_id = str(uuid4())
        incoming = Manifest()
        current = Manifest(organizations=[ManifestOrganization(id=org_id, name="OldOrg")])

        changes, _ = _diff_and_collect(incoming, current)
        assert len(changes) == 1
        assert changes[0]["action"] == "delete"
        assert changes[0]["entity_type"] == "organizations"

    def test_role_update(self):
        """Role with changed name detected as update."""
        role_id = str(uuid4())
        incoming = Manifest(roles=[ManifestRole(id=role_id, name="super_admin")])
        current = Manifest(roles=[ManifestRole(id=role_id, name="admin")])

        changes, _ = _diff_and_collect(incoming, current)
        assert len(changes) == 1
        assert changes[0]["action"] == "update"

    def test_changes_sorted_by_type_action_name(self, kitchen_sink_manifest: Manifest):
        """Changes should be sorted by entity_type, then action priority, then name."""
        # Add a new workflow and delete an existing one
        incoming = copy.deepcopy(kitchen_sink_manifest)
        new_id = str(uuid4())
        incoming.workflows[new_id] = ManifestWorkflow(
            id=new_id, name="alpha_workflow", path="workflows/alpha.py", function_name="alpha",
        )
        del incoming.workflows[WF_ID]

        changes, _ = _diff_and_collect(incoming, kitchen_sink_manifest)

        wf_changes = [c for c in changes if c["entity_type"] == "workflows"]
        assert len(wf_changes) == 2
        # add (priority 0) before delete (priority 2)
        assert wf_changes[0]["action"] == "add"
        assert wf_changes[1]["action"] == "delete"


# =============================================================================
# Group C: Entity change hook serialization compatibility
# =============================================================================


class TestEntityHookSerialization:
    """Verify that the entity_change_hook's serialization (exclude_defaults=True)
    produces data that round-trips cleanly through YAML and diffs as equal."""

    def test_exclude_defaults_serialization_round_trip(self, kitchen_sink_manifest: Manifest):
        """Simulates entity_change_hook: serialize with exclude_defaults=True,
        write to YAML, parse back, diff against original."""
        # Simulate what entity_change_hook does for each entity type
        for wf in kitchen_sink_manifest.workflows.values():
            hook_data = wf.model_dump(mode="json", exclude_defaults=True, by_alias=True)
            # Write to YAML and parse back
            yaml_str = yaml.dump({"workflows": {wf.id: hook_data}}, sort_keys=True)
            parsed = parse_manifest_dir({"workflows.yaml": yaml_str})
            # The restored workflow should diff as equal against the original
            minimal = Manifest(workflows={wf.id: wf})
            changes, _ = _diff_and_collect(parsed, minimal)
            assert changes == [], f"Hook-serialized workflow should round-trip cleanly: {changes}"

    def test_exclude_defaults_integration_round_trip(self, kitchen_sink_manifest: Manifest):
        """Integration with OAuth and mappings round-trips through hook serialization."""
        integ = kitchen_sink_manifest.integrations[INTEG_ID]
        hook_data = integ.model_dump(mode="json", exclude_defaults=True, by_alias=True)
        yaml_str = yaml.dump({"integrations": {INTEG_ID: hook_data}}, sort_keys=True)
        parsed = parse_manifest_dir({"integrations.yaml": yaml_str})

        minimal = Manifest(integrations={INTEG_ID: integ})
        changes, _ = _diff_and_collect(parsed, minimal)
        assert changes == [], f"Hook-serialized integration should round-trip cleanly: {changes}"

    def test_exclude_defaults_config_round_trip(self, kitchen_sink_manifest: Manifest):
        """All config types round-trip through hook serialization."""
        for cfg_id, cfg in kitchen_sink_manifest.configs.items():
            hook_data = cfg.model_dump(mode="json", exclude_defaults=True, by_alias=True)
            yaml_str = yaml.dump({"configs": {cfg_id: hook_data}}, sort_keys=True)
            parsed = parse_manifest_dir({"configs.yaml": yaml_str})

            minimal = Manifest(configs={cfg_id: cfg})
            changes, _ = _diff_and_collect(parsed, minimal)
            assert changes == [], f"Hook-serialized config {cfg.key} should round-trip cleanly: {changes}"

    def test_exclude_defaults_event_round_trip(self, kitchen_sink_manifest: Manifest):
        """Event source with subscriptions round-trips through hook serialization."""
        event = kitchen_sink_manifest.events[EVENT_ID]
        hook_data = event.model_dump(mode="json", exclude_defaults=True, by_alias=True)
        yaml_str = yaml.dump({"events": {EVENT_ID: hook_data}}, sort_keys=True)
        parsed = parse_manifest_dir({"events.yaml": yaml_str})

        minimal = Manifest(events={EVENT_ID: event})
        changes, _ = _diff_and_collect(parsed, minimal)
        assert changes == [], f"Hook-serialized event should round-trip cleanly: {changes}"
