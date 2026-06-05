"""E2E tests for ``bifrost agents`` CLI commands.

Covers the CRUD surface from Task 5e of the CLI mutation surface plan:

* ``bifrost agents create --name foo --system-prompt @prompt.md`` — POSTs a
  new agent with the prompt loaded from disk.
* ``bifrost agents update <ref> --llm-model ...`` — PUTs (the audit
  correction — **not** PATCH) by UUID or name ref.
* ``bifrost agents delete <ref>`` — soft-deletes the agent; subsequent
  fetches via the admin API return 404 / mark the record inactive.

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

from bifrost.commands.agents import agents_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(agents_group, args)``."""
    return lambda args: invoke_cli(agents_group, args)


@pytest.mark.e2e
class TestCliAgents:
    """End-to-end coverage for ``bifrost agents`` commands."""

    def test_list_returns_payload(self, cli_client, _invoke) -> None:
        """``agents list --json`` returns a JSON list (possibly empty)."""
        result = _invoke(["--json", "list"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        for item in payload:
            assert "id" in item
            assert "name" in item

    def test_get_by_uuid_returns_agent(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``agents get <uuid>`` round-trips the created entity body."""
        name = f"cli-agent-get-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": name,
                "system_prompt": "You are a test agent.",
                "access_level": "authenticated",
            },
        )
        assert create_resp.status_code in (200, 201), create_resp.text
        created_id = str(create_resp.json()["id"])

        try:
            result = _invoke(["--json", "get", created_id])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert str(payload["id"]) == created_id
            assert payload["name"] == name
        finally:
            e2e_client.delete(
                f"/api/agents/{created_id}",
                headers=platform_admin.headers,
            )

    def test_create_update_delete_roundtrip(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
        tmp_path,
    ) -> None:
        """Create via @file prompt → update llm-model → delete."""
        name = f"cli-agent-{uuid4().hex[:8]}"

        # --- prompt file (multi-line to exercise the loader) ---
        prompt_path = tmp_path / "prompt.md"
        prompt_text = (
            "# System Prompt\n"
            "\n"
            "You are a test agent used by the CLI agents command suite.\n"
            "Stay terse.\n"
        )
        prompt_path.write_text(prompt_text, encoding="utf-8")

        # --- create ---
        create_result = _invoke(
            [
                "--json",
                "create",
                "--name",
                name,
                "--system-prompt",
                f"@{prompt_path}",
                "--access-level",
                "authenticated",
            ]
        )
        assert create_result.exit_code == 0, create_result.output
        created = json.loads(create_result.output)
        created_id = str(created["id"])
        assert created["name"] == name
        assert created["system_prompt"] == prompt_text
        assert created["access_level"] == "authenticated"

        # Sanity-check via REST that the agent is reachable by UUID.
        get_resp = e2e_client.get(
            f"/api/agents/{created_id}",
            headers=platform_admin.headers,
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["system_prompt"] == prompt_text

        # --- update (by name ref) — changes the llm-model ---
        new_model = "claude-3-5-sonnet-20241022"
        update_result = _invoke(
            ["--json", "update", name, "--llm-model", new_model]
        )
        assert update_result.exit_code == 0, update_result.output
        updated = json.loads(update_result.output)
        assert str(updated["id"]) == created_id
        assert updated["llm_model"] == new_model
        # The unrelated prompt should be left untouched (default-omit for unset flags).
        assert updated["system_prompt"] == prompt_text

        # --- delete (by UUID — exercise the pass-through path) ---
        delete_result = _invoke(["--json", "delete", created_id])
        assert delete_result.exit_code == 0, delete_result.output
        deleted_payload = json.loads(delete_result.output)
        assert deleted_payload["deleted"] == created_id

        # Confirm the soft-delete: admin list filters inactive agents by default.
        list_resp = e2e_client.get(
            "/api/agents", headers=platform_admin.headers
        )
        assert list_resp.status_code == 200
        active_ids = {str(a["id"]) for a in list_resp.json()}
        assert created_id not in active_ids, (
            f"Agent {created_id} should be absent from active list after delete"
        )

    def test_update_tool_ids_persists_exact_set(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
        test_tool_workflow,
    ) -> None:
        """``agents update --tool-ids`` persists the requested workflow tool IDs."""
        name = f"cli-agent-tools-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": name,
                "system_prompt": "You are a test agent.",
                "access_level": "authenticated",
            },
        )
        assert create_resp.status_code in (200, 201), create_resp.text
        created_id = str(create_resp.json()["id"])
        tool_id = str(test_tool_workflow["id"])

        try:
            update_result = _invoke(
                ["--json", "update", created_id, "--tool-ids", tool_id]
            )
            assert update_result.exit_code == 0, update_result.output
            updated = json.loads(update_result.output)
            assert str(updated["id"]) == created_id
            assert set(updated["tool_ids"]) == {tool_id}

            get_resp = e2e_client.get(
                f"/api/agents/{created_id}",
                headers=platform_admin.headers,
            )
            assert get_resp.status_code == 200, get_resp.text
            assert set(get_resp.json()["tool_ids"]) == {tool_id}
        finally:
            e2e_client.delete(
                f"/api/agents/{created_id}",
                headers=platform_admin.headers,
            )
