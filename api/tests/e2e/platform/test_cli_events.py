"""E2E tests for ``bifrost events`` CLI commands.

Covers Task 5j of the CLI mutation surface plan:

* ``bifrost events create-source --source-type schedule --cron "*/5 * * * *"
  --timezone UTC`` collapses the flat schedule flags into the nested
  ``schedule`` config and POSTs to ``/api/events/sources``.
* ``bifrost events subscribe <source-ref> --workflow path::func`` resolves
  the workflow ref and POSTs to the source's subscriptions endpoint.
* ``bifrost events update-subscription <source-ref> <subscription-id>
  --event-type foo`` patches only the allowed fields.
* ``bifrost events update-subscription ... --workflow new`` is rejected with
  a "delete and recreate instead" error — the user cannot change the target.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from click.testing import CliRunner

from bifrost.commands.events import events_group
from tests.e2e.conftest import write_and_register


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(events_group, args)``."""
    return lambda args: invoke_cli(events_group, args)


@pytest.fixture
def registered_workflow(e2e_client, platform_admin):
    """Register a minimal workflow so ``--workflow path::func`` can resolve it."""
    suffix = uuid4().hex[:8]
    function_name = f"cli_events_wf_{suffix}"
    path = f"workflows/cli_events_{suffix}.py"
    content = (
        "from bifrost import workflow\n\n"
        f"@workflow\nasync def {function_name}(event: dict) -> dict:\n"
        "    return {'ok': True}\n"
    )
    wf = write_and_register(
        e2e_client,
        platform_admin.headers,
        path=path,
        content=content,
        function_name=function_name,
    )
    return {
        "id": wf["id"],
        "path": path,
        "function_name": function_name,
        "ref": f"{path}::{function_name}",
    }


@pytest.fixture
def second_registered_workflow(e2e_client, platform_admin):
    """Register a second workflow so we can try (and be rejected at) re-targeting."""
    suffix = uuid4().hex[:8]
    function_name = f"cli_events_wf2_{suffix}"
    path = f"workflows/cli_events2_{suffix}.py"
    content = (
        "from bifrost import workflow\n\n"
        f"@workflow\nasync def {function_name}(event: dict) -> dict:\n"
        "    return {'ok': True}\n"
    )
    wf = write_and_register(
        e2e_client,
        platform_admin.headers,
        path=path,
        content=content,
        function_name=function_name,
    )
    return {
        "id": wf["id"],
        "path": path,
        "function_name": function_name,
        "ref": f"{path}::{function_name}",
    }


@pytest.mark.e2e
class TestCliEvents:
    """End-to-end coverage for ``bifrost events`` commands."""

    def test_list_sources_returns_payload(self, cli_client, _invoke) -> None:
        """``events list-sources --json`` returns the wrapped ``{items, total}`` payload."""
        result = _invoke(["--json", "list-sources"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, dict)
        assert "items" in payload and "total" in payload
        for item in payload["items"]:
            assert "id" in item
            assert "name" in item

    def test_get_source_by_uuid(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``events get-source <uuid>`` returns the created source body."""
        name = f"cli-evt-get-{uuid4().hex[:8]}"
        create_result = _invoke([
            "--json",
            "create-source",
            "--name", name,
            "--source-type", "schedule",
            "--cron", "0 0 * * *",
            "--timezone", "UTC",
        ])
        assert create_result.exit_code == 0, create_result.output
        source_id = str(json.loads(create_result.output)["id"])

        try:
            result = _invoke(["--json", "get-source", source_id])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert str(payload["id"]) == source_id
            assert payload["name"] == name
        finally:
            e2e_client.delete(
                f"/api/events/sources/{source_id}",
                headers=platform_admin.headers,
            )

    def test_create_schedule_source_with_flat_flags(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
    ) -> None:
        """``events create-source --cron ... --timezone UTC`` collapses flat
        flags into the nested ``schedule`` config and creates a schedule
        source."""
        name = f"cli-evt-sched-{uuid4().hex[:8]}"
        result = _invoke([
            "--json",
            "create-source",
            "--name", name,
            "--source-type", "schedule",
            "--cron", "*/5 * * * *",
            "--timezone", "UTC",
            "--schedule-enabled",
        ])
        assert result.exit_code == 0, result.output
        created = json.loads(result.output)
        source_id = str(created["id"])
        assert created["name"] == name
        assert created["source_type"] == "schedule"
        assert created["schedule"] is not None
        assert created["schedule"]["cron_expression"] == "*/5 * * * *"
        assert created["schedule"]["timezone"] == "UTC"
        assert created["schedule"]["enabled"] is True

        # Cleanup this fixture through REST; delete-source has dedicated coverage below.
        e2e_client.delete(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
        )

    def test_provider_source_can_be_resubscribed_and_deleted(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
    ) -> None:
        name = f"cli-evt-provider-{uuid4().hex[:8]}"
        create_result = _invoke([
            "--json",
            "create-source",
            "--name", name,
            "--source-type", "webhook",
            "--adapter", "local_fixture",
            "--webhook-config", "{}",
        ])
        assert create_result.exit_code == 0, create_result.output
        source = json.loads(create_result.output)

        resubscribe_result = _invoke([
            "--json",
            "resubscribe-source", source["id"],
        ])
        assert resubscribe_result.exit_code == 0, resubscribe_result.output
        resubscribed = json.loads(resubscribe_result.output)
        assert resubscribed["webhook"]["external_id"] == "local-scheduler-fixture"

        delete_result = _invoke(["--json", "delete-source", source["id"]])
        assert delete_result.exit_code == 0, delete_result.output
        assert json.loads(delete_result.output) == {"deleted": source["id"]}
        missing = e2e_client.get(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )
        assert missing.status_code == 404

    def test_graph_create_help_exposes_first_class_provider_flags(self, _invoke) -> None:
        result = _invoke(["create-graph-source", "--help"])
        assert result.exit_code == 0, result.output
        assert "--integration" in result.output
        assert "--user-id" in result.output
        assert "--resource" in result.output
        assert "--change-type" in result.output

    def test_subscribe_workflow_and_update_event_type(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
        registered_workflow,
    ) -> None:
        """``events subscribe`` creates a subscription; update-subscription
        patches ``--event-type``."""
        # --- Create a schedule source to subscribe to ---
        source_name = f"cli-evt-src-{uuid4().hex[:8]}"
        create_result = _invoke([
            "--json",
            "create-source",
            "--name", source_name,
            "--source-type", "schedule",
            "--cron", "0 9 * * *",
            "--timezone", "UTC",
        ])
        assert create_result.exit_code == 0, create_result.output
        source_id = str(json.loads(create_result.output)["id"])

        # --- Subscribe the workflow ---
        sub_result = _invoke([
            "--json",
            "subscribe", source_name,
            "--workflow", registered_workflow["ref"],
            "--event-type", "daily.report",
        ])
        assert sub_result.exit_code == 0, sub_result.output
        subscription = json.loads(sub_result.output)
        subscription_id = str(subscription["id"])
        assert subscription["target_type"] == "workflow"
        assert str(subscription["workflow_id"]) == str(registered_workflow["id"])
        assert subscription["event_type"] == "daily.report"

        # --- Update the subscription's event_type (allowed) ---
        update_result = _invoke([
            "--json",
            "update-subscription", source_name, subscription_id,
            "--event-type", "daily.rollup",
        ])
        assert update_result.exit_code == 0, update_result.output
        updated = json.loads(update_result.output)
        assert updated["event_type"] == "daily.rollup"
        # Target is untouched.
        assert str(updated["workflow_id"]) == str(registered_workflow["id"])

        # --- Cleanup ---
        e2e_client.delete(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
        )

    def test_update_subscription_rejects_workflow_change(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
        registered_workflow,
        second_registered_workflow,
    ) -> None:
        """``update-subscription --workflow new`` is refused with a clear
        error directing the user to delete and recreate."""
        # Seed a source + subscription.
        source_name = f"cli-evt-reject-{uuid4().hex[:8]}"
        create_result = _invoke([
            "--json",
            "create-source",
            "--name", source_name,
            "--source-type", "schedule",
            "--cron", "0 0 * * *",
            "--timezone", "UTC",
        ])
        assert create_result.exit_code == 0, create_result.output
        source_id = str(json.loads(create_result.output)["id"])

        sub_result = _invoke([
            "--json",
            "subscribe", source_name,
            "--workflow", registered_workflow["ref"],
        ])
        assert sub_result.exit_code == 0, sub_result.output
        subscription_id = str(json.loads(sub_result.output)["id"])

        # Attempt to change the workflow target — must be rejected.
        runner = CliRunner()
        reject_result = runner.invoke(
            events_group,
            [
                "--json",
                "update-subscription", source_name, subscription_id,
                "--workflow", second_registered_workflow["ref"],
            ],
            standalone_mode=False,
            # Let UsageError surface so we can inspect it rather than being
            # wrapped by ``run_async``'s ``sys.exit``.
            catch_exceptions=True,
        )
        assert reject_result.exit_code != 0
        # Error message points the user to delete + recreate.
        output_blob = (reject_result.output or "") + str(reject_result.exception or "")
        assert "Delete the subscription and create a new one" in output_blob, (
            f"expected delete+recreate error, got: {output_blob!r}"
        )

        # Confirm the subscription's target was NOT changed.
        list_resp = e2e_client.get(
            f"/api/events/sources/{source_id}/subscriptions",
            headers=platform_admin.headers,
        )
        assert list_resp.status_code == 200, list_resp.text
        items = list_resp.json().get("items", [])
        matching = [s for s in items if str(s["id"]) == subscription_id]
        assert matching, "subscription vanished after rejection"
        assert str(matching[0]["workflow_id"]) == str(registered_workflow["id"])

        # --- Cleanup ---
        e2e_client.delete(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
        )
