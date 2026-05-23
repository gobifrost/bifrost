"""E2E tests for ``bifrost claims`` CLI commands."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from bifrost.commands.claims import claims_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(claims_group, args)``."""
    return lambda args: invoke_cli(claims_group, args)


@pytest.mark.e2e
class TestCliClaims:
    """End-to-end coverage for ``bifrost claims`` commands."""

    def test_create_get_update_delete_roundtrip(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
    ) -> None:
        source_table = f"cli_claim_memberships_{uuid4().hex[:8]}"
        claim_name = f"allowed_campus_ids_{uuid4().hex[:8]}"

        table_resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={"name": source_table, "description": "claim source table"},
        )
        assert table_resp.status_code == 201, table_resp.text

        query = {
            "table": source_table,
            "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
            "select": "campus_id",
        }

        create_result = _invoke(
            [
                "--json",
                "create",
                "--name",
                claim_name,
                "--description",
                "created by test_cli_claims",
                "--type",
                "list",
                "--query",
                json.dumps(query),
            ]
        )
        assert create_result.exit_code == 0, create_result.output
        created = json.loads(create_result.output)
        assert created["name"] == claim_name
        assert created["query"] == query

        get_result = _invoke(["--json", "get", claim_name])
        assert get_result.exit_code == 0, get_result.output
        fetched = json.loads(get_result.output)
        assert fetched["id"] == created["id"]

        update_result = _invoke(
            [
                "--json",
                "update",
                claim_name,
                "--description",
                "updated by test_cli_claims",
            ]
        )
        assert update_result.exit_code == 0, update_result.output
        updated = json.loads(update_result.output)
        assert updated["description"] == "updated by test_cli_claims"

        list_result = _invoke(["--json", "list"])
        assert list_result.exit_code == 0, list_result.output
        listed = json.loads(list_result.output)
        assert any(item["name"] == claim_name for item in listed["claims"])

        delete_result = _invoke(["--json", "delete", claim_name])
        assert delete_result.exit_code == 0, delete_result.output
        assert json.loads(delete_result.output)["deleted"] == claim_name

        get_after = e2e_client.get(
            f"/api/claims/{claim_name}",
            headers=platform_admin.headers,
        )
        assert get_after.status_code == 404, get_after.text
