"""E2E tests for ``bifrost apps`` CLI commands.

Covers Task 5f of the CLI mutation surface plan:

* ``apps create foo --deps @package.json`` — two-call orchestration:
  ``POST /api/applications`` followed by ``PUT .../dependencies`` with the
  parsed dependency dict.
* ``apps create`` without ``--deps`` — single REST call, no dependencies PUT.
* ``apps update <ref>`` — patch-without-draft via
  ``PATCH /api/applications/{id}`` (slug, UUID, or name ref).
* ``apps get-dependencies <ref>`` / ``update-dependencies <ref>`` — direct
  GET/PUT to ``/dependencies`` without
  touching the app metadata.
* ``apps validate <ref>`` — compile and statically inspect current source.
* ``apps delete <ref>`` — DELETE by slug, UUID, or name ref.

The commands are invoked via :class:`click.testing.CliRunner` against the
real API stack. ``BifrostClient.get_instance`` is patched to return a client
bound to the E2E API URL with ``platform_admin``'s JWT so the CLI code path
exercised here is identical to what a real user hits.
"""

from __future__ import annotations

import json
import pathlib
from uuid import uuid4

import pytest

from bifrost.commands.apps import apps_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(apps_group, args)``."""
    return lambda args: invoke_cli(apps_group, args)


def _write_package_json(tmp_path: pathlib.Path, deps: dict[str, str]) -> pathlib.Path:
    """Write a minimal package.json with the given dependencies dict."""
    path = tmp_path / "package.json"
    path.write_text(
        json.dumps(
            {
                "name": "cli-apps-test",
                "version": "0.0.0",
                "dependencies": deps,
            }
        )
    )
    return path


@pytest.mark.e2e
class TestCliApps:
    """End-to-end coverage for ``bifrost apps`` commands."""

    def test_list_returns_payload(self, cli_client, _invoke) -> None:
        """``apps list --json`` returns the wrapped ``{applications, total}`` payload."""
        result = _invoke(["--json", "list"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, dict)
        assert "applications" in payload and "total" in payload
        for item in payload["applications"]:
            assert "id" in item

    def test_get_by_slug_returns_app(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``apps get <slug>`` round-trips the created app body."""
        slug = f"cli-app-get-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": slug, "slug": slug, "app_model": "inline_v1"},
        )
        assert create_resp.status_code == 201, create_resp.text
        app_id = create_resp.json()["id"]

        try:
            result = _invoke(["--json", "get", slug])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert str(payload["id"]) == app_id
            assert payload["slug"] == slug
        finally:
            _invoke(["--json", "delete", app_id])

    def test_create_with_deps_makes_two_rest_calls(
        self, cli_client, _invoke, e2e_client, platform_admin, tmp_path
    ) -> None:
        """``apps create --deps @package.json`` creates the app and PUTs deps.

        The command orchestrates two REST calls. Verify both landed:

        1. ``POST /api/applications`` — created app visible via GET by slug.
        2. ``PUT /api/applications/{id}/dependencies`` — deps persisted on
           the DB record (via GET /dependencies).
        """
        slug = f"cli-app-{uuid4().hex[:8]}"
        deps = {"react": "^18.2.0"}
        pkg_path = _write_package_json(tmp_path, deps)

        result = _invoke(
            [
                "--json",
                "create",
                "--name",
                slug,
                "--slug",
                slug,
                "--app-model",
                "inline_v1",
                "--deps",
                f"@{pkg_path}",
            ]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "application" in payload
        assert "dependencies" in payload
        app_id = payload["application"]["id"]
        assert payload["application"]["slug"] == slug
        assert payload["dependencies"] == deps

        # Verify the app exists via GET by slug.
        get_resp = e2e_client.get(
            f"/api/applications/{slug}", headers=platform_admin.headers
        )
        assert get_resp.status_code == 200, get_resp.text
        assert str(get_resp.json()["id"]) == app_id

        # Verify deps landed via GET /dependencies.
        deps_resp = e2e_client.get(
            f"/api/applications/{app_id}/dependencies",
            headers=platform_admin.headers,
        )
        assert deps_resp.status_code == 200, deps_resp.text
        assert deps_resp.json() == deps

        # Cleanup.
        _invoke(["--json", "delete", app_id])

    def test_create_without_deps_skips_dependencies_call(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``apps create`` without ``--deps`` makes only the POST — no deps key."""
        slug = f"cli-app-nodeps-{uuid4().hex[:8]}"

        result = _invoke(
            [
                "--json",
                "create",
                "--name",
                slug,
                "--slug",
                slug,
                "--app-model",
                "inline_v1",
            ]
        )
        assert result.exit_code == 0, result.output
        created = json.loads(result.output)
        # Single-call shape: flat application object, not the two-call wrapper.
        assert "application" not in created
        assert created["slug"] == slug
        app_id = created["id"]

        # Verify no dependencies were set.
        deps_resp = e2e_client.get(
            f"/api/applications/{app_id}/dependencies",
            headers=platform_admin.headers,
        )
        assert deps_resp.status_code == 200, deps_resp.text
        assert deps_resp.json() == {}

        # Cleanup.
        _invoke(["--json", "delete", app_id])

    def test_update_metadata_via_patch(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``apps update <ref>`` PATCHes metadata (patch-without-draft)."""
        slug = f"cli-app-upd-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": slug,
                "slug": slug,
                "app_model": "inline_v1",
                "description": "before",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        app_id = create_resp.json()["id"]

        # Update by slug ref.
        result = _invoke(
            [
                "--json",
                "update",
                slug,
                "--description",
                "after",
            ]
        )
        assert result.exit_code == 0, result.output
        updated = json.loads(result.output)
        assert str(updated["id"]) == app_id
        assert updated["description"] == "after"
        # Slug unchanged since we didn't pass --slug.
        assert updated["slug"] == slug

        # Cleanup.
        _invoke(["--json", "delete", app_id])

    def test_dependency_commands_roundtrip(
        self, cli_client, _invoke, e2e_client, platform_admin, tmp_path
    ) -> None:
        """Canonical dependency commands share the REST dependency contract."""
        slug = f"cli-app-setdeps-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": slug, "slug": slug, "app_model": "inline_v1"},
        )
        assert create_resp.status_code == 201, create_resp.text
        app_id = create_resp.json()["id"]

        deps = {"lodash": "^4.17.0"}
        pkg_path = _write_package_json(tmp_path, deps)

        result = _invoke(
            ["--json", "update-dependencies", slug, "--deps", f"@{pkg_path}"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == deps

        get_result = _invoke(["--json", "get-dependencies", app_id])
        assert get_result.exit_code == 0, get_result.output
        assert json.loads(get_result.output) == deps

        deps_resp = e2e_client.get(
            f"/api/applications/{app_id}/dependencies",
            headers=platform_admin.headers,
        )
        assert deps_resp.status_code == 200
        assert deps_resp.json() == deps

        # Cleanup.
        _invoke(["--json", "delete", app_id])

    def test_validate_and_unified_org_targeting(
        self,
        cli_client,
        _invoke,
        org1,
    ) -> None:
        """Create/update use the common --org/--global target adapter."""
        slug = f"cli-app-org-{uuid4().hex[:8]}"
        result = _invoke(
            [
                "--json",
                "create",
                "--name",
                slug,
                "--slug",
                slug,
                "--app-model",
                "inline_v1",
                "--org",
                org1["id"],
            ]
        )
        assert result.exit_code == 0, result.output
        created = json.loads(result.output)
        app_id = created["id"]
        try:
            assert created["organization_id"] == org1["id"]

            validation_result = _invoke(["--json", "validate", app_id])
            assert validation_result.exit_code == 0, validation_result.output
            validation = json.loads(validation_result.output)
            assert isinstance(validation["valid"], bool)
            assert isinstance(validation["errors"], list)
            assert isinstance(validation["warnings"], list)

            global_result = _invoke(["--json", "update", app_id, "--global"])
            assert global_result.exit_code == 0, global_result.output
            assert json.loads(global_result.output)["organization_id"] is None
        finally:
            _invoke(["--json", "delete", app_id])

    def test_delete_by_slug(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``apps delete <ref>`` removes the app; subsequent GET returns 404."""
        slug = f"cli-app-del-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": slug, "slug": slug, "app_model": "inline_v1"},
        )
        assert create_resp.status_code == 201, create_resp.text
        app_id = create_resp.json()["id"]

        result = _invoke(["--json", "delete", slug])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["deleted"] == app_id

        get_resp = e2e_client.get(
            f"/api/applications/{slug}", headers=platform_admin.headers
        )
        assert get_resp.status_code == 404
