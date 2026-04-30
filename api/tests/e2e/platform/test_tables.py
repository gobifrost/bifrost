"""E2E tests for the public tables PATCH endpoint.

Covers the TableUpdate DTO's ability to rename tables and reassign the owning
application, plus validation for bogus application references.
Also covers the default-deny behaviour for non-admin users.
"""

from uuid import uuid4

import pytest


def _create_table(e2e_client, headers, name: str) -> str:
    """Create a table via POST /api/tables and return its UUID."""
    resp = e2e_client.post(
        "/api/tables",
        headers=headers,
        json={"name": name, "description": "original"},
    )
    assert resp.status_code == 201, f"Create table '{name}' failed: {resp.text}"
    return resp.json()["id"]


def _create_app(e2e_client, headers, slug: str) -> str:
    """Create an app via POST /api/applications and return its UUID."""
    resp = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={"name": slug, "slug": slug},
    )
    assert resp.status_code == 201, f"Create app '{slug}' failed: {resp.text}"
    return resp.json()["id"]


@pytest.mark.e2e
class TestTableUpdatePublic:
    """TableUpdate rename and application reassignment via PATCH /api/tables/{id}."""

    def test_rename_table_via_patch(self, e2e_client, platform_admin):
        """PATCH with `name` updates the table name; GET reflects the new name."""
        original = f"rn_orig_{uuid4().hex[:8]}"
        new_name = f"rn_new_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, original)

        resp = e2e_client.patch(
            f"/api/tables/{table_id}",
            headers=platform_admin.headers,
            json={"name": new_name},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == new_name

        get_resp = e2e_client.get(
            f"/api/tables/{table_id}", headers=platform_admin.headers
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == new_name

    def test_reassign_application_id(self, e2e_client, platform_admin):
        """PATCH with `application_id` updates the app linkage; GET reflects it."""
        table_name = f"reasg_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        app1_id = _create_app(e2e_client, platform_admin.headers, f"reasg-app1-{uuid4().hex[:8]}")
        app2_id = _create_app(e2e_client, platform_admin.headers, f"reasg-app2-{uuid4().hex[:8]}")

        # Initial assignment
        resp = e2e_client.patch(
            f"/api/tables/{table_id}",
            headers=platform_admin.headers,
            json={"application_id": app1_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["application_id"] == app1_id

        get1 = e2e_client.get(f"/api/tables/{table_id}", headers=platform_admin.headers)
        assert get1.status_code == 200
        assert get1.json()["application_id"] == app1_id

        # Reassign to a different app
        resp = e2e_client.patch(
            f"/api/tables/{table_id}",
            headers=platform_admin.headers,
            json={"application_id": app2_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["application_id"] == app2_id

        get2 = e2e_client.get(f"/api/tables/{table_id}", headers=platform_admin.headers)
        assert get2.status_code == 200
        assert get2.json()["application_id"] == app2_id

    def test_invalid_application_id_returns_422(self, e2e_client, platform_admin):
        """PATCH with a non-existent `application_id` returns 422."""
        table_name = f"bad_app_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        fake_app_id = str(uuid4())
        resp = e2e_client.patch(
            f"/api/tables/{table_id}",
            headers=platform_admin.headers,
            json={"application_id": fake_app_id},
        )
        assert resp.status_code == 422, resp.text
        assert "not found" in resp.json()["detail"].lower()


@pytest.mark.e2e
class TestTableDefaultDeny:
    """Non-admin users are denied by default when no access block is set."""

    def test_default_deny_non_superuser(self, e2e_client, platform_admin, non_admin_user):
        """A table with no access block denies inserts and queries for non-admin users."""
        table_name = f"default_deny_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        # Non-admin insert → 403
        insert_resp = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=non_admin_user.headers,
            json={"data": {"key": "value"}},
        )
        assert insert_resp.status_code == 403, insert_resp.text

        # Non-admin query → 403
        query_resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=non_admin_user.headers,
            json={},
        )
        assert query_resp.status_code == 403, query_resp.text
