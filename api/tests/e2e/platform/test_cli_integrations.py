"""E2E tests for ``bifrost integrations`` CLI commands.

Covers the mutation surface from Task 5g of the CLI mutation surface plan:

* ``integrations create --config-schema @schema.yaml`` — POSTs a new
  integration with a schema loaded from disk.
* ``integrations update <ref> --config-schema @schema.yaml`` — refuses when
  the new schema drops keys currently on the integration, unless
  ``--force-remove-keys`` is passed.
* ``integrations create-mapping <integration> --organization <org>`` — resolves
  both refs and POSTs to ``/api/integrations/{id}/mappings``.
* ``integrations update-mapping <integration> --organization <org>`` — looks
  up the mapping by org, then PUTs. ``oauth_token_id`` must NOT be clobbered;
  the CLI deliberately does not expose OAuth credential IDs.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
import pytest_asyncio

from bifrost.commands.integrations import integrations_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(integrations_group, args)``."""
    return lambda args: invoke_cli(integrations_group, args)


@pytest.fixture
def schema_yaml_path(tmp_path):
    """A config schema YAML file with two keys."""
    schema_file = tmp_path / "schema.yaml"
    schema_file.write_text(
        "- key: api_endpoint\n"
        "  type: string\n"
        "  required: true\n"
        "  description: API endpoint URL\n"
        "- key: timeout_seconds\n"
        "  type: int\n"
        "  required: false\n"
        "  description: Timeout in seconds\n"
    )
    return schema_file


@pytest.fixture
def schema_yaml_one_key(tmp_path):
    """A config schema YAML file with only one of the original keys.

    Used to exercise the removed-key refusal path on update.
    """
    schema_file = tmp_path / "schema_reduced.yaml"
    schema_file.write_text(
        "schema:\n"
        "  - key: api_endpoint\n"
        "    type: string\n"
        "    required: true\n"
    )
    return schema_file


@pytest.mark.e2e
class TestCliIntegrations:
    """End-to-end coverage for ``bifrost integrations`` commands."""

    def test_list_returns_payload(self, cli_client, _invoke) -> None:
        """``integrations list --json`` returns the wrapped ``{items, total}`` payload."""
        result = _invoke(["--json", "list"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, dict)
        assert "items" in payload and "total" in payload
        for item in payload["items"]:
            assert "id" in item
            assert "name" in item

    def test_get_by_uuid_returns_integration(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``integrations get <uuid>`` round-trips the created integration."""
        name = f"cli-integ-get-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        integration_id = create_resp.json()["id"]

        try:
            result = _invoke(["--json", "get", integration_id])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert str(payload["id"]) == integration_id
            assert payload["name"] == name
            # Detail payload includes mappings and config_schema keys.
            assert "mappings" in payload
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )

    def test_create_with_config_schema_file(
        self, cli_client, _invoke, e2e_client, platform_admin, schema_yaml_path
    ):
        """``integrations create --config-schema @file`` loads the YAML."""
        name = f"cli-integ-{uuid4().hex[:8]}"
        result = _invoke([
            "--json",
            "create",
            "--name", name,
            "--config-schema", f"@{schema_yaml_path}",
        ])
        assert result.exit_code == 0, result.output
        created = json.loads(result.output)
        assert created["name"] == name

        # Verify the schema round-tripped through the API.
        detail = e2e_client.get(
            f"/api/integrations/{created['id']}",
            headers=platform_admin.headers,
        )
        assert detail.status_code == 200, detail.text
        schema = detail.json().get("config_schema") or []
        assert {item["key"] for item in schema} == {"api_endpoint", "timeout_seconds"}

        # Cleanup.
        e2e_client.delete(
            f"/api/integrations/{created['id']}",
            headers=platform_admin.headers,
        )

    def test_update_refuses_removed_keys_without_force(
        self,
        cli_client,
        _invoke,
        e2e_client,
        platform_admin,
        schema_yaml_path,
        schema_yaml_one_key,
    ):
        """Removed schema keys require explicit ``--force-remove-keys``."""
        name = f"cli-integ-rm-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": name,
                "config_schema": [
                    {"key": "api_endpoint", "type": "string", "required": True},
                    {"key": "timeout_seconds", "type": "int", "required": False},
                ],
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        integration_id = create_resp.json()["id"]

        try:
            # REST is the authority: direct callers also need confirmation.
            rest_refusal = e2e_client.put(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
                json={
                    "config_schema": [
                        {
                            "key": "api_endpoint",
                            "type": "string",
                            "required": True,
                        }
                    ]
                },
            )
            assert rest_refusal.status_code == 409, rest_refusal.text
            detail = rest_refusal.json()["detail"]
            assert detail["code"] == "integration_schema_removal_requires_confirmation"
            assert detail["removed_keys"] == ["timeout_seconds"]

            # Without --force-remove-keys → refused (non-zero exit).
            refusal = _invoke([
                "--json",
                "update", name,
                "--config-schema", f"@{schema_yaml_one_key}",
            ])
            assert refusal.exit_code != 0, (
                f"expected refusal, got stdout={refusal.output} "
                f"stderr={getattr(refusal, 'stderr', '')}"
            )
            # Schema unchanged on the server since the PUT never fired.
            detail = e2e_client.get(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )
            current_keys = {i["key"] for i in (detail.json().get("config_schema") or [])}
            assert current_keys == {"api_endpoint", "timeout_seconds"}

            # With --force-remove-keys → proceeds.
            forced = _invoke([
                "--json",
                "update", name,
                "--config-schema", f"@{schema_yaml_one_key}",
                "--force-remove-keys",
            ])
            assert forced.exit_code == 0, forced.output
            updated_detail = e2e_client.get(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )
            new_keys = {i["key"] for i in (updated_detail.json().get("config_schema") or [])}
            assert new_keys == {"api_endpoint"}
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )

    def test_create_mapping_resolves_refs(
        self, cli_client, _invoke, e2e_client, platform_admin, org1
    ):
        """``create-mapping`` resolves Integration and Organization refs by name."""
        name = f"cli-integ-map-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        integration_id = create_resp.json()["id"]

        try:
            result = _invoke([
                "--json",
                "create-mapping", name,
                "--organization", org1["name"],
                "--entity-id", "tenant-abc",
                "--entity-name", "Tenant ABC",
            ])
            assert result.exit_code == 0, result.output
            mapping = json.loads(result.output)
            assert mapping["entity_id"] == "tenant-abc"
            assert mapping["entity_name"] == "Tenant ABC"
            assert mapping["organization_id"] == str(org1["id"])
            assert mapping["oauth_token_id"] is None
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )

    def test_update_mapping_preserves_oauth_token_id(
        self, cli_client, _invoke, e2e_client, platform_admin, org1, oauth_token_seed,
    ):
        """``update-mapping`` must NOT clobber ``oauth_token_id`` when unset.

        Pre-seeds a mapping with a real OAuth token, updates an unrelated
        field via CLI, and verifies the token remains attached.
        """
        integration_id = oauth_token_seed["integration_id"]
        integration_name = oauth_token_seed["integration_name"]
        oauth_token_id = oauth_token_seed["oauth_token_id"]

        # Seed mapping with the oauth_token_id.
        create_resp = e2e_client.post(
            f"/api/integrations/{integration_id}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "tenant-with-token",
                "entity_name": "Original",
                "oauth_token_id": str(oauth_token_id),
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        mapping_id = create_resp.json()["id"]
        assert create_resp.json()["oauth_token_id"] == str(oauth_token_id)

        # CLI update has no OAuth-token input surface.
        result = _invoke([
            "--json",
            "update-mapping", integration_name,
            "--organization", org1["name"],
            "--entity-name", "Renamed via CLI",
        ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["entity_name"] == "Renamed via CLI"
        # Critical: the pre-existing OAuth token must be untouched.
        assert payload["oauth_token_id"] == str(oauth_token_id), (
            "oauth_token_id was clobbered by an unrelated mapping update"
        )

        # Double-check via GET.
        detail = e2e_client.get(
            f"/api/integrations/{integration_id}/mappings/{mapping_id}",
            headers=platform_admin.headers,
        )
        assert detail.status_code == 200
        assert detail.json()["oauth_token_id"] == str(oauth_token_id)


# ---------------------------------------------------------------------------
# OAuth token seeding (DB-level because no public endpoint creates tokens).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def oauth_token_seed(e2e_client, platform_admin, db_session):
    """Create an integration + OAuthProvider + OAuthToken via direct DB ops.

    Yields the resulting IDs; cleans up the integration (cascades the rest)
    on teardown.
    """
    from uuid import UUID

    from src.models.orm import OAuthProvider
    from src.models.orm.oauth import OAuthToken

    integration_name = f"cli-integ-oauth-{uuid4().hex[:8]}"
    create_resp = e2e_client.post(
        "/api/integrations",
        headers=platform_admin.headers,
        json={"name": integration_name},
    )
    assert create_resp.status_code == 201, create_resp.text
    integration_id = UUID(create_resp.json()["id"])

    provider = OAuthProvider(
        provider_name=f"cli_test_provider_{uuid4().hex[:8]}",
        display_name="CLI Test Provider",
        oauth_flow_type="authorization_code",
        client_id="cli-test-client",
        encrypted_client_secret=b"cli-test-secret",
        authorization_url="https://example.com/authorize",
        token_url="https://example.com/token",
        scopes=["read"],
        redirect_uri="/api/oauth/callback/cli_test",
        integration_id=integration_id,
    )
    db_session.add(provider)
    await db_session.flush()

    token = OAuthToken(
        organization_id=None,
        provider_id=provider.id,
        encrypted_access_token=b"cli-test-access-token",
        scopes=["read"],
    )
    db_session.add(token)
    await db_session.commit()

    yield {
        "integration_id": str(integration_id),
        "integration_name": integration_name,
        "oauth_token_id": str(token.id),
    }

    # Cleanup: soft-delete integration (cascades mappings); OAuth provider +
    # token stay, that's acceptable since each seed uses a unique provider_name.
    e2e_client.delete(
        f"/api/integrations/{integration_id}",
        headers=platform_admin.headers,
    )
