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


@pytest.mark.e2e
class TestTableUpdatePublic:
    """TableUpdate rename via PATCH /api/tables/{id}."""

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


@pytest.mark.e2e
class TestTableDefaultDeny:
    """Non-admin users are denied by default when only the seeded admin_bypass policy applies."""

    def test_default_deny_non_superuser_in_same_org(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """A freshly-created table in non-admin's org only grants admins;
        non-admins in the same org see empty reads and 403 writes from the
        policy layer (admin_bypass-only seed)."""
        org1_id = org1["id"]
        table_name = f"default_deny_{uuid4().hex[:8]}"
        # Create the table IN non-admin's org, otherwise the org gate (not
        # the policy gate) kicks in and produces 404 instead of 403.
        table_id = _create_table(
            e2e_client,
            platform_admin.headers,
            table_name,
            organization_id=org1_id,
        )

        # Non-admin insert → 403 (policy denies)
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
    consumed by the web SDK. Two layers of enforcement:

    - ``_resolve_target_org_safe`` (silent fallback): non-superuser scope is
      ignored, returning their own org for the name-based lookup path.
    - ``get_table_or_404`` org gate (hard 404): after fetching by UUID or
      name, if the table's ``organization_id`` is neither None (global) nor
      the caller's home org, raise 404. Non-superusers cannot reach another
      org's table at any document endpoint regardless of scope.

    Superusers (= provider admins) bypass the gate; ``scope`` selects which
    org their request targets.
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

    def test_provider_admin_can_reach_all_new_endpoints_cross_org(
        self, e2e_client, platform_admin, org2
    ):
        """Sibling check to ``test_provider_admin_targets_other_org_via_scope``
        for the endpoints added in the consolidation work — upsert + the two
        batch verbs. A provider admin (superuser) targeting another org via
        UUID lookup must hit each endpoint successfully.
        """
        org2_id = org2["id"]
        name = f"prov_admin_new_{uuid4().hex[:8]}"
        table_id = _create_table(
            e2e_client, platform_admin.headers, name, organization_id=org2_id,
        )

        # Upsert verb against an org2 table by UUID — superuser-bypass on the org gate.
        doc_id = f"k-{uuid4().hex[:8]}"
        up = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=platform_admin.headers,
            json={"id": doc_id, "data": {"v": 1}},
        )
        assert up.status_code == 200, up.text

        # Batch insert.
        b = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={"documents": [{"data": {"v": 2}}, {"data": {"v": 3}}]},
        )
        assert b.status_code == 200, b.text
        assert b.json()["inserted"] == 2

        # Batch delete (the original upsert row).
        bd = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch-delete",
            headers=platform_admin.headers,
            json={"ids": [doc_id]},
        )
        assert bd.status_code == 200, bd.text
        assert bd.json()["deleted"] == 1
        assert bd.json()["deleted_ids"] == [doc_id]

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

    def test_non_superuser_cannot_reach_other_org_table_by_uuid(
        self, e2e_client, platform_admin, alice_user, org2
    ):
        """Hard rule: non-superuser + non-self-org + non-global = 404 at every endpoint.

        The org gate fires before the policy layer in get_table_or_404. UUID
        lookup is org-blind, so the gate runs after fetch — alice cannot reach
        an org2 table at any document endpoint regardless of method or scope.
        """
        org2_id = org2["id"]
        name = f"hardgate_{uuid4().hex[:8]}"
        table_id = _create_table(
            e2e_client,
            platform_admin.headers,
            name,
            organization_id=org2_id,
        )
        ins = e2e_client.post(
            f"/api/tables/{name}/documents",
            headers=platform_admin.headers,
            params={"scope": org2_id},
            json={"data": {"secret": "org2-only"}},
        )
        assert ins.status_code == 201, ins.text
        doc_id = ins.json()["id"]

        # Every document endpoint must 404 (or 403 for body-bearing rejects).
        # All CRUD verbs against the UUID, with and without scope. Includes
        # the upsert and batch endpoints added in the consolidation work —
        # they share get_table_or_404, so the gate covers them too, but
        # this asserts that explicitly so a future refactor that bypasses
        # the gate on one verb breaks the test loud.
        for params in ({}, {"scope": org2_id}, {"scope": "global"}):
            q = e2e_client.post(
                f"/api/tables/{table_id}/documents/query",
                headers=alice_user.headers,
                params=params,
                json={},
            )
            assert q.status_code == 404, f"query {params} leaked: {q.status_code} {q.text}"

            g = e2e_client.get(
                f"/api/tables/{table_id}/documents/{doc_id}",
                headers=alice_user.headers,
                params=params,
            )
            assert g.status_code == 404, f"get {params} leaked: {g.status_code}"

            i = e2e_client.post(
                f"/api/tables/{table_id}/documents",
                headers=alice_user.headers,
                params=params,
                json={"data": {"x": 1}},
            )
            assert i.status_code == 404, f"insert {params} succeeded: {i.status_code}"

            p = e2e_client.patch(
                f"/api/tables/{table_id}/documents/{doc_id}",
                headers=alice_user.headers,
                params=params,
                json={"data": {"x": 1}},
            )
            assert p.status_code == 404, f"patch {params} leaked: {p.status_code}"

            d = e2e_client.delete(
                f"/api/tables/{table_id}/documents/{doc_id}",
                headers=alice_user.headers,
                params=params,
            )
            assert d.status_code == 404, f"delete {params} leaked: {d.status_code}"

            c = e2e_client.get(
                f"/api/tables/{table_id}/documents/count",
                headers=alice_user.headers,
                params=params,
            )
            assert c.status_code == 404, f"count {params} leaked: {c.status_code}"

            # Upsert verb (new in consolidation).
            up = e2e_client.post(
                f"/api/tables/{table_id}/documents/upsert",
                headers=alice_user.headers,
                params=params,
                json={"id": "k1", "data": {"x": 1}},
            )
            assert up.status_code == 404, f"upsert {params} leaked: {up.status_code}"

            # Batch insert/upsert.
            b = e2e_client.post(
                f"/api/tables/{table_id}/documents/batch",
                headers=alice_user.headers,
                params=params,
                json={"documents": [{"data": {"x": 1}}]},
            )
            assert b.status_code == 404, f"batch {params} leaked: {b.status_code}"

            # Batch delete.
            bd = e2e_client.post(
                f"/api/tables/{table_id}/documents/batch-delete",
                headers=alice_user.headers,
                params=params,
                json={"ids": [doc_id]},
            )
            assert bd.status_code == 404, f"batch-delete {params} leaked: {bd.status_code}"

        # Same checks via NAME (the existing _resolve_target_org_safe path
        # already 404s here because the resolved org is alice's, and her org
        # has no table by this name; included for completeness).
        q_by_name = e2e_client.post(
            f"/api/tables/{name}/documents/query",
            headers=alice_user.headers,
            json={},
        )
        assert q_by_name.status_code == 404

    def test_non_superuser_can_reach_global_tables(
        self, e2e_client, platform_admin, alice_user
    ):
        """Global tables (organization_id IS NULL) ARE accessible to all users.

        The org gate allows: superuser, OR table.organization_id is None,
        OR table.organization_id == user.organization_id.
        """
        name = f"global_access_{uuid4().hex[:8]}"
        # Create a global table with everyone_read so alice can see rows.
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": name,
                "description": "global",
                "organization_id": None,
                "policies": {
                    "policies": [
                        {
                            "name": "admin_bypass",
                            "actions": ["read", "create", "update", "delete"],
                            "when": {"user": "is_platform_admin"},
                        },
                        {"name": "everyone_read", "actions": ["read"], "when": None},
                    ],
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]

        ins = e2e_client.post(
            f"/api/tables/{name}/documents",
            headers=platform_admin.headers,
            json={"data": {"public": True}},
        )
        assert ins.status_code == 201

        # Alice can read via UUID
        q = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=alice_user.headers,
            json={},
        )
        assert q.status_code == 200, q.text
        assert q.json()["table_id"] == table_id
        assert len(q.json()["documents"]) == 1


@pytest.mark.e2e
class TestDocumentAttributionOverride:
    """`created_by` / `updated_by` override on document writes.

    Engine and platform-admin callers can override attribution to attribute
    a write to a different actor (used by the SDK to attribute workflow
    writes to the workflow's calling user). Non-privileged callers that
    send the field receive 403 — they cannot forge attribution.
    """

    def test_superuser_can_set_created_by_on_insert(
        self, e2e_client, platform_admin, alice_user
    ):
        """Superuser sends `created_by` in the body; the row is attributed to
        that user, not the calling superuser."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"attr_super_ins_{uuid4().hex[:8]}"
        )
        resp = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"data": {"x": 1}, "created_by": str(alice_user.user_id)},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["created_by"] == str(alice_user.user_id)
        # updated_by defaults to created_by on insert when not explicitly set
        assert body["updated_by"] == str(alice_user.user_id)

    def test_superuser_can_set_distinct_created_by_and_updated_by_on_insert(
        self, e2e_client, platform_admin, alice_user, non_admin_user
    ):
        """Superuser can set created_by and updated_by to different actors."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"attr_super_dist_{uuid4().hex[:8]}"
        )
        resp = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={
                "data": {"x": 1},
                "created_by": str(alice_user.user_id),
                "updated_by": str(non_admin_user.user_id),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["created_by"] == str(alice_user.user_id)
        assert body["updated_by"] == str(non_admin_user.user_id)

    def test_superuser_can_set_updated_by_on_update(
        self, e2e_client, platform_admin, alice_user
    ):
        """Superuser sends `updated_by` on PATCH; row is attributed accordingly."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"attr_super_upd_{uuid4().hex[:8]}"
        )
        ins = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"data": {"x": 1}},
        )
        assert ins.status_code == 201, ins.text
        doc_id = ins.json()["id"]

        upd = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=platform_admin.headers,
            json={"data": {"x": 2}, "updated_by": str(alice_user.user_id)},
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["updated_by"] == str(alice_user.user_id)

    def test_non_superuser_with_created_by_returns_403(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """Non-superuser sending `created_by` is rejected (can't forge attribution)."""
        org1_id = org1["id"]
        # Table must seed a policy that grants non_admin_user create access; admin_bypass
        # alone denies the call before attribution check. Use an everyone-can-create policy.
        table_name = f"attr_forge_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "organization_id": org1_id,
                "policies": {
                    "policies": [
                        {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": {"user": "is_platform_admin"}},
                        {"name": "anyone_create", "actions": ["create"], "when": None},
                    ]
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]

        forge = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=non_admin_user.headers,
            json={"data": {"x": 1}, "created_by": str(platform_admin.user_id)},
        )
        assert forge.status_code == 403, forge.text
        assert "override" in forge.json()["detail"].lower()

    def test_non_superuser_with_updated_by_returns_403(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """Non-superuser sending `updated_by` on PATCH is rejected."""
        org1_id = org1["id"]
        table_name = f"attr_forge_upd_{uuid4().hex[:8]}"
        # Policy: non_admin_user can create+update their own rows; admin_bypass for admins.
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "organization_id": org1_id,
                "policies": {
                    "policies": [
                        {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": {"user": "is_platform_admin"}},
                        {"name": "anyone", "actions": ["create", "update"], "when": None},
                    ]
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]

        # Non-admin inserts a row without override (allowed)
        ins = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=non_admin_user.headers,
            json={"data": {"x": 1}},
        )
        assert ins.status_code == 201, ins.text
        doc_id = ins.json()["id"]

        # Non-admin updates with override → 403
        forge = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=non_admin_user.headers,
            json={"data": {"x": 2}, "updated_by": str(platform_admin.user_id)},
        )
        assert forge.status_code == 403, forge.text
        assert "override" in forge.json()["detail"].lower()

    def test_non_superuser_without_override_defaults_to_caller(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """Regression: non-superuser writing without override is unaffected — row
        is attributed to the calling user."""
        org1_id = org1["id"]
        table_name = f"attr_default_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "organization_id": org1_id,
                "policies": {
                    "policies": [
                        {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": {"user": "is_platform_admin"}},
                        {"name": "anyone", "actions": ["create", "read", "update"], "when": None},
                    ]
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]

        ins = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=non_admin_user.headers,
            json={"data": {"x": 1}},
        )
        assert ins.status_code == 201, ins.text
        body = ins.json()
        assert body["created_by"] == str(non_admin_user.user_id)
        assert body["updated_by"] == str(non_admin_user.user_id)

    def test_batch_with_forged_attribution_fails_whole_batch(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """A batch with any item carrying override from a non-superuser fails
        the whole batch with 403 before any row is written."""
        org1_id = org1["id"]
        table_name = f"attr_batch_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "organization_id": org1_id,
                "policies": {
                    "policies": [
                        {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": {"user": "is_platform_admin"}},
                        {"name": "anyone", "actions": ["create"], "when": None},
                    ]
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]

        batch = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=non_admin_user.headers,
            json={
                "documents": [
                    {"data": {"x": 1}},
                    {"data": {"x": 2}, "created_by": str(platform_admin.user_id)},
                    {"data": {"x": 3}},
                ],
            },
        )
        assert batch.status_code == 403, batch.text

        # No rows should have been written
        cnt = e2e_client.get(
            f"/api/tables/{table_id}/documents/count",
            headers=platform_admin.headers,
        )
        assert cnt.status_code == 200
        assert cnt.json()["count"] == 0

    def test_batch_superuser_distinct_attribution_per_item(
        self, e2e_client, platform_admin, alice_user, non_admin_user
    ):
        """Superuser batch writes can carry per-item attribution."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"attr_batch_super_{uuid4().hex[:8]}"
        )
        resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "documents": [
                    {"data": {"x": 1}, "created_by": str(alice_user.user_id)},
                    {"data": {"x": 2}, "created_by": str(non_admin_user.user_id)},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["inserted"] == 2

        q = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=platform_admin.headers,
            json={"order_by": "x", "order_dir": "asc"},
        )
        assert q.status_code == 200, q.text
        docs = q.json()["documents"]
        assert len(docs) == 2
        # Map by data.x to assert per-item attribution
        by_x = {d["data"]["x"]: d for d in docs}
        assert by_x[1]["created_by"] == str(alice_user.user_id)
        assert by_x[2]["created_by"] == str(non_admin_user.user_id)


@pytest.mark.e2e
class TestDocumentUpsertVerb:
    """Explicit ``POST /api/tables/{id}/documents/upsert`` — atomic INSERT
    ON CONFLICT DO UPDATE with replace semantics on conflict."""

    def test_upsert_inserts_when_missing(self, e2e_client, platform_admin):
        """First call creates the row; response carries the supplied id."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"upsert_ins_{uuid4().hex[:8]}"
        )
        doc_id = f"key-{uuid4().hex[:8]}"
        resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=platform_admin.headers,
            json={"id": doc_id, "data": {"v": 1}},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == doc_id
        assert body["data"] == {"v": 1}
        assert body["created_by"] == str(platform_admin.user_id)

    def test_upsert_replaces_on_conflict(self, e2e_client, platform_admin):
        """Second call with the same id REPLACES (not merges) the data column."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"upsert_rep_{uuid4().hex[:8]}"
        )
        doc_id = f"key-{uuid4().hex[:8]}"
        first = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=platform_admin.headers,
            json={"id": doc_id, "data": {"a": 1, "b": 2}},
        )
        assert first.status_code == 200, first.text

        second = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=platform_admin.headers,
            json={"id": doc_id, "data": {"a": 99}},
        )
        assert second.status_code == 200, second.text
        # Replace semantics: 'b' is gone; only 'a' remains.
        assert second.json()["data"] == {"a": 99}

    def test_upsert_denied_for_non_superuser_no_policy(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """A seeded admin_bypass-only table denies non-admin upserts (no create rule)."""
        org1_id = org1["id"]
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"upsert_deny_{uuid4().hex[:8]}",
            organization_id=org1_id,
        )
        resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=non_admin_user.headers,
            json={"id": f"k-{uuid4().hex[:8]}", "data": {"v": 1}},
        )
        assert resp.status_code == 403, resp.text

    def test_upsert_existing_row_gates_on_update_action(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """If a row already exists, the upsert is gated by the `update` action
        on the pre-image (same as PATCH semantics)."""
        org1_id = org1["id"]
        table_name = f"upsert_gate_{uuid4().hex[:8]}"
        # Policy: anyone can create, but only admins can update
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "organization_id": org1_id,
                "policies": {
                    "policies": [
                        {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": {"user": "is_platform_admin"}},
                        {"name": "anyone_create", "actions": ["create"], "when": None},
                    ]
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]
        doc_id = f"k-{uuid4().hex[:8]}"

        # Non-admin upserts a new row → allowed (create branch)
        first = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=non_admin_user.headers,
            json={"id": doc_id, "data": {"v": 1}},
        )
        assert first.status_code == 200, first.text

        # Non-admin upserts the same id → must hit the update branch and 403
        second = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=non_admin_user.headers,
            json={"id": doc_id, "data": {"v": 2}},
        )
        assert second.status_code == 403, second.text

    def test_upsert_id_is_required(self, e2e_client, platform_admin):
        """Missing id → 422 (the conflict key isn't optional on this verb)."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"upsert_noid_{uuid4().hex[:8]}"
        )
        resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=platform_admin.headers,
            json={"data": {"v": 1}},
        )
        assert resp.status_code == 422, resp.text

    def test_upsert_attribution_override_on_insert(
        self, e2e_client, platform_admin, alice_user
    ):
        """Engine/superuser can override created_by on the insert branch."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"upsert_attr_{uuid4().hex[:8]}"
        )
        resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=platform_admin.headers,
            json={
                "id": f"k-{uuid4().hex[:8]}",
                "data": {"v": 1},
                "created_by": str(alice_user.user_id),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created_by"] == str(alice_user.user_id)
        assert body["updated_by"] == str(alice_user.user_id)

    def test_upsert_attribution_override_rejected_for_non_superuser(
        self, e2e_client, platform_admin, non_admin_user, org1
    ):
        """Non-privileged caller sending an override gets 403 even if the
        action would have been allowed."""
        org1_id = org1["id"]
        table_name = f"upsert_attr_403_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "organization_id": org1_id,
                "policies": {
                    "policies": [
                        {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": {"user": "is_platform_admin"}},
                        {"name": "anyone", "actions": ["create"], "when": None},
                    ]
                },
            },
        )
        assert resp.status_code == 201, resp.text
        table_id = resp.json()["id"]

        forge = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert",
            headers=non_admin_user.headers,
            json={
                "id": f"k-{uuid4().hex[:8]}",
                "data": {"v": 1},
                "created_by": str(platform_admin.user_id),
            },
        )
        assert forge.status_code == 403, forge.text


@pytest.mark.e2e
class TestDocumentQueryPaginationStable:
    """Pagination must be stable across pages even when the primary sort
    column has ties.

    Regression for the bug where ``DocumentRepository.query`` ordered by
    ``created_at`` only — rows inserted in the same transaction shared a
    timestamp and Postgres returned them in arbitrary order across
    OFFSET/LIMIT calls, so the same id could appear on adjacent pages or be
    skipped entirely. The fix appends ``Document.id`` as a secondary sort
    so OFFSET/LIMIT is deterministic.
    """

    def _seed(self, e2e_client, platform_admin, n: int) -> tuple[str, list[str]]:
        """Create a table and batch-insert N rows in one transaction so
        ``created_at`` is effectively tied across them. Returns
        ``(table_id, sorted_ids)``."""
        name = f"pag_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, name)

        b = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={"documents": [{"data": {"i": i}} for i in range(n)]},
        )
        assert b.status_code == 200, b.text
        assert b.json()["inserted"] == n

        # Pull the canonical id list straight from the DB (one big page) and
        # sort it the same way the new tiebreaker does. This is what each
        # paginated walk should reconstruct.
        full = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=platform_admin.headers,
            json={"limit": 1000},
        )
        assert full.status_code == 200, full.text
        ids = [d["id"] for d in full.json()["documents"]]
        assert len(ids) == n
        return table_id, ids

    def _walk(
        self,
        e2e_client,
        platform_admin,
        table_id: str,
        page_size: int,
        body_extra: dict,
    ) -> list[str]:
        """Walk every page with the given query body extras and return the
        concatenated id list."""
        seen: list[str] = []
        offset = 0
        while True:
            page = e2e_client.post(
                f"/api/tables/{table_id}/documents/query",
                headers=platform_admin.headers,
                json={"limit": page_size, "offset": offset, **body_extra},
            )
            assert page.status_code == 200, page.text
            docs = page.json()["documents"]
            if not docs:
                break
            seen.extend(d["id"] for d in docs)
            if len(docs) < page_size:
                break
            offset += page_size
        return seen

    def test_default_order_paginates_without_duplicates_on_tied_created_at(
        self, e2e_client, platform_admin
    ):
        """Default sort (no ``order_by``) — batch-inserted rows share
        ``created_at``. Pagination must still cover every row exactly once."""
        n = 12
        table_id, full_ids = self._seed(e2e_client, platform_admin, n)

        walked = self._walk(e2e_client, platform_admin, table_id, page_size=4, body_extra={})

        # Every row appears exactly once.
        assert sorted(walked) == sorted(full_ids), (
            f"Pagination drift detected. Full set: {sorted(full_ids)}, "
            f"walked: {walked}"
        )
        assert len(walked) == n
        assert len(set(walked)) == n  # no duplicates

    def test_order_by_jsonb_field_paginates_without_duplicates_on_ties(
        self, e2e_client, platform_admin
    ):
        """When sorting by a JSONB field that has ties (e.g. all rows share
        the same ``status``), the secondary id tiebreaker keeps pages
        non-overlapping."""
        name = f"pag_tie_{uuid4().hex[:8]}"
        table_id = _create_table(e2e_client, platform_admin.headers, name)
        n = 10

        # Every row has the same `status` so ordering by it is fully tied.
        b = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "documents": [
                    {"data": {"i": i, "status": "open"}} for i in range(n)
                ],
            },
        )
        assert b.status_code == 200, b.text

        full = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=platform_admin.headers,
            json={"limit": 1000, "order_by": "status", "order_dir": "asc"},
        )
        assert full.status_code == 200, full.text
        full_ids = [d["id"] for d in full.json()["documents"]]
        assert len(full_ids) == n

        walked = self._walk(
            e2e_client,
            platform_admin,
            table_id,
            page_size=3,
            body_extra={"order_by": "status", "order_dir": "asc"},
        )
        assert sorted(walked) == sorted(full_ids)
        assert len(set(walked)) == n
