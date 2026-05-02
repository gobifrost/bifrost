"""E2E tests for the public tables PATCH endpoint.

Covers the TableUpdate DTO's ability to rename tables and reassign the owning
application, plus validation for bogus application references.
Also covers the default-deny behaviour for non-admin users and the ?scope=
query parameter on the document endpoints.
"""

from uuid import UUID, uuid4

import pytest


def _create_table(
    e2e_client,
    headers,
    name: str,
    organization_id: str | None = None,
) -> str:
    """Create a table via POST /api/tables and return its UUID."""
    body: dict = {"name": name, "description": "original"}
    if organization_id is not None:
        body["organization_id"] = organization_id
    resp = e2e_client.post(
        "/api/tables",
        headers=headers,
        json=body,
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
    """Non-admin users are denied by default when only the seeded admin_bypass policy applies."""

    def test_default_deny_non_superuser(self, e2e_client, platform_admin, non_admin_user):
        """A freshly-created table only grants admins; non-admins see empty reads and 403 writes."""
        table_name = f"default_deny_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, table_name)

        # Non-admin insert → 403
        insert_resp = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=non_admin_user.headers,
            json={"data": {"key": "value"}},
        )
        assert insert_resp.status_code == 403, insert_resp.text

        # Non-admin query → 200 with empty results (existence-non-leak per Task 9 design)
        query_resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=non_admin_user.headers,
            json={},
        )
        assert query_resp.status_code == 200, query_resp.text
        assert query_resp.json()["documents"] == []


@pytest.mark.e2e
class TestDocumentScopeQueryParam:
    """`?scope=` query param on /tables/{table_id}/documents/* endpoints.

    Mirrors the Python SDK's `tables.*` scope semantics on the REST surface
    consumed by the web SDK. The server uses ``_resolve_target_org_safe`` →
    ``resolve_target_org`` from ``src.core.org_filter``, which keys off
    ``user.is_superuser``:

    - Superusers (= provider admins) may target any org via ``?scope=<uuid>``,
      ``?scope=global``, or omit the param.
    - Non-superusers always resolve to their own org; ``scope`` is silently
      ignored at this layer (it is *not* a 422). Cross-org *enforcement* for
      non-providers lives in the SDK's ``ExecutionContext.set_scope`` /
      ``resolve_scope`` (see ``tests/unit/test_scope_override.py``); the
      REST endpoint just refuses to leak data because the resolved org is
      always the caller's own.
    """

    def test_provider_admin_targets_other_org_via_scope(
        self, e2e_client, platform_admin, org2
    ):
        """Platform admin (provider) creates a row in org2's table via ?scope=<org2_id>."""
        org2_id = org2["id"]
        name = f"scope_other_{uuid4().hex[:8]}"
        table_id = _create_table(
            e2e_client,
            platform_admin.headers,
            name,
            organization_id=org2_id,
        )

        # Insert a row directly via ?scope=<org2_id>
        ins = e2e_client.post(
            f"/api/tables/{name}/documents",
            headers=platform_admin.headers,
            params={"scope": org2_id},
            json={"data": {"hello": "org2"}},
        )
        assert ins.status_code == 201, ins.text

        # Query the same table by name + scope and read the row back
        q = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=platform_admin.headers,
            params={"scope": org2_id},
            json={},
        )
        assert q.status_code == 200, q.text
        body = q.json()
        assert body["table_id"] == table_id
        assert len(body["documents"]) == 1
        assert body["documents"][0]["data"] == {"hello": "org2"}

    def test_non_superuser_scope_silently_ignored(
        self, e2e_client, platform_admin, alice_user, org2
    ):
        """Non-superuser passing ?scope=<other_org_id> resolves to caller's own org.

        At the REST layer ``resolve_target_org`` ignores ``scope`` for
        non-superusers — there is no 422. Cross-org access is prevented
        because the resolved org becomes Alice's (org1), so the org2 table
        is invisible: name lookup yields 404 (a global table by that name
        doesn't exist either).
        """
        org2_id = org2["id"]
        name = f"scope_xorg_{uuid4().hex[:8]}"
        # Table lives in org2; alice has no access.
        _create_table(
            e2e_client,
            platform_admin.headers,
            name,
            organization_id=org2_id,
        )

        # Alice tries to query with ?scope=<org2_id>. The server resolves the
        # target org to alice.organization_id (== org1), so the org2 table
        # by that name is not visible → 404.
        q = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=alice_user.headers,
            params={"scope": org2_id},
            json={},
        )
        assert q.status_code == 404, q.text

    def test_user_scope_to_own_org_works(self, e2e_client, platform_admin, alice_user):
        """Alice passes ?scope=<her-own-org-id> — works (resolves to home org)."""
        assert alice_user.organization_id is not None
        alice_org = str(alice_user.organization_id)
        name = f"scope_self_{uuid4().hex[:8]}"
        # Platform admin creates an org-scoped table in alice's org with a
        # policy that lets alice read.
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": name,
                "description": "self scope test",
                "organization_id": alice_org,
                "policies": {
                    "policies": [
                        {
                            "name": "admin_bypass",
                            "actions": ["read", "create", "update", "delete"],
                            "when": {"user": "is_platform_admin"},
                        },
                        {
                            "name": "any_org_user_read",
                            "actions": ["read"],
                            "when": None,
                        },
                    ]
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]

        # Admin seeds a row.
        ins = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"data": {"k": "v"}},
        )
        assert ins.status_code == 201, ins.text

        # Alice reads with explicit ?scope=<her_org>; allowed (matches default).
        q = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=alice_user.headers,
            params={"scope": alice_org},
            json={},
        )
        assert q.status_code == 200, q.text
        body = q.json()
        assert body["table_id"] == table_id
        assert len(body["documents"]) == 1

    def test_scope_global_targets_global_table(self, e2e_client, platform_admin, org1):
        """?scope=global resolves to the global (organization_id IS NULL) table."""
        name = f"scope_global_{uuid4().hex[:8]}"
        # Same name in two scopes: global + org1. Explicitly create the global
        # table by passing organization_id=null in the body — platform_admin's
        # default ctx.org_id is the platform/provider org, NOT None.
        global_resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={"name": name, "description": "global", "organization_id": None},
        )
        assert global_resp.status_code == 201, global_resp.text
        global_table_id = global_resp.json()["id"]
        assert global_resp.json()["organization_id"] is None, global_resp.text

        org_table_id = _create_table(
            e2e_client,
            platform_admin.headers,
            name,
            organization_id=org1["id"],
        )
        assert global_table_id != org_table_id

        # Seed one row each so we can prove which table was hit.
        ins_global = e2e_client.post(
            f"/api/tables/{global_table_id}/documents",
            headers=platform_admin.headers,
            json={"data": {"where": "global"}},
        )
        assert ins_global.status_code == 201, ins_global.text
        ins_org = e2e_client.post(
            f"/api/tables/{org_table_id}/documents",
            headers=platform_admin.headers,
            json={"data": {"where": "org1"}},
        )
        assert ins_org.status_code == 201, ins_org.text

        # ?scope=global hits the global table, NOT the org1 same-name table.
        q = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=platform_admin.headers,
            params={"scope": "global"},
            json={},
        )
        assert q.status_code == 200, q.text
        body = q.json()
        assert body["table_id"] == global_table_id
        assert len(body["documents"]) == 1
        assert body["documents"][0]["data"] == {"where": "global"}

    def test_query_response_carries_table_id(self, e2e_client, platform_admin):
        """`DocumentListResponse.table_id` matches the resolved table's UUID.

        The web SDK relies on this to switch from a name-based query to a
        UUID-based realtime subscription without re-resolving the name.
        """
        name = f"scope_tid_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, name)

        q = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=platform_admin.headers,
            json={},
        )
        assert q.status_code == 200, q.text
        body = q.json()
        # Returned table_id must be a real UUID and equal the table we
        # created (not the request path's `name`).
        assert body["table_id"] == table_id
        # Spot-check it parses as a UUID.
        UUID(body["table_id"])

    def test_name_collision_disambiguated_by_scope(
        self, e2e_client, platform_admin, org1, org2
    ):
        """Same table name in two orgs is disambiguated by ?scope=<org-id>.

        This proves the cross-org name-collision case the design called out:
        without scope, name lookup is ambiguous; with scope, the correct
        table is selected per-org.
        """
        org1_id = org1["id"]
        org2_id = org2["id"]
        name = f"scope_dup_{uuid4().hex[:8]}"

        org1_table_id = _create_table(
            e2e_client,
            platform_admin.headers,
            name,
            organization_id=org1_id,
        )
        org2_table_id = _create_table(
            e2e_client,
            platform_admin.headers,
            name,
            organization_id=org2_id,
        )
        assert org1_table_id != org2_table_id

        # Seed a distinct row in each.
        for tid, marker in ((org1_table_id, "org1"), (org2_table_id, "org2")):
            r = e2e_client.post(
                f"/api/tables/{tid}/documents",
                headers=platform_admin.headers,
                json={"data": {"marker": marker}},
            )
            assert r.status_code == 201, r.text

        # Query by name + scope=org1 → org1 table.
        q1 = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=platform_admin.headers,
            params={"scope": org1_id},
            json={},
        )
        assert q1.status_code == 200, q1.text
        b1 = q1.json()
        assert b1["table_id"] == org1_table_id
        assert [d["data"]["marker"] for d in b1["documents"]] == ["org1"]

        # Query by name + scope=org2 → org2 table.
        q2 = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=platform_admin.headers,
            params={"scope": org2_id},
            json={},
        )
        assert q2.status_code == 200, q2.text
        b2 = q2.json()
        assert b2["table_id"] == org2_table_id
        assert [d["data"]["marker"] for d in b2["documents"]] == ["org2"]

    def test_scope_invalid_value_returns_422(self, e2e_client, platform_admin):
        """A non-UUID, non-'global' scope value is a 422 from the safe wrapper."""
        name = f"scope_bad_{uuid4().hex[:8]}"
        _create_table(e2e_client, platform_admin.headers, name)

        q = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=platform_admin.headers,
            params={"scope": "not-a-uuid-or-global"},
            json={},
        )
        assert q.status_code == 422, q.text

    def test_uuid_lookup_does_not_leak_other_org_rows(
        self, e2e_client, platform_admin, alice_user, org2
    ):
        """A non-superuser who learns another org's table UUID cannot read its rows.

        get_table_or_404's UUID branch fetches the Table row without an org
        filter (pre-existing behavior). The defense is the policy gate, not
        the table lookup. Verify the policy gate holds: alice (org1) targets
        an org2 table by UUID + scope, gets the table object back, but the
        rows are policy-filtered to empty.
        """
        org2_id = org2["id"]
        name = f"uuid_leak_{uuid4().hex[:8]}"
        # Org2 table with admin_bypass only — no read for non-org2 users.
        table_id = _create_table(
            e2e_client,
            platform_admin.headers,
            name,
            organization_id=org2_id,
        )
        # Provider admin inserts a row into org2.
        ins = e2e_client.post(
            f"/api/tables/{name}/documents",
            headers=platform_admin.headers,
            params={"scope": org2_id},
            json={"data": {"secret": "org2-only"}},
        )
        assert ins.status_code == 201, ins.text

        # Alice (org1) hits the org2 table BY UUID with ?scope=org2_id.
        # Scope is silently ignored for non-superusers, but the UUID lookup
        # at line 614 of tables.py finds the table regardless. The policy
        # gate must filter the read to empty.
        q = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=alice_user.headers,
            params={"scope": org2_id},
            json={},
        )
        assert q.status_code == 200, q.text
        body = q.json()
        assert body["table_id"] == table_id
        assert body["documents"] == [], (
            f"LEAK: alice saw org2 rows via UUID + scope: {body['documents']}"
        )

        # Direct GET by doc_id is also gated.
        doc_id = ins.json()["id"]
        get_one = e2e_client.get(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=alice_user.headers,
            params={"scope": org2_id},
        )
        assert get_one.status_code == 403, get_one.text
