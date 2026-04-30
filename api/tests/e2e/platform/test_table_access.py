"""E2E tests for table access control rules.

Covers the full access matrix:
- Admin bypass (superuser always allowed)
- everyone.read grants anonymous read but not write
- Role-based grants
- Creator-only read (per-row filtering)
"""

import uuid

import pytest


def _set_access(e2e_client, headers, table_id, access):
    r = e2e_client.patch(
        f"/api/tables/{table_id}", headers=headers, json={"access": access}
    )
    assert r.status_code == 200, r.text


def _create_table(e2e_client, headers, name: str) -> str:
    resp = e2e_client.post(
        "/api/tables",
        headers=headers,
        json={"name": name, "description": "access test table"},
    )
    assert resp.status_code == 201, f"Create table '{name}' failed: {resp.text}"
    return resp.json()["id"]


def _insert_doc(e2e_client, headers, table_id, data: dict) -> dict:
    resp = e2e_client.post(
        f"/api/tables/{table_id}/documents",
        headers=headers,
        json={"data": data},
    )
    return resp


def _query_docs(e2e_client, headers, table_id) -> dict:
    resp = e2e_client.post(
        f"/api/tables/{table_id}/documents/query",
        headers=headers,
        json={},
    )
    return resp


@pytest.mark.e2e
class TestTableAccessMatrix:
    """Full access matrix tests for table document endpoints."""

    def test_admin_bypass(self, e2e_client, platform_admin):
        """Superuser can insert and query a table with no access block."""
        table_name = f"admin_bypass_{uuid.uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        insert = _insert_doc(
            e2e_client, platform_admin.headers, table_id, {"x": 1}
        )
        assert insert.status_code == 201, insert.text

        query = _query_docs(e2e_client, platform_admin.headers, table_id)
        assert query.status_code == 200, query.text
        assert query.json()["total"] == 1

    def test_everyone_read_only(self, e2e_client, platform_admin, non_admin_user):
        """Table with everyone.read=true: non-admin can GET but not POST."""
        table_name = f"everyone_ro_{uuid.uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        _set_access(
            e2e_client,
            platform_admin.headers,
            table_id,
            {
                "everyone": {"read": True, "create": False, "update": False, "delete": False},
                "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
                "creator": {"read": False, "create": False, "update": False, "delete": False},
            },
        )

        # Admin pre-seeds a row so there is something to read
        seed = _insert_doc(e2e_client, platform_admin.headers, table_id, {"seed": True})
        assert seed.status_code == 201, seed.text

        # Non-admin can query
        query = _query_docs(e2e_client, non_admin_user.headers, table_id)
        assert query.status_code == 200, query.text

        # Non-admin cannot insert
        insert = _insert_doc(e2e_client, non_admin_user.headers, table_id, {"y": 2})
        assert insert.status_code == 403, insert.text

    def test_role_grant(self, e2e_client, platform_admin, non_admin_user):
        """Table with role grant: user with the role can insert; user without cannot."""
        # Create a dedicated role for this test
        role_name = f"tbl_role_{uuid.uuid4().hex[:8]}"
        role_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": role_name, "description": "table access test role"},
        )
        assert role_resp.status_code == 201, role_resp.text
        role_id = role_resp.json()["id"]

        table_name = f"role_grant_{uuid.uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        _set_access(
            e2e_client,
            platform_admin.headers,
            table_id,
            {
                "everyone": {"read": False, "create": False, "update": False, "delete": False},
                "role": {"roles": [role_id], "read": True, "create": True, "update": True, "delete": True},
                "creator": {"read": False, "create": False, "update": False, "delete": False},
            },
        )

        # Non-admin user has no role yet → denied
        insert_before = _insert_doc(
            e2e_client, non_admin_user.headers, table_id, {"z": 3}
        )
        assert insert_before.status_code == 403, insert_before.text

        # Assign the role to non_admin_user
        assign_resp = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(non_admin_user.user_id)]},
        )
        assert assign_resp.status_code == 204, assign_resp.text

        # Now the user can insert
        insert_after = _insert_doc(
            e2e_client, non_admin_user.headers, table_id, {"z": 3}
        )
        assert insert_after.status_code == 201, insert_after.text

    def test_creator_filter_in_query(
        self, e2e_client, platform_admin, alice_user, bob_user
    ):
        """Table with creator-only read: each user sees only their own rows."""
        table_name = f"creator_filter_{uuid.uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        _set_access(
            e2e_client,
            platform_admin.headers,
            table_id,
            {
                "everyone": {"read": False, "create": False, "update": False, "delete": False},
                "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
                "creator": {"read": True, "create": True, "update": False, "delete": False},
            },
        )

        # Alice inserts a row
        alice_insert = _insert_doc(
            e2e_client, alice_user.headers, table_id, {"owner": "alice"}
        )
        assert alice_insert.status_code == 201, alice_insert.text

        # Bob inserts a row
        bob_insert = _insert_doc(
            e2e_client, bob_user.headers, table_id, {"owner": "bob"}
        )
        assert bob_insert.status_code == 201, bob_insert.text

        # Alice queries — sees only her row
        alice_query = _query_docs(e2e_client, alice_user.headers, table_id)
        assert alice_query.status_code == 200, alice_query.text
        alice_docs = alice_query.json()["documents"]
        assert len(alice_docs) == 1, f"Alice expected 1 doc, got {len(alice_docs)}"
        assert alice_docs[0]["data"]["owner"] == "alice"

        # Bob queries — sees only his row
        bob_query = _query_docs(e2e_client, bob_user.headers, table_id)
        assert bob_query.status_code == 200, bob_query.text
        bob_docs = bob_query.json()["documents"]
        assert len(bob_docs) == 1, f"Bob expected 1 doc, got {len(bob_docs)}"
        assert bob_docs[0]["data"]["owner"] == "bob"
