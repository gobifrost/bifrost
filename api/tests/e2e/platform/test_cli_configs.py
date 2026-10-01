"""E2E tests for ``bifrost configs`` CLI commands.

Covers the CRUD surface from Task 5h of the CLI mutation surface plan:

* ``bifrost configs list`` — returns configs for the current scope.
* ``bifrost configs set <key> --value X`` — upsert wrapper. Creates the row
  on the first call, updates in place on subsequent calls with the same
  ``(key, organization_id)`` tuple.
* ``bifrost configs update <ref>`` — PUTs by UUID or key ref. Omitting
  ``--value`` preserves the stored value (server-side omit-unset).
* ``bifrost configs delete <ref>`` — DELETE by UUID or key ref. Refuses to
  delete a ``secret``-type config without ``--confirm``.

The commands are invoked via :class:`click.testing.CliRunner` against the
real API stack. ``BifrostClient.get_instance`` is patched via the thread-
local to return a client bound to the E2E API URL with ``platform_admin``'s
JWT so the CLI code path exercised here is identical to what a real user
hits.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from bifrost.commands.configs import configs_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(configs_group, args)``."""
    return lambda args: invoke_cli(configs_group, args)


def _cleanup_config(e2e_client, platform_admin, config_id: str) -> None:
    """Best-effort DELETE of a config row by UUID."""
    e2e_client.delete(
        f"/api/config/{config_id}", headers=platform_admin.headers
    )


@pytest.mark.e2e
class TestCliConfigs:
    """End-to-end coverage for ``bifrost configs`` commands."""

    def test_get_by_uuid_returns_config(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``configs get <uuid>`` resolves and returns the matching config row.

        The API has no per-id GET endpoint; the CLI implements ``get`` via
        list-and-filter through :class:`RefResolver`.
        """
        key = f"cli_cfg_get_{uuid4().hex[:8]}"
        create_result = _invoke(["--json", "set", key, "--value", "bar"])
        assert create_result.exit_code == 0, create_result.output
        config_id = str(json.loads(create_result.output)["id"])

        try:
            result = _invoke(["--json", "get", config_id])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert str(payload["id"]) == config_id
            assert payload["key"] == key
            assert payload["value"] == "bar"
        finally:
            _cleanup_config(e2e_client, platform_admin, config_id)

    def test_set_creates_then_updates(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``configs set`` acts as an upsert: first call POSTs, second PUTs."""
        key = f"cli_cfg_{uuid4().hex[:8]}"

        # --- first call: creates ---
        create_result = _invoke(["--json", "set", key, "--value", "bar"])
        assert create_result.exit_code == 0, create_result.output
        created = json.loads(create_result.output)
        config_id = str(created["id"])
        assert created["key"] == key
        assert created["value"] == "bar"
        assert created["type"] == "string"

        try:
            # --- second call with same key: updates the same row ---
            update_result = _invoke(["--json", "set", key, "--value", "baz"])
            assert update_result.exit_code == 0, update_result.output
            updated = json.loads(update_result.output)
            assert str(updated["id"]) == config_id, (
                "set should upsert — a second call must target the same row"
            )
            assert updated["value"] == "baz"
        finally:
            _cleanup_config(e2e_client, platform_admin, config_id)

    def test_set_secret_prints_redacted_value(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``configs set --type secret`` echoes ``[SECRET]`` on create and update."""
        key = f"cli_secret_echo_{uuid4().hex[:8]}"
        plaintext = f"cli-plain-{uuid4().hex}"
        rotated = f"cli-rotated-{uuid4().hex}"

        create_result = _invoke(
            ["--json", "set", key, "--value", plaintext, "--type", "secret", "--global"]
        )
        assert create_result.exit_code == 0, create_result.output
        created = json.loads(create_result.output)
        config_id = str(created["id"])

        try:
            assert plaintext not in create_result.output
            assert created["value"] == "[SECRET]"

            update_result = _invoke(["--json", "set", key, "--value", rotated, "--global"])
            assert update_result.exit_code == 0, update_result.output
            assert rotated not in update_result.output
            assert json.loads(update_result.output)["value"] == "[SECRET]"
        finally:
            _cleanup_config(e2e_client, platform_admin, config_id)

    def test_delete_secret_requires_confirm(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """Deleting a secret-type config without ``--confirm`` refuses."""
        key = f"cli_secret_{uuid4().hex[:8]}"

        # Seed a secret-type config directly via REST so we don't rely on the
        # CLI create path to get a secret into the DB.
        create_resp = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={"key": key, "value": "supersecret", "type": "secret"},
        )
        assert create_resp.status_code == 201, create_resp.text
        config_id = str(create_resp.json()["id"])

        try:
            # Without --confirm: must refuse, exit non-zero, config remains.
            bad_result = _invoke(["--json", "delete", key])
            assert bad_result.exit_code != 0, (
                f"delete of a secret without --confirm should fail; got "
                f"exit_code={bad_result.exit_code}, output={bad_result.output!r}"
            )

            still_there = e2e_client.get(
                "/api/config", headers=platform_admin.headers
            )
            assert still_there.status_code == 200
            keys = {c["key"] for c in still_there.json()}
            assert key in keys, "secret config must still exist after refused delete"

            # With --confirm: succeeds.
            ok_result = _invoke(["--json", "delete", key, "--confirm"])
            assert ok_result.exit_code == 0, ok_result.output

            gone = e2e_client.get(
                "/api/config", headers=platform_admin.headers
            )
            assert gone.status_code == 200
            keys_after = {c["key"] for c in gone.json()}
            assert key not in keys_after, (
                "secret config should be removed after confirmed delete"
            )
        finally:
            # Belt-and-suspenders: ensure cleanup even if assertions above
            # short-circuited.
            _cleanup_config(e2e_client, platform_admin, config_id)

    def test_update_without_value_preserves_existing(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``configs update --description X`` must NOT clear the stored value."""
        key = f"cli_keep_{uuid4().hex[:8]}"

        # Seed via REST so the starting state is explicit.
        create_resp = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={
                "key": key,
                "value": "original-value",
                "type": "string",
                "description": "initial",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        config_id = str(create_resp.json()["id"])

        try:
            # Update description only — do NOT pass --value.
            result = _invoke(
                [
                    "--json",
                    "update",
                    key,
                    "--description",
                    "changed description",
                ]
            )
            assert result.exit_code == 0, result.output
            updated = json.loads(result.output)
            assert str(updated["id"]) == config_id
            assert updated["description"] == "changed description"
            assert updated["value"] == "original-value", (
                "omitting --value must preserve the existing value — server "
                "uses model_fields_set to distinguish unset from empty"
            )
        finally:
            _cleanup_config(e2e_client, platform_admin, config_id)
