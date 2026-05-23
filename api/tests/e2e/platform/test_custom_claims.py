"""End-to-end coverage for Custom Claims."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser


@pytest.fixture
def org_admin(e2e_client, platform_admin, org1) -> E2EUser:
    """Create an org-bound superuser so Custom Claims have org context."""
    suffix = uuid4().hex[:8]
    user = E2EUser(
        email=f"claims-admin-{suffix}@gobifrost.dev",
        password="ClaimsAdminPass123!",
        name=f"Claims Admin {suffix}",
    )
    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org1["id"],
            "is_superuser": True,
        },
    )
    assert response.status_code == 201, response.text
    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    assert user.is_superuser
    user.organization_id = org1["id"]
    return user


def _create_table(e2e_client, headers, name: str, org_id: str, policies=None) -> str:
    body = {
        "name": name,
        "description": "custom claims e2e table",
        "organization_id": org_id,
    }
    if policies is not None:
        body["policies"] = policies
    response = e2e_client.post("/api/tables", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _insert(e2e_client, headers, table_id: str, data: dict) -> str:
    response = e2e_client.post(
        f"/api/tables/{table_id}/documents",
        headers=headers,
        json={"data": data},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _query(e2e_client, headers, table_id: str) -> list[dict]:
    response = e2e_client.post(
        f"/api/tables/{table_id}/documents/query",
        headers=headers,
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()["documents"]


def _admin_bypass_policies() -> dict:
    return {
        "policies": [
            {
                "name": "admin_bypass",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            }
        ]
    }


def _claim_policy(claim_name: str) -> dict:
    return {
        "policies": [
            *_admin_bypass_policies()["policies"],
            {
                "name": "claim_scoped_read",
                "actions": ["read"],
                "when": {"in": [{"row": "campus_id"}, {"claims": claim_name}]},
            },
        ]
    }


def _own_row_policies() -> dict:
    """Source-table policies that grant each user read on their own membership row.

    Required for claims to be resolvable as non-admin users: the resolver
    intersects the source-table read filter, so the caller must be able to
    read the rows the claim selects from.
    """
    return {
        "policies": [
            *_admin_bypass_policies()["policies"],
            {
                "name": "own_row_read",
                "actions": ["read"],
                "when": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
            },
        ]
    }


def _create_claim_source(
    e2e_client,
    org_admin: E2EUser,
    org1: dict,
    *,
    policies: dict | None = None,
) -> tuple[str, str]:
    table_name = f"claim_memberships_{uuid4().hex[:8]}"
    if policies is None:
        policies = _own_row_policies()
    table_id = _create_table(
        e2e_client, org_admin.headers, table_name, org1["id"], policies=policies
    )
    return table_name, table_id


@pytest.mark.e2e
class TestCustomClaims:
    def test_admin_can_crud_claims(self, e2e_client, org_admin, org1):
        source_table, _source_id = _create_claim_source(e2e_client, org_admin, org1)
        claim_name = f"allowed_campus_ids_{uuid4().hex[:8]}"

        create = e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": claim_name,
                "type": "list",
                "query": {"table": source_table, "select": "campus_id"},
            },
        )
        assert create.status_code == 201, create.text
        assert create.json()["name"] == claim_name

        listed = e2e_client.get("/api/claims", headers=org_admin.headers)
        assert listed.status_code == 200, listed.text
        assert claim_name in {claim["name"] for claim in listed.json()["claims"]}

        fetched = e2e_client.get(f"/api/claims/{claim_name}", headers=org_admin.headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["query"]["table"] == source_table

        updated = e2e_client.patch(
            f"/api/claims/{claim_name}",
            headers=org_admin.headers,
            json={"description": "updated by e2e"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["description"] == "updated by e2e"

        deleted = e2e_client.delete(f"/api/claims/{claim_name}", headers=org_admin.headers)
        assert deleted.status_code == 204, deleted.text

    def test_unknown_claim_ref_in_query_rejected(self, e2e_client, org_admin, org1):
        """A claim whose query.where references an unknown claim is 422'd."""
        source_table, _ = _create_claim_source(e2e_client, org_admin, org1)
        response = e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": f"refs_missing_{uuid4().hex[:8]}",
                "type": "list",
                "query": {
                    "table": source_table,
                    "where": {"in": [{"row": "id"}, {"claims": "does_not_exist"}]},
                    "select": "campus_id",
                },
            },
        )
        assert response.status_code == 422, response.text
        assert "does_not_exist" in response.json()["detail"]["unknown"]

    def test_unknown_source_table_rejected(self, e2e_client, org_admin):
        response = e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": f"missing_source_{uuid4().hex[:8]}",
                "type": "list",
                "query": {"table": "does_not_exist", "select": "id"},
            },
        )
        assert response.status_code == 422, response.text

    def test_scoped_read_against_claim(self, e2e_client, org_admin, org1, alice_user, bob_user):
        source_table, source_id = _create_claim_source(e2e_client, org_admin, org1)
        claim_name = f"allowed_campus_ids_{uuid4().hex[:8]}"
        e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": claim_name,
                "type": "list",
                "query": {
                    "table": source_table,
                    "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                    "select": "campus_id",
                },
            },
        ).raise_for_status()

        _insert(
            e2e_client,
            org_admin.headers,
            source_id,
            {"user_id": str(alice_user.user_id), "campus_id": "c1"},
        )
        _insert(
            e2e_client,
            org_admin.headers,
            source_id,
            {"user_id": str(bob_user.user_id), "campus_id": "c2"},
        )

        docs_id = _create_table(
            e2e_client,
            org_admin.headers,
            f"claim_docs_{uuid4().hex[:8]}",
            org1["id"],
            policies=_claim_policy(claim_name),
        )
        _insert(e2e_client, org_admin.headers, docs_id, {"campus_id": "c1", "title": "alice"})
        _insert(e2e_client, org_admin.headers, docs_id, {"campus_id": "c2", "title": "bob"})
        _insert(e2e_client, org_admin.headers, docs_id, {"campus_id": "c3", "title": "hidden"})

        alice_rows = _query(e2e_client, alice_user.headers, docs_id)
        bob_rows = _query(e2e_client, bob_user.headers, docs_id)

        assert {row["data"]["title"] for row in alice_rows} == {"alice"}
        assert {row["data"]["title"] for row in bob_rows} == {"bob"}

    def test_delete_referenced_claim_refused(self, e2e_client, org_admin, org1):
        source_table, _source_id = _create_claim_source(e2e_client, org_admin, org1)
        claim_name = f"referenced_claim_{uuid4().hex[:8]}"
        e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": claim_name,
                "type": "list",
                "query": {"table": source_table, "select": "campus_id"},
            },
        ).raise_for_status()
        table_name = f"claim_refs_{uuid4().hex[:8]}"
        _create_table(
            e2e_client,
            org_admin.headers,
            table_name,
            org1["id"],
            policies=_claim_policy(claim_name),
        )

        response = e2e_client.delete(f"/api/claims/{claim_name}", headers=org_admin.headers)

        assert response.status_code == 409, response.text
        assert table_name in response.json()["detail"]["tables"]

    def test_claim_edit_reflected_in_next_request(
        self,
        e2e_client,
        org_admin,
        org1,
        alice_user,
    ):
        source_table, source_id = _create_claim_source(e2e_client, org_admin, org1)
        claim_name = f"editable_claim_{uuid4().hex[:8]}"
        e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": claim_name,
                "type": "list",
                "query": {
                    "table": source_table,
                    "where": {"eq": [{"row": "campus_id"}, "missing"]},
                    "select": "campus_id",
                },
            },
        ).raise_for_status()
        _insert(
            e2e_client,
            org_admin.headers,
            source_id,
            {"user_id": str(alice_user.user_id), "campus_id": "c1"},
        )
        docs_id = _create_table(
            e2e_client,
            org_admin.headers,
            f"claim_edit_docs_{uuid4().hex[:8]}",
            org1["id"],
            policies=_claim_policy(claim_name),
        )
        _insert(e2e_client, org_admin.headers, docs_id, {"campus_id": "c1", "title": "now-visible"})

        assert _query(e2e_client, alice_user.headers, docs_id) == []

        updated = e2e_client.patch(
            f"/api/claims/{claim_name}",
            headers=org_admin.headers,
            json={
                "query": {
                    "table": source_table,
                    "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                    "select": "campus_id",
                }
            },
        )
        assert updated.status_code == 200, updated.text

        rows = _query(e2e_client, alice_user.headers, docs_id)
        assert {row["data"]["title"] for row in rows} == {"now-visible"}

    def test_claim_does_not_bypass_source_table_policies(
        self,
        e2e_client,
        org_admin,
        org1,
        alice_user,
    ):
        """Regression: a claim's resolver MUST intersect with the source
        table's read filter, so a user who can't read a row directly cannot
        observe its column via a claim."""
        # Source table with NO read policy for non-admins.
        source_table = f"claim_locked_{uuid4().hex[:8]}"
        source_id = _create_table(
            e2e_client,
            org_admin.headers,
            source_table,
            org1["id"],
            policies=_admin_bypass_policies(),
        )
        _insert(
            e2e_client,
            org_admin.headers,
            source_id,
            {"user_id": str(alice_user.user_id), "campus_id": "c1"},
        )

        # Claim with a where that WOULD match alice's row if the policy were
        # bypassed. The fix should force the read filter to deny first.
        claim_name = f"locked_campus_ids_{uuid4().hex[:8]}"
        e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": claim_name,
                "type": "list",
                "query": {
                    "table": source_table,
                    "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                    "select": "campus_id",
                },
            },
        ).raise_for_status()

        docs_id = _create_table(
            e2e_client,
            org_admin.headers,
            f"locked_docs_{uuid4().hex[:8]}",
            org1["id"],
            policies=_claim_policy(claim_name),
        )
        _insert(e2e_client, org_admin.headers, docs_id, {"campus_id": "c1", "title": "should-stay-hidden"})

        # Alice can't read the source table → claim resolves to [] → no docs.
        assert _query(e2e_client, alice_user.headers, docs_id) == []

    def test_empty_claim_denies_explicitly(
        self,
        e2e_client,
        org_admin,
        org1,
        alice_user,
    ):
        """Empty claim resolution → IN ([]) → user sees zero rows even though
        rows match the row-level predicate's structural shape."""
        source_table, _ = _create_claim_source(e2e_client, org_admin, org1)
        # No membership row for alice → claim resolves to [].
        claim_name = f"empty_campus_ids_{uuid4().hex[:8]}"
        e2e_client.post(
            "/api/claims",
            headers=org_admin.headers,
            json={
                "name": claim_name,
                "type": "list",
                "query": {
                    "table": source_table,
                    "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                    "select": "campus_id",
                },
            },
        ).raise_for_status()

        docs_id = _create_table(
            e2e_client,
            org_admin.headers,
            f"empty_claim_docs_{uuid4().hex[:8]}",
            org1["id"],
            policies=_claim_policy(claim_name),
        )
        for campus in ("c1", "c2", "c3"):
            _insert(e2e_client, org_admin.headers, docs_id, {"campus_id": campus, "title": campus})

        assert _query(e2e_client, alice_user.headers, docs_id) == []

    def test_two_claim_and_scoped_read(
        self,
        e2e_client,
        org_admin,
        org1,
        alice_user,
        bob_user,
    ):
        """Two claims AND'd in a single policy: doc visible iff BOTH match.

        Alice sees campus c1 × doc_type d1 only. Bob sees campus c2 × d2 only.
        Cross-product rows (c1×d2, c2×d1) are visible to neither.
        """
        campus_src, campus_src_id = _create_claim_source(e2e_client, org_admin, org1)
        doctype_src, doctype_src_id = _create_claim_source(e2e_client, org_admin, org1)

        campus_claim = f"allowed_campus_ids_{uuid4().hex[:8]}"
        doctype_claim = f"allowed_doc_type_ids_{uuid4().hex[:8]}"
        for name, table in ((campus_claim, campus_src), (doctype_claim, doctype_src)):
            e2e_client.post(
                "/api/claims",
                headers=org_admin.headers,
                json={
                    "name": name,
                    "type": "list",
                    "query": {
                        "table": table,
                        "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                        "select": "campus_id" if table is campus_src else "doc_type_id",
                    },
                },
            ).raise_for_status()

        # alice: c1 + d1; bob: c2 + d2
        _insert(e2e_client, org_admin.headers, campus_src_id, {"user_id": str(alice_user.user_id), "campus_id": "c1"})
        _insert(e2e_client, org_admin.headers, campus_src_id, {"user_id": str(bob_user.user_id), "campus_id": "c2"})
        _insert(e2e_client, org_admin.headers, doctype_src_id, {"user_id": str(alice_user.user_id), "doc_type_id": "d1"})
        _insert(e2e_client, org_admin.headers, doctype_src_id, {"user_id": str(bob_user.user_id), "doc_type_id": "d2"})

        docs_id = _create_table(
            e2e_client,
            org_admin.headers,
            f"two_claim_docs_{uuid4().hex[:8]}",
            org1["id"],
            policies={
                "policies": [
                    *_admin_bypass_policies()["policies"],
                    {
                        "name": "scoped_read",
                        "actions": ["read"],
                        "when": {
                            "and": [
                                {"in": [{"row": "campus_id"}, {"claims": campus_claim}]},
                                {"in": [{"row": "doc_type_id"}, {"claims": doctype_claim}]},
                            ]
                        },
                    },
                ]
            },
        )
        # Seed the full 2×2 cross product.
        for campus in ("c1", "c2"):
            for doctype in ("d1", "d2"):
                _insert(
                    e2e_client,
                    org_admin.headers,
                    docs_id,
                    {"campus_id": campus, "doc_type_id": doctype, "title": f"{campus}-{doctype}"},
                )

        alice_titles = {row["data"]["title"] for row in _query(e2e_client, alice_user.headers, docs_id)}
        bob_titles = {row["data"]["title"] for row in _query(e2e_client, bob_user.headers, docs_id)}
        assert alice_titles == {"c1-d1"}
        assert bob_titles == {"c2-d2"}
