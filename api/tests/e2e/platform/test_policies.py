"""E2E tests for table policy rules."""

import uuid

import pytest


def _create_table(e2e_client, headers, name: str, policies=None, organization_id=None) -> str:
    """Create a table for policy testing.

    Defaults to a GLOBAL table (organization_id=None) so the org gate in
    get_table_or_404 doesn't confound the policy-layer assertions. Tests
    that specifically need an org-scoped table can pass organization_id.
    """
    # Explicitly mark as global so the platform-admin's home org doesn't
    # leak into the table's org_id when caller didn't specify one.
    body = {"name": name, "description": "policy test table", "organization_id": organization_id}
    if policies is not None:
        body["policies"] = policies
    resp = e2e_client.post("/api/tables", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _set_policies(e2e_client, headers, table_id, policies):
    r = e2e_client.patch(
        f"/api/tables/{table_id}", headers=headers, json={"policies": policies}
    )
    assert r.status_code == 200, r.text


def _insert(e2e_client, headers, table_id, data):
    return e2e_client.post(
        f"/api/tables/{table_id}/documents", headers=headers, json={"data": data}
    )


def _query(e2e_client, headers, table_id):
    return e2e_client.post(
        f"/api/tables/{table_id}/documents/query", headers=headers, json={}
    )


def _admin_bypass_policies():
    return {"policies": [{
        "name": "admin_bypass",
        "actions": ["read", "create", "update", "delete"],
        "when": {"user": "is_platform_admin"},
    }]}


def _add_own_row_policy(base_policies):
    """Append the standard own-row policy."""
    new = dict(base_policies)
    new["policies"] = list(base_policies["policies"]) + [{
        "name": "own_row",
        "actions": ["read", "update", "delete"],
        "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
    }]
    return new


@pytest.mark.e2e
class TestPoliciesMatrix:

    def test_default_seeded_admin_bypass_allows_admin(self, e2e_client, platform_admin):
        """Newly-created table seeds admin_bypass; admins can do everything."""
        table_id = _create_table(e2e_client, platform_admin.headers, f"seed_{uuid.uuid4().hex[:8]}")
        r = _insert(e2e_client, platform_admin.headers, table_id, {"x": 1})
        assert r.status_code == 201, r.text
        q = _query(e2e_client, platform_admin.headers, table_id)
        assert q.status_code == 200
        assert len(q.json()["documents"]) == 1

    def test_default_seeded_table_denies_non_admin(self, e2e_client, platform_admin, alice_user):
        """Seeded table has only admin_bypass; non-admins get empty/403."""
        table_id = _create_table(e2e_client, platform_admin.headers, f"seed_alice_{uuid.uuid4().hex[:8]}")
        # Alice queries: no rule grants her read → empty result
        q = _query(e2e_client, alice_user.headers, table_id)
        assert q.status_code == 200
        assert q.json()["documents"] == []
        # Alice tries to insert: 403
        r = _insert(e2e_client, alice_user.headers, table_id, {"x": 1})
        assert r.status_code == 403, r.text

    def test_create_with_explicit_policies_does_not_seed(
        self, e2e_client, platform_admin, alice_user
    ):
        """Passing policies on create skips the seed; admin not auto-allowed unless rule grants it."""
        # No admin_bypass; only an own_row rule for reads
        explicit = {"policies": [{
            "name": "own_row_read",
            "actions": ["read"],
            "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
        }]}
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"explicit_{uuid.uuid4().hex[:8]}", policies=explicit
        )
        # Admin can't insert (no create rule, no admin_bypass)
        r = _insert(e2e_client, platform_admin.headers, table_id, {"x": 1})
        assert r.status_code == 403, r.text

    def test_own_row_policy_filters_query(
        self, e2e_client, platform_admin, alice_user, bob_user
    ):
        """Two users insert; each only sees their own."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"own_{uuid.uuid4().hex[:8]}",
            policies=_add_own_row_policy(_admin_bypass_policies()) | {
                "policies": _admin_bypass_policies()["policies"] + [
                    {
                        "name": "own_row_full",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
                    },
                ]
            },
        )
        # Need to set policies again because the dict-merge above is fragile.
        # Replace with explicit set:
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "own_row_full",
                "actions": ["read", "create", "update", "delete"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
        ]})

        # Alice and Bob each insert
        ar = _insert(e2e_client, alice_user.headers, table_id, {"who": "alice"})
        assert ar.status_code == 201, ar.text
        br = _insert(e2e_client, bob_user.headers, table_id, {"who": "bob"})
        assert br.status_code == 201, br.text

        # Alice queries
        aq = _query(e2e_client, alice_user.headers, table_id).json()["documents"]
        assert len(aq) == 1 and aq[0]["data"]["who"] == "alice"
        # Bob queries
        bq = _query(e2e_client, bob_user.headers, table_id).json()["documents"]
        assert len(bq) == 1 and bq[0]["data"]["who"] == "bob"
        # Admin queries — sees both
        admin_q = _query(e2e_client, platform_admin.headers, table_id).json()["documents"]
        assert len(admin_q) == 2

    def test_state_locked_update(self, e2e_client, platform_admin, alice_user):
        """Owner can update while status=open; cannot once status=done (pre-update semantics)."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"locked_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "owner_open",
                "actions": ["read", "create", "update"],
                "when": {
                    "and": [
                        {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
                        {"eq": [{"row": "status"}, "open"]},
                    ]
                },
            },
        ]})

        # Alice creates a row in 'open' state
        r = _insert(e2e_client, alice_user.headers, table_id, {"status": "open", "title": "task1"})
        assert r.status_code == 201, r.text
        doc_id = r.json()["id"]

        # Alice can update while open
        u1 = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=alice_user.headers,
            json={"data": {"status": "open", "title": "task1-edited"}},
        )
        assert u1.status_code == 200, u1.text

        # Alice flips to done (pre-update state was open → allowed)
        u2 = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=alice_user.headers,
            json={"data": {"status": "done", "title": "task1-done"}},
        )
        assert u2.status_code == 200, u2.text

        # Now status is done; further updates should be denied
        u3 = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=alice_user.headers,
            json={"data": {"status": "done", "title": "edit-after-done"}},
        )
        assert u3.status_code == 403, u3.text

    def test_role_gated_via_has_role(self, e2e_client, platform_admin, alice_user):
        """has_role() in policy gates by role membership."""
        # Create a role and assign Alice to it
        role_resp = e2e_client.post(
            "/api/roles", headers=platform_admin.headers,
            json={"name": "policy_test_role", "description": "for policy test"},
        )
        assert role_resp.status_code == 201, role_resp.text
        role_id = role_resp.json()["id"]
        e2e_client.post(
            f"/api/roles/{role_id}/users", headers=platform_admin.headers,
            json={"user_ids": [str(alice_user.user_id)]},
        )

        table_id = _create_table(
            e2e_client, platform_admin.headers, f"role_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "role_can_read",
                "actions": ["read", "create"],
                "when": {"call": "has_role", "args": [role_id]},
            },
        ]})

        # Alice (has the role) can insert
        ar = _insert(e2e_client, alice_user.headers, table_id, {"x": 1})
        assert ar.status_code == 201, ar.text

    def test_admin_bypass_can_be_removed(self, e2e_client, platform_admin):
        """Removing the seeded admin_bypass denies admins."""
        table_id = _create_table(e2e_client, platform_admin.headers, f"strict_{uuid.uuid4().hex[:8]}")
        # Replace with an explicit policy that excludes admin
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "no_one_writes",
                "actions": ["read"],
                "when": None,  # everyone can read
            },
        ]})
        # Admin tries to insert → 403
        r = _insert(e2e_client, platform_admin.headers, table_id, {"x": 1})
        assert r.status_code == 403, r.text

    def test_denial_writes_audit_row(self, e2e_client, platform_admin, alice_user):
        """A 403 from policy denial writes an audit row that the admin can query."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"audit_{uuid.uuid4().hex[:8]}",
        )  # seeded admin_bypass only — Alice will be denied

        # Alice tries to insert (denied)
        r = _insert(e2e_client, alice_user.headers, table_id, {"x": 1})
        assert r.status_code == 403

        # Admin queries the audit log
        audit = e2e_client.get(
            "/api/audit",
            headers=platform_admin.headers,
            params={
                "action": "policy.deny",
                "user_id": str(alice_user.user_id),
                "limit": 50,
            },
        )
        assert audit.status_code == 200, audit.text
        rows = audit.json()["entries"]
        assert len(rows) >= 1, f"no policy.deny rows for alice: {rows}"
        entry = rows[0]
        assert entry["action"] == "policy.deny"
        assert entry["resource_type"] == "table_document"
        assert entry["outcome"] == "failure"
        assert entry["details"]["policy_action"] == "create"
        assert entry["details"]["table_id"] == table_id
        assert set(entry["details"].keys()) == {"policy_action", "table_id", "table_name"}

    def test_cross_org_isolation_via_org_policy(
        self, e2e_client, platform_admin, alice_user, org2_user
    ):
        """Alice (org1) and org2_user (org2) share the same global table; an
        ``own_org`` policy keyed off ``user.organization_id`` must filter rows
        denormalized with the inserter's org. Alice MUST NOT see org2's row,
        and org2_user MUST NOT see Alice's row — same table, same policy.

        Security boundary: a single policy clause is the entire org-isolation
        mechanism (the document data path has no implicit org filter). If the
        evaluator/compiler ever stops resolving ``{user: organization_id}``
        correctly, both users see both rows.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"orgiso_{uuid.uuid4().hex[:8]}",
        )
        # Policy:
        #   - admin_bypass keeps admin able to seed/inspect
        #   - everyone may insert rows for THEIR OWN org (so alice and
        #     org2_user can both populate)
        #   - reads are gated to rows whose data.organization_id matches the
        #     caller's user.organization_id
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "create_for_own_org",
                "actions": ["create"],
                "when": {
                    "eq": [
                        {"row": "organization_id"},
                        {"user": "organization_id"},
                    ]
                },
            },
            {
                "name": "own_org_read",
                "actions": ["read"],
                "when": {
                    "eq": [
                        {"row": "organization_id"},
                        {"user": "organization_id"},
                    ]
                },
            },
        ]})

        alice_org = str(alice_user.organization_id)
        org2_org = str(org2_user.organization_id)
        assert alice_org != org2_org, "fixture sanity: orgs must differ"

        # Alice (org1) inserts; row data carries her org id
        ar = _insert(
            e2e_client, alice_user.headers, table_id,
            {"who": "alice", "organization_id": alice_org},
        )
        assert ar.status_code == 201, ar.text

        # org2_user (org2) inserts; row data carries org2's id
        br = _insert(
            e2e_client, org2_user.headers, table_id,
            {"who": "org2", "organization_id": org2_org},
        )
        assert br.status_code == 201, br.text

        # Alice queries — sees ONLY her org's row
        aq = _query(e2e_client, alice_user.headers, table_id).json()["documents"]
        assert len(aq) == 1, f"alice should see 1 row, saw {len(aq)}: {aq}"
        assert aq[0]["data"]["who"] == "alice"

        # org2_user queries — sees ONLY org2's row
        bq = _query(e2e_client, org2_user.headers, table_id).json()["documents"]
        assert len(bq) == 1, f"org2_user should see 1 row, saw {len(bq)}: {bq}"
        assert bq[0]["data"]["who"] == "org2"

        # Alice cannot insert a row claiming org2 — create-time policy denies
        cross = _insert(
            e2e_client, alice_user.headers, table_id,
            {"who": "alice_lying", "organization_id": org2_org},
        )
        assert cross.status_code == 403, (
            f"alice should not be able to forge a row for org2; got {cross.text}"
        )

    def test_single_doc_get_enforces_policy(
        self, e2e_client, platform_admin, alice_user, bob_user
    ):
        """A row filtered out of the LIST must also be unreachable via
        GET /tables/{id}/documents/{doc_id}.

        Security boundary: same policy gate on both endpoints. If the single-doc
        handler skipped the policy check, an attacker who guesses or otherwise
        learns a doc_id could read across the row-level boundary.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"singleget_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "own_row_read",
                "actions": ["read", "create"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
        ]})

        # Bob inserts — this row is filtered out for alice in any list query.
        br = _insert(e2e_client, bob_user.headers, table_id, {"secret": "bobs"})
        assert br.status_code == 201, br.text
        bob_doc_id = br.json()["id"]

        # Sanity: alice's list query does NOT include bob's row.
        aq = _query(e2e_client, alice_user.headers, table_id).json()["documents"]
        assert all(d["id"] != bob_doc_id for d in aq), aq

        # Direct single-doc GET MUST be blocked. The handler returns 403 via
        # `_check_action_or_403` (the row is fetched first, then the policy
        # check runs against it — see api/src/routers/tables.py::get_document).
        r = e2e_client.get(
            f"/api/tables/{table_id}/documents/{bob_doc_id}",
            headers=alice_user.headers,
        )
        assert r.status_code == 403, (
            f"single-doc GET must apply read policy; got {r.status_code} "
            f"body={r.text}"
        )

    def test_additive_or_across_multiple_policies(
        self, e2e_client, platform_admin, alice_user, bob_user
    ):
        """Two read rules compose with OR semantics: alice should see a row
        owned by Bob if she has the role granted by the second rule.

        Security boundary inverse: removing the role assignment must collapse
        her view back to the own_row rule alone. Tests the OR composition that
        users will actually rely on for "self OR support team" patterns.
        """
        # 1. Create a role and assign Alice to it.
        role_resp = e2e_client.post(
            "/api/roles", headers=platform_admin.headers,
            json={"name": f"or_role_{uuid.uuid4().hex[:6]}", "description": "OR test role"},
        )
        assert role_resp.status_code == 201, role_resp.text
        role_id = role_resp.json()["id"]
        e2e_client.post(
            f"/api/roles/{role_id}/users", headers=platform_admin.headers,
            json={"user_ids": [str(alice_user.user_id)]},
        )

        table_id = _create_table(
            e2e_client, platform_admin.headers, f"or_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "everyone_create",
                "actions": ["create"],
                "when": None,
            },
            {
                "name": "own_row_read",
                "actions": ["read"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
            {
                "name": "support_role_read",
                "actions": ["read"],
                "when": {"call": "has_role", "args": [role_id]},
            },
        ]})

        # Bob inserts a row Alice does NOT own.
        br = _insert(e2e_client, bob_user.headers, table_id, {"who": "bobs_row"})
        assert br.status_code == 201, br.text
        bob_doc_id = br.json()["id"]

        # Alice queries — she has the role, so support_role_read OR own_row_read
        # grants visibility on Bob's row even though created_by != alice.
        aq = _query(e2e_client, alice_user.headers, table_id).json()["documents"]
        assert any(d["id"] == bob_doc_id for d in aq), (
            f"OR-composed policy failed: alice (with support role) cannot see "
            f"bob's row. saw: {aq}"
        )

    def test_delete_action_independent_of_update(
        self, e2e_client, platform_admin, alice_user
    ):
        """``actions: [read, update]`` on the own_row rule MUST NOT imply delete.

        Security boundary: each action is evaluated independently. If the
        evaluator ever folded delete into a generic "write" check, a
        permission-by-mistake would silently expand alice's authority on every
        table that names update without delete.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"deltest_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                # Note: NO "delete" in the actions list.
                "name": "own_row_no_delete",
                "actions": ["read", "create", "update"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
        ]})

        # Alice inserts and confirms read+update are allowed
        ins = _insert(e2e_client, alice_user.headers, table_id, {"v": "hers"})
        assert ins.status_code == 201, ins.text
        doc_id = ins.json()["id"]

        upd = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=alice_user.headers,
            json={"data": {"v": "edited"}},
        )
        assert upd.status_code == 200, upd.text

        # Alice tries to DELETE → must be denied even though she owns the row.
        dele = e2e_client.delete(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=alice_user.headers,
        )
        assert dele.status_code == 403, (
            f"delete must be gated independently of update; got {dele.status_code}"
        )

    def test_update_gates_on_pre_image_only(
        self, e2e_client, platform_admin, alice_user
    ):
        """OBSERVED BEHAVIOR (not aspirational): the PATCH handler runs the
        ``update`` policy check against the PRE-update row only.

        See ``api/src/routers/tables.py::update_document``: ``old_row`` is
        passed to ``_check_action_or_403("update", ...)``; the post-image is
        not re-checked. As a consequence, a user with update permission on a
        row they own can mutate it into a row that ``read`` would no longer
        permit them to see. The user then loses visibility of their own row.

        This test pins that behavior. If the handler ever starts re-checking
        the post-image, this test must be updated and the change advertised
        — the new semantics may break apps that rely on "self-hide on close."
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"posthide_{uuid.uuid4().hex[:8]}",
        )
        # Read+update gated on created_by==self. Post-update, alice can flip
        # `created_by` in the JSONB data — but the policy resolves
        # `row.created_by` to the COLUMN, not the JSONB. So actually the
        # COLUMN-level created_by stays alice's, the policy still matches,
        # and the row remains visible. The drift surface this test pins is
        # different: alice can mutate `data.status` such that a hypothetical
        # "read where status==open" policy would hide it post-update — but
        # the update was already authorized, and the row is now invisible to
        # her on subsequent reads.
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "owner_open_only",
                "actions": ["read", "create", "update"],
                "when": {
                    "and": [
                        {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
                        {"eq": [{"row": "status"}, "open"]},
                    ]
                },
            },
        ]})

        # Alice creates an open row she can read.
        ar = _insert(
            e2e_client, alice_user.headers, table_id,
            {"status": "open", "v": "hers"},
        )
        assert ar.status_code == 201, ar.text
        doc_id = ar.json()["id"]

        # Sanity: alice sees the row via list.
        aq = _query(e2e_client, alice_user.headers, table_id).json()["documents"]
        assert any(d["id"] == doc_id for d in aq)

        # Alice flips status to "closed" — pre-image had status==open and
        # created_by==alice, so the update is authorized and succeeds.
        upd = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{doc_id}",
            headers=alice_user.headers,
            json={"data": {"status": "closed", "v": "hers"}},
        )
        assert upd.status_code == 200, upd.text

        # Post-update, the row no longer satisfies `status==open`, so alice's
        # read filter excludes it. She has just made one of her own rows
        # invisible to herself. This is the observed semantics — the test
        # exists to lock it in, not endorse it.
        aq2 = _query(e2e_client, alice_user.headers, table_id).json()["documents"]
        assert all(d["id"] != doc_id for d in aq2), (
            f"post-update read filter should now exclude doc {doc_id}; saw {aq2}"
        )

        # Admin can still see the row (admin_bypass) — the row was not deleted.
        admin_q = _query(
            e2e_client, platform_admin.headers, table_id,
        ).json()["documents"]
        assert any(d["id"] == doc_id for d in admin_q), (
            f"admin should still see the closed row; saw {admin_q}"
        )

    def test_cannot_patch_to_make_visible_to_self(
        self, e2e_client, platform_admin, alice_user
    ):
        """Pre-update gating: alice cannot PATCH a row she cannot read into a
        row she could read.

        Concretely: a row with ``created_by=admin`` is invisible to alice under
        ``own_row``. She tries to PATCH that row's data — the handler fetches
        the OLD row, runs ``_check_action_or_403("update", old_row, ...)``,
        which fails because no rule grants alice update on a row she does not
        own. She cannot mutate the row to flip ownership.

        Security boundary: prevents "write-via-update" exfiltration where
        unauthorized writes would otherwise mutate a row to satisfy a read
        policy on the post-image.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"flip_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "own_row_full",
                "actions": ["read", "create", "update", "delete"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
        ]})

        # Admin creates a row alice does NOT own. created_by is auto-stamped
        # to the admin's user id by the handler.
        ar = _insert(e2e_client, platform_admin.headers, table_id, {"v": "secret"})
        assert ar.status_code == 201, ar.text
        admin_doc_id = ar.json()["id"]

        # Alice tries to PATCH it — pre-update row's created_by is admin, so
        # own_row_full does not match her, and she has no other update rule.
        # The fact that she might claim her own user_id in the patch body is
        # irrelevant: PATCH gates on the PRE-image only.
        flip = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{admin_doc_id}",
            headers=alice_user.headers,
            json={"data": {"created_by": str(alice_user.user_id), "v": "flipped"}},
        )
        assert flip.status_code == 403, (
            f"PATCH must gate on pre-image; got {flip.status_code} body={flip.text}"
        )

        # Sanity: the row was NOT mutated. Admin re-reads via list (admin_bypass).
        admin_q = _query(
            e2e_client, platform_admin.headers, table_id,
        ).json()["documents"]
        target = next((d for d in admin_q if d["id"] == admin_doc_id), None)
        assert target is not None, admin_q
        assert target["data"].get("v") == "secret", (
            f"row should be untouched; got {target['data']}"
        )
        # Tightened post-state check: nothing from alice's PATCH leaked into
        # ``data`` — not the forged ``created_by`` field, not the new ``v``,
        # nothing. The only key in the original insert was ``v: secret``;
        # any extra key here would mean the denial wrote partial state.
        assert target["data"] == {"v": "secret"}, (
            f"PATCH body leaked into row data despite 403; got {target['data']!r}"
        )
        assert "created_by" not in target["data"], (
            f"forged created_by leaked into row data: {target['data']!r}"
        )

    def test_batch_all_or_nothing(self, e2e_client, platform_admin, alice_user):
        """Batch insert: any single denial rejects the whole batch (transactional)."""
        table_id = _create_table(e2e_client, platform_admin.headers, f"batch_{uuid.uuid4().hex[:8]}")
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "creator_must_be_self",
                "actions": ["create"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
        ]})
        # Alice tries to batch-insert; one of the rows would silently bypass (the policy is OK
        # for both rows since created_by is auto-stamped). This should succeed.
        # Validate the batch endpoint shape doesn't leak policy names on denial: we test
        # denial by using an explicit policies block that has no create rule for non-admins.
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            }
        ]})
        # Alice tries to batch insert without create permission → 403
        body = {"documents": [{"data": {"x": 1}}, {"data": {"x": 2}}]}
        r = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch", headers=alice_user.headers, json=body
        )
        assert r.status_code == 403, r.text
        # Response includes denied row indices but NOT policy names
        body = r.json()
        if isinstance(body.get("detail"), dict):
            assert "denied_row_indices" in body["detail"]
            # No mention of policy "name" leaks
            assert "name" not in str(body["detail"])

    def test_cross_org_isolation_blocks_update(
        self, e2e_client, platform_admin, alice_user, org2_user
    ):
        """Alice (org1) cannot UPDATE a doc whose data.organization_id == org2.

        Security boundary: same row-level org policy that gates READ also
        gates UPDATE. The handler runs ``_check_action_or_403("update", ...)``
        against the PRE-image; alice is from org1, the row carries org2's id,
        so own_org_full does NOT match her — even though both users are on
        the same global table.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"orgupd_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "own_org_full",
                "actions": ["read", "create", "update", "delete"],
                "when": {
                    "eq": [
                        {"row": "organization_id"},
                        {"user": "organization_id"},
                    ]
                },
            },
        ]})

        org2_org = str(org2_user.organization_id)
        alice_org = str(alice_user.organization_id)
        assert alice_org != org2_org, "fixture sanity: orgs must differ"

        # org2 inserts a row carrying its org id
        ins = _insert(
            e2e_client, org2_user.headers, table_id,
            {"who": "org2", "secret": "org2-secret", "organization_id": org2_org},
        )
        assert ins.status_code == 201, ins.text
        org2_doc_id = ins.json()["id"]

        # Alice (org1) tries to PATCH org2's row — denied at pre-image check.
        flip = e2e_client.patch(
            f"/api/tables/{table_id}/documents/{org2_doc_id}",
            headers=alice_user.headers,
            json={"data": {"secret": "alice-pwned", "organization_id": org2_org}},
        )
        assert flip.status_code == 403, (
            f"cross-org UPDATE must be denied; got {flip.status_code} body={flip.text}"
        )

        # Side-effect check: admin reads the row and confirms it's untouched.
        admin_q = _query(
            e2e_client, platform_admin.headers, table_id,
        ).json()["documents"]
        target = next((d for d in admin_q if d["id"] == org2_doc_id), None)
        assert target is not None, admin_q
        assert target["data"].get("secret") == "org2-secret", (
            f"row mutated by cross-org PATCH; got {target['data']!r}"
        )
        assert target["data"].get("who") == "org2"

    def test_cross_org_isolation_blocks_delete(
        self, e2e_client, platform_admin, alice_user, org2_user
    ):
        """Alice (org1) cannot DELETE a doc whose data.organization_id == org2.

        Security boundary: row-level org isolation also covers delete. Without
        this test, a delete-only regression would not be caught by the
        existing READ/CREATE org-isolation test.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"orgdel_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "own_org_full",
                "actions": ["read", "create", "update", "delete"],
                "when": {
                    "eq": [
                        {"row": "organization_id"},
                        {"user": "organization_id"},
                    ]
                },
            },
        ]})

        org2_org = str(org2_user.organization_id)

        # org2 inserts a row carrying its org id
        ins = _insert(
            e2e_client, org2_user.headers, table_id,
            {"who": "org2", "organization_id": org2_org},
        )
        assert ins.status_code == 201, ins.text
        org2_doc_id = ins.json()["id"]

        # Alice (org1) tries to DELETE org2's row — denied.
        dele = e2e_client.delete(
            f"/api/tables/{table_id}/documents/{org2_doc_id}",
            headers=alice_user.headers,
        )
        assert dele.status_code == 403, (
            f"cross-org DELETE must be denied; got {dele.status_code} body={dele.text}"
        )

        # Side-effect check: admin verifies the row is still there.
        admin_q = _query(
            e2e_client, platform_admin.headers, table_id,
        ).json()["documents"]
        assert any(d["id"] == org2_doc_id for d in admin_q), (
            f"row missing after denied delete; admin sees {admin_q}"
        )

    def test_count_documents_enforces_policy(
        self, e2e_client, platform_admin, alice_user
    ):
        """``GET /tables/{id}/documents/count`` applies the same read filter
        as query — no count leak.

        Security boundary: a count endpoint that bypassed the read filter
        would leak existence/cardinality of private rows. Specifically, if
        the handler ever stops calling ``compile_read_filter`` and short-
        circuiting on ``read_filter is None`` to 0, alice would see admin's
        private row count.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"count_{uuid.uuid4().hex[:8]}",
        )
        # Default seeded admin_bypass — only admin can read; alice has no rule.
        # Admin inserts 3 rows.
        for i in range(3):
            r = _insert(e2e_client, platform_admin.headers, table_id, {"i": i})
            assert r.status_code == 201, r.text

        # Admin's count == 3 (sanity)
        ar = e2e_client.get(
            f"/api/tables/{table_id}/documents/count",
            headers=platform_admin.headers,
        )
        assert ar.status_code == 200, ar.text
        assert ar.json()["count"] == 3, ar.json()

        # Alice's count == 0 — count_documents short-circuits when no rule
        # grants read; the handler returns DocumentCountResponse(count=0)
        # without running an unfiltered SQL count.
        br = e2e_client.get(
            f"/api/tables/{table_id}/documents/count",
            headers=alice_user.headers,
        )
        assert br.status_code == 200, br.text
        assert br.json()["count"] == 0, (
            f"count_documents leaked private row count: {br.json()}"
        )

    def test_batch_delete_all_or_nothing(
        self, e2e_client, platform_admin, alice_user, bob_user
    ):
        """``POST /tables/{id}/documents/batch-delete`` denies the whole batch
        if ANY row fails its delete policy. Neither row is removed.

        Security boundary: the pre-flight loop in batch_delete_documents must
        check every row before deleting any. A regression that ran deletes in
        the same loop as the policy check would leave partial state (alice's
        row gone, bob's row still there) on a 403.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"bdel_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                # everyone can read so admin/alice/bob can list + verify
                "name": "everyone_read",
                "actions": ["read", "create"],
                "when": None,
            },
            {
                "name": "own_row_delete",
                "actions": ["delete"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
        ]})

        # Alice and Bob each insert a row.
        ar = _insert(e2e_client, alice_user.headers, table_id, {"who": "alice"})
        assert ar.status_code == 201, ar.text
        alice_doc_id = ar.json()["id"]
        br = _insert(e2e_client, bob_user.headers, table_id, {"who": "bob"})
        assert br.status_code == 201, br.text
        bob_doc_id = br.json()["id"]

        # Alice batch-deletes BOTH ids. own_row_delete grants alice on her
        # row but NOT bob's → denied row index = 1, whole batch aborts.
        r = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch-delete",
            headers=alice_user.headers,
            json={"ids": [alice_doc_id, bob_doc_id]},
        )
        assert r.status_code == 403, r.text
        body = r.json()
        if isinstance(body.get("detail"), dict):
            assert body["detail"].get("denied_row_indices") == [1], body

        # Side-effect check: admin lists the table — BOTH rows still exist.
        admin_q = _query(
            e2e_client, platform_admin.headers, table_id,
        ).json()["documents"]
        ids_present = {d["id"] for d in admin_q}
        assert alice_doc_id in ids_present, (
            f"alice's row was deleted on a denied batch (partial state leak); "
            f"admin sees {admin_q}"
        )
        assert bob_doc_id in ids_present, (
            f"bob's row missing after denied batch; admin sees {admin_q}"
        )

    def test_additive_or_negative_arm_without_role(
        self, e2e_client, platform_admin, alice_user, bob_user
    ):
        """Without the role assignment, alice does NOT see bob's row.

        Inverse of ``test_additive_or_across_multiple_policies``: proves
        ``support_role_read`` is the SOLE grant on bob's row, not a phantom
        always-true that happens to be true for alice. If ``has_role`` ever
        evaluates to True without the role assignment (e.g. a regression
        treating an unknown role as a no-op), this test catches it.
        """
        # Create the role but DO NOT assign alice to it.
        role_resp = e2e_client.post(
            "/api/roles", headers=platform_admin.headers,
            json={"name": f"or_neg_{uuid.uuid4().hex[:6]}", "description": "neg arm"},
        )
        assert role_resp.status_code == 201, role_resp.text
        role_id = role_resp.json()["id"]
        # NOTE: deliberately omitting the POST /api/roles/{id}/users step.

        table_id = _create_table(
            e2e_client, platform_admin.headers, f"orneg_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                "name": "everyone_create",
                "actions": ["create"],
                "when": None,
            },
            {
                "name": "own_row_read",
                "actions": ["read"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            },
            {
                "name": "support_role_read",
                "actions": ["read"],
                "when": {"call": "has_role", "args": [role_id]},
            },
        ]})

        # Bob inserts; alice does NOT own it.
        br = _insert(e2e_client, bob_user.headers, table_id, {"who": "bobs_row"})
        assert br.status_code == 201, br.text
        bob_doc_id = br.json()["id"]

        # Alice queries — own_row fails (not hers), support_role fails (no
        # role assignment). She must NOT see bob's row.
        aq = _query(e2e_client, alice_user.headers, table_id).json()["documents"]
        assert all(d["id"] != bob_doc_id for d in aq), (
            f"alice (NO role) saw bob's row — has_role returned true without "
            f"the role assignment: {aq}"
        )

        # Admin still sees the row (admin_bypass) — sanity check it exists.
        admin_q = _query(
            e2e_client, platform_admin.headers, table_id,
        ).json()["documents"]
        assert any(d["id"] == bob_doc_id for d in admin_q), admin_q

    def test_upsert_existing_id_gates_on_update_action(
        self, e2e_client, platform_admin, alice_user
    ):
        """``POST /tables/{id}/documents`` with ``upsert=True`` against an
        existing id runs the UPDATE policy check, not CREATE. A user with
        create-only access cannot upsert-update.

        Security boundary: see ``insert_document`` in
        ``api/src/routers/tables.py`` — the upsert branch fetches the
        existing row and calls ``_check_action_or_403("update", ...)`` on
        the pre-image. If that gate were removed, a "create-only" grant
        would silently allow row mutation.
        """
        table_id = _create_table(
            e2e_client, platform_admin.headers,
            f"upsert_{uuid.uuid4().hex[:8]}",
        )
        _set_policies(e2e_client, platform_admin.headers, table_id, {"policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
            {
                # alice is granted CREATE only — no update, no read.
                "name": "alice_create_only",
                "actions": ["create"],
                "when": None,
            },
        ]})

        # Admin pre-creates a row at a known id.
        admin_doc_id = str(uuid.uuid4())
        cr = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"id": admin_doc_id, "data": {"v": "admin-original"}},
        )
        assert cr.status_code == 201, cr.text

        # Alice POSTs with upsert=True at the same id → update path runs;
        # alice has no update grant → 403.
        flip = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=alice_user.headers,
            json={
                "id": admin_doc_id,
                "upsert": True,
                "data": {"v": "alice-pwned"},
            },
        )
        assert flip.status_code == 403, (
            f"upsert against existing id must gate on UPDATE; "
            f"got {flip.status_code} body={flip.text}"
        )

        # Side-effect check: admin reads the row, confirms unchanged.
        admin_q = _query(
            e2e_client, platform_admin.headers, table_id,
        ).json()["documents"]
        target = next((d for d in admin_q if d["id"] == admin_doc_id), None)
        assert target is not None, admin_q
        assert target["data"].get("v") == "admin-original", (
            f"row mutated via upsert despite update being denied; "
            f"got {target['data']!r}"
        )
