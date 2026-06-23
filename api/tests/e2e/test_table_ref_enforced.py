"""E2E test: table policy that uses a $ref to a PolicyRule is enforced correctly.

The resolving loader (load_resolved_table_policies) must inline the ref before
evaluation so that `preresolve_for_policies` / `evaluate_action` never see a
bare PolicyRuleRef (which has no `.when` attribute).

Flow:
1. Inject a PolicyRule via db_session (committed so the running API can see it).
2. Create a table via HTTP with a policies list containing a single {"$ref": <rule>}.
3. Insert a document via admin (must succeed: the referenced rule grants all actions
   to platform admins).
4. Query the table via admin → rows returned (resolution worked).
5. Clean up: delete the seeded rule so other tests don't see it.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.policy_rule import PolicyRule

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_table_list_allowed_via_referenced_rule(
    db_session,
    e2e_client,
    platform_admin,
):
    """A table whose access is a $ref to a PolicyRule allows rows for matching users.

    This is the critical regression guard for Task 5: if $refs aren't resolved
    before `preresolve_for_policies`, the test will either ERROR (AttributeError
    on `.when`) or return an empty result (ref treated as deny because it has no
    `.actions`).
    """
    rule_name = f"ref_admin_{uuid.uuid4().hex[:8]}"

    # Seed the named rule directly — no REST endpoint exists yet (Task 6).
    # Commit so the running API can read it in its own DB session.
    rule = PolicyRule(
        name=rule_name,
        domain="table",
        organization_id=None,  # global rule — visible to all orgs
        body={
            "actions": ["read", "create", "update", "delete"],
            "when": {"user": "is_platform_admin"},
        },
    )
    db_session.add(rule)
    await db_session.commit()

    try:
        # Create a table whose entire policy set is a reference to the global rule.
        table_name = f"ref_table_{uuid.uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "description": "ref-policy test table",
                "organization_id": None,  # global table
                "policies": {"policies": [{"$ref": rule_name}]},
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        table_id = create_resp.json()["id"]

        # Insert a document as admin.
        insert_resp = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"data": {"x": 42}},
        )
        assert insert_resp.status_code == 201, (
            f"insert failed (ref rule not granting create?): {insert_resp.text}"
        )

        # Query: the referenced rule grants read to platform admins → rows visible.
        query_resp = e2e_client.post(
            f"/api/tables/{table_id}/documents/query",
            headers=platform_admin.headers,
            json={},
        )
        assert query_resp.status_code == 200, query_resp.text
        docs = query_resp.json()["documents"]
        assert len(docs) == 1, (
            f"expected 1 document (ref rule resolved), got {len(docs)}. "
            f"An empty result means the ref was NOT inlined before evaluation."
        )
        assert docs[0]["data"]["x"] == 42

    finally:
        # Clean up the global rule so it doesn't affect other tests.
        await db_session.delete(rule)
        await db_session.commit()
