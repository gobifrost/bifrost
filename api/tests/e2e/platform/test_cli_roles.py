"""E2E tests for ``bifrost roles`` CLI commands.

Covers the CRUD surface from Task 5b of the CLI mutation surface plan:

* ``bifrost roles list`` — returns the seeded / created role set.
* ``bifrost roles create --name foo [--capabilities <key>]`` — POSTs a new
  role and returns the created entity.
* ``bifrost roles update <ref> --name bar`` — PATCHes by UUID or name ref.
* ``bifrost roles delete <ref>`` — deletes the role; CASCADE removes all
  assignments.

``capabilities`` is the only Role authorization vocabulary. The generated
``--capabilities`` flag is repeatable and round-trips directly to the API.

The commands are invoked via :class:`click.testing.CliRunner` against the
real API stack. ``BifrostClient.get_instance`` is patched to return a client
bound to the E2E API URL with ``platform_admin``'s JWT so the CLI code path
exercised here is identical to what a real user hits.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from bifrost.commands.roles import roles_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(roles_group, args)``."""
    return lambda args: invoke_cli(roles_group, args)


@pytest.mark.e2e
class TestCliRoles:
    """End-to-end coverage for ``bifrost roles`` commands."""

    def test_list_returns_payload(self, cli_client, _invoke) -> None:
        """``roles list --json`` returns the (possibly empty) role set as JSON."""
        result = _invoke(["--json", "list"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        # Every item has an id, a name, and canonical capability keys.
        for item in payload:
            assert "id" in item
            assert "name" in item
            assert "capabilities" in item
            assert isinstance(item["capabilities"], list)

    def test_get_by_uuid_returns_role(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``roles get <uuid>`` round-trips the created role body."""
        name = f"cli-role-get-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": name, "capabilities": ["workflows.read"]},
        )
        assert create_resp.status_code == 201, create_resp.text
        role_id = create_resp.json()["id"]

        try:
            result = _invoke(["--json", "get", str(role_id)])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert str(payload["id"]) == str(role_id)
            assert payload["name"] == name
        finally:
            e2e_client.delete(
                f"/api/roles/{role_id}", headers=platform_admin.headers
            )

    def test_create_update_delete_roundtrip(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """Full CRUD cycle: create → update → delete by name ref.

        Also verifies repeated ``--capabilities`` flags round-trip through the
        generated DTO surface.
        """
        original_name = f"cli-role-{uuid4().hex[:8]}"
        renamed = f"cli-role-renamed-{uuid4().hex[:8]}"

        capabilities = ["workflows.read", "workflows.execute"]

        # --- create ---
        create_result = _invoke(
            [
                "--json",
                "create",
                "--name",
                original_name,
                "--description",
                "created by test_cli_roles",
                "--capabilities",
                capabilities[0],
                "--capabilities",
                capabilities[1],
            ]
        )
        assert create_result.exit_code == 0, create_result.output
        created = json.loads(create_result.output)
        created_id = str(created["id"])
        assert created["name"] == original_name
        assert created["description"] == "created by test_cli_roles"
        assert created["capabilities"] == capabilities

        # Sanity-check via the REST API that the role is reachable by UUID.
        get_resp = e2e_client.get(
            f"/api/roles/{created_id}",
            headers=platform_admin.headers,
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["name"] == original_name

        # --- update (by name ref) ---
        new_capabilities = ["workflows.readwrite"]
        update_result = _invoke(
            [
                "--json",
                "update",
                original_name,
                "--name",
                renamed,
                "--capabilities",
                new_capabilities[0],
            ]
        )
        assert update_result.exit_code == 0, update_result.output
        updated = json.loads(update_result.output)
        assert str(updated["id"]) == created_id
        assert updated["name"] == renamed
        assert updated["capabilities"] == new_capabilities

        # --- delete (by renamed ref) ---
        delete_result = _invoke(["--json", "delete", renamed])
        assert delete_result.exit_code == 0, delete_result.output
        deleted_payload = json.loads(delete_result.output)
        assert deleted_payload["deleted"] == created_id

        # Confirm the delete cascaded through to the API.
        get_after = e2e_client.get(
            f"/api/roles/{created_id}", headers=platform_admin.headers
        )
        assert get_after.status_code == 404, get_after.text

    def test_capabilities_flag_is_repeatable(self, cli_client, _invoke) -> None:
        """The generated capability flag collects every repeated key."""
        name = f"cli-role-capabilities-{uuid4().hex[:8]}"
        result = _invoke(
            [
                "--json",
                "create",
                "--name",
                name,
                "--capabilities",
                "agents.read",
                "--capabilities",
                "forms.read",
            ]
        )
        try:
            assert result.exit_code == 0, result.output
            created = json.loads(result.output)
            assert created["capabilities"] == ["agents.read", "forms.read"]
        finally:
            # Cleanup: best-effort delete.
            _invoke(["--json", "delete", name])

    def test_update_by_uuid(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """Update accepts a UUID ref directly (ref resolver pass-through)."""
        name = f"cli-role-uuid-{uuid4().hex[:8]}"
        renamed = f"cli-role-uuid-new-{uuid4().hex[:8]}"

        create_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        role_id = create_resp.json()["id"]

        update_result = _invoke(
            ["--json", "update", str(role_id), "--name", renamed]
        )
        assert update_result.exit_code == 0, update_result.output
        payload = json.loads(update_result.output)
        assert payload["name"] == renamed

        # Cleanup to keep fixtures clean across the session.
        _invoke(["--json", "delete", str(role_id)])
