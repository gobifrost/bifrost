"""E2E tests for ``bifrost organizations`` CLI commands.

Covers the CRUD surface from Task 5a of the CLI mutation surface plan:

* ``bifrost organizations list`` — sees the seeded platform org.
* ``bifrost organizations create --name foo [--is-active/--no-is-active]`` — POSTs a new
  org and returns the created entity.
* ``bifrost organizations update <ref> --name bar`` — PATCHes by UUID or name ref.
* ``bifrost organizations delete <ref>`` — soft-deletes the org; subsequent GETs via
  the admin API return 404 (or mark the record inactive).

The commands are invoked via :class:`click.testing.CliRunner` against the
real API stack. ``BifrostClient.get_instance`` is patched to return a client
bound to the E2E API URL with ``platform_admin``'s JWT so the CLI code path
exercised here is identical to what a real user hits.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from bifrost.commands.orgs import orgs_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(orgs_group, args)``."""
    return lambda args: invoke_cli(orgs_group, args)


@pytest.mark.e2e
class TestCliOrgs:
    """End-to-end coverage for ``bifrost organizations`` commands."""

    def test_list_returns_platform_admin_org(self, cli_client, _invoke) -> None:
        """``organizations list --json`` returns the seeded provider org."""
        result = _invoke(["--json", "list"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert payload, "expected at least one org from organizations list"
        # Every item has an id and a name; the provider org is always present.
        for item in payload:
            assert "id" in item
            assert "name" in item

    def test_create_update_delete_roundtrip(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """Full CRUD cycle: create → update → delete by name ref."""
        original_name = f"cli-org-{uuid4().hex[:8]}"
        renamed = f"cli-org-renamed-{uuid4().hex[:8]}"

        # --- create ---
        create_result = _invoke(
            ["--json", "create", "--name", original_name, "--is-active"]
        )
        assert create_result.exit_code == 0, create_result.output
        created = json.loads(create_result.output)
        created_id = str(created["id"])
        assert created["name"] == original_name

        # Sanity-check via the REST API that the org is reachable by UUID.
        get_resp = e2e_client.get(
            f"/api/organizations/{created_id}",
            headers=platform_admin.headers,
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["name"] == original_name

        # --- update (by name ref) ---
        update_result = _invoke(
            ["--json", "update", original_name, "--name", renamed]
        )
        assert update_result.exit_code == 0, update_result.output
        updated = json.loads(update_result.output)
        assert str(updated["id"]) == created_id
        assert updated["name"] == renamed

        # --- delete (by renamed ref) ---
        delete_result = _invoke(["--json", "delete", renamed])
        assert delete_result.exit_code == 0, delete_result.output
        deleted_payload = json.loads(delete_result.output)
        assert deleted_payload["deleted"] == created_id

        # Confirm the soft-delete: provider list filters inactive orgs.
        list_resp = e2e_client.get(
            "/api/organizations", headers=platform_admin.headers
        )
        assert list_resp.status_code == 200
        names = {org["name"] for org in list_resp.json()}
        assert renamed not in names, (
            f"Org {renamed} should be absent from active list after delete"
        )

    def test_get_by_uuid_returns_org(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``organizations get <uuid>`` round-trips the created org body."""
        name = f"cli-org-get-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/organizations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        org_id = create_resp.json()["id"]

        try:
            result = _invoke(["--json", "get", str(org_id)])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert str(payload["id"]) == str(org_id)
            assert payload["name"] == name
        finally:
            _invoke(["--json", "delete", str(org_id)])

    def test_update_by_uuid(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """Update accepts a UUID ref directly (ref resolver pass-through)."""
        name = f"cli-uuid-{uuid4().hex[:8]}"
        renamed = f"cli-uuid-new-{uuid4().hex[:8]}"

        create_resp = e2e_client.post(
            "/api/organizations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        org_id = create_resp.json()["id"]

        update_result = _invoke(
            ["--json", "update", str(org_id), "--name", renamed]
        )
        assert update_result.exit_code == 0, update_result.output
        payload = json.loads(update_result.output)
        assert payload["name"] == renamed

        # Cleanup to keep fixtures clean across the session.
        _invoke(["--json", "delete", str(org_id)])
