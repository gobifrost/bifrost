"""E2E tests for the CLI policy-rule group + tables policies get/set.

Tests exercise the REST API surface that the CLI wraps:

* policy-rule create / list / get / usages / delete (domain=file)
* tables policies set round-trips a $ref to a named policy rule
* tables policies get returns the policies field
"""

import json
import pytest

pytestmark = pytest.mark.e2e


class TestCliPolicyRuleGroup:
    """REST-level verification of the endpoints the CLI policy-rule group calls."""

    def test_create_list_get_usages_delete_file_rule(self, e2e_client, platform_admin):
        """Full CRUD round-trip: create → list → usages → delete."""
        # Create a file-domain rule
        create_resp = e2e_client.post(
            "/api/policy-rules",
            headers=platform_admin.headers,
            json={
                "name": "cli_e2e_file_rule",
                "domain": "file",
                "body": {"actions": ["read"], "when": None},
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        rule = create_resp.json()
        assert rule["name"] == "cli_e2e_file_rule"
        assert rule["domain"] == "file"
        assert rule["is_builtin"] is False

        # List: rule appears in list with domain filter
        list_resp = e2e_client.get(
            "/api/policy-rules",
            headers=platform_admin.headers,
            params={"domain": "file"},
        )
        assert list_resp.status_code == 200, list_resp.text
        names = [r["name"] for r in list_resp.json()]
        assert "cli_e2e_file_rule" in names

        # Get: find in list (CLI implements get as list+filter)
        all_items = list_resp.json()
        match = next((r for r in all_items if r["name"] == "cli_e2e_file_rule"), None)
        assert match is not None
        assert match["domain"] == "file"

        # Usages: freshly created rule has no usages
        usages_resp = e2e_client.get(
            "/api/policy-rules/file/cli_e2e_file_rule/usages",
            headers=platform_admin.headers,
        )
        assert usages_resp.status_code == 200, usages_resp.text
        assert usages_resp.json()["total"] == 0

        # Delete
        del_resp = e2e_client.delete(
            "/api/policy-rules/file/cli_e2e_file_rule",
            headers=platform_admin.headers,
        )
        assert del_resp.status_code == 204, del_resp.text

        # Confirm gone (usages 404s after delete)
        gone_resp = e2e_client.get(
            "/api/policy-rules/file/cli_e2e_file_rule/usages",
            headers=platform_admin.headers,
        )
        assert gone_resp.status_code == 404, gone_resp.text

    def test_update_policy_rule(self, e2e_client, platform_admin):
        """Update endpoint changes the description."""
        e2e_client.post(
            "/api/policy-rules",
            headers=platform_admin.headers,
            json={
                "name": "cli_e2e_update_rule",
                "domain": "table",
                "body": {"actions": ["read"], "when": None},
            },
        )
        update_resp = e2e_client.put(
            "/api/policy-rules/table/cli_e2e_update_rule",
            headers=platform_admin.headers,
            json={"description": "updated via e2e"},
        )
        assert update_resp.status_code == 200, update_resp.text
        assert update_resp.json()["description"] == "updated via e2e"

        # Cleanup
        e2e_client.delete(
            "/api/policy-rules/table/cli_e2e_update_rule",
            headers=platform_admin.headers,
        )


class TestTablesPolicisCLI:
    """REST-level verification of the endpoints the CLI tables policies subgroup calls."""

    def _create_table(self, e2e_client, platform_admin, name: str) -> dict:
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={"name": name, "schema": {}},
        )
        assert resp.status_code in (200, 201), f"create table failed: {resp.text}"
        return resp.json()

    def _delete_table(self, e2e_client, platform_admin, table_id: str) -> None:
        e2e_client.delete(f"/api/tables/{table_id}", headers=platform_admin.headers)

    def test_tables_policies_get_returns_policies_field(self, e2e_client, platform_admin):
        """GET /api/tables/{id} exposes the policies field the CLI surfaces."""
        table = self._create_table(e2e_client, platform_admin, "cli_e2e_tbl_get_pol")
        tid = table["id"]
        try:
            resp = e2e_client.get(f"/api/tables/{tid}", headers=platform_admin.headers)
            assert resp.status_code == 200, resp.text
            # policies field exists on the table record
            assert "policies" in resp.json()
        finally:
            self._delete_table(e2e_client, platform_admin, tid)

    def test_tables_policies_set_round_trips_ref(self, e2e_client, platform_admin):
        """PATCH /api/tables/{id} with a $ref entry and read it back."""
        # Create a table-domain rule to reference
        rule_name = "cli_e2e_tbl_ref_rule"
        e2e_client.post(
            "/api/policy-rules",
            headers=platform_admin.headers,
            json={
                "name": rule_name,
                "domain": "table",
                "body": {"actions": ["read"], "when": None},
            },
        )

        table = self._create_table(e2e_client, platform_admin, "cli_e2e_tbl_set_pol")
        tid = table["id"]
        try:
            # Set policies to include a $ref to our named rule.
            # TableUpdate.policies is of type TablePolicies (a wrapper model with
            # a ``policies`` list), so the wire shape is {"policies": {"policies": [...]}}.
            patch_resp = e2e_client.patch(
                f"/api/tables/{tid}",
                headers=platform_admin.headers,
                json={"policies": {"policies": [{"$ref": rule_name}]}},
            )
            assert patch_resp.status_code == 200, patch_resp.text

            # Read back — the $ref must round-trip unchanged
            get_resp = e2e_client.get(f"/api/tables/{tid}", headers=platform_admin.headers)
            assert get_resp.status_code == 200, get_resp.text
            stored = get_resp.json().get("policies")
            assert stored is not None
            # The stored policies should contain the $ref
            stored_str = json.dumps(stored)
            assert rule_name in stored_str

        finally:
            self._delete_table(e2e_client, platform_admin, tid)
            # Delete rule (may be in-use, delete table first → no longer in use)
            e2e_client.delete(
                f"/api/policy-rules/table/{rule_name}",
                headers=platform_admin.headers,
            )

    def test_tables_policies_set_plain_inline_policy(self, e2e_client, platform_admin):
        """PATCH tables with a plain inline policy list (no $ref)."""
        table = self._create_table(e2e_client, platform_admin, "cli_e2e_tbl_inline_pol")
        tid = table["id"]
        try:
            # TableUpdate.policies is TablePolicies — wrapper with ``policies`` list.
            inline_policies = {"policies": [{"name": "read_only", "actions": ["read"], "when": None}]}
            patch_resp = e2e_client.patch(
                f"/api/tables/{tid}",
                headers=platform_admin.headers,
                json={"policies": inline_policies},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            stored = patch_resp.json().get("policies")
            assert stored is not None
            # The inline policy should be stored
            assert isinstance(stored, (list, dict))
        finally:
            self._delete_table(e2e_client, platform_admin, tid)
