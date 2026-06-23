"""Fail-closed policy $ref validation in solution deploy.

A solution bundle whose table `policies` contains a {"$ref": "nonexistent_rule"}
must FAIL (deploy errors, entity not installed) rather than silently storing an
unresolvable reference.

A bundle that references an existing rule (built-in admin_bypass) must SUCCEED
and the table must be accessible.
"""
from __future__ import annotations

import uuid

import pytest

from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": None},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_deploy_table_with_unresolvable_ref_fails_closed(e2e_client, platform_admin):
    """Deploy a bundle whose table policies include {"$ref": "does_not_exist"} must fail.

    The deploy must NOT silently install a table with an unresolvable policy
    reference — the bundle must be rejected with a clear error.
    """
    headers = platform_admin.headers
    slug = f"ref-fail-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    tid = str(uuid.uuid4())

    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "tables": [
                {
                    "id": tid,
                    "name": f"ref_fail_{slug.replace('-', '_')}",
                    "schema": {"columns": [{"name": "data"}]},
                    # Reference a rule that does NOT exist in any scope tier.
                    "policies": [{"$ref": "does_not_exist_xyz_abc"}],
                }
            ],
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)

    # Must fail — an unresolvable $ref is a hard error.
    assert dep.status_code in (409, 422), (
        f"Expected deploy failure but got {dep.status_code}: {dep.text}"
    )

    # Confirm the table was NOT installed (entity must not exist).
    real_id = str(solution_entity_id(uuid.UUID(sid), uuid.UUID(tid)))
    tbl_r = e2e_client.get(f"/api/tables/{real_id}", headers=headers)
    # 404 — the table must not have been created.
    assert tbl_r.status_code == 404, (
        f"Table should not exist after failed deploy, got {tbl_r.status_code}: {tbl_r.text}"
    )


def test_deploy_table_with_builtin_ref_succeeds(e2e_client, platform_admin):
    """Deploy a bundle whose table policies reference the built-in admin_bypass rule.

    The built-in rule exists at startup; the deploy must succeed and the table
    must be accessible. Crucially, the PERSISTED table.access must still carry
    the {"$ref": "admin_bypass"} form (not the inlined policy) — resolving for
    validation must NOT corrupt the stored document.
    """
    headers = platform_admin.headers
    slug = f"ref-ok-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    tid = str(uuid.uuid4())
    table_name = f"ref_ok_{slug.replace('-', '_')}"

    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "tables": [
                {
                    "id": tid,
                    "name": table_name,
                    "schema": {"columns": [{"name": "data"}]},
                    # Reference the built-in admin_bypass table rule.
                    "policies": [{"$ref": "admin_bypass"}],
                }
            ],
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), (
        f"Deploy with built-in ref should succeed but got {dep.status_code}: {dep.json()}"
    )
    deploy_result = dep.json()
    assert deploy_result.get("tables_upserted", 0) == 1, (
        f"Expected 1 table upserted, got: {deploy_result}"
    )

    # Table is accessible and policy-loads cleanly (document count uses policy resolution).
    real_id = str(solution_entity_id(uuid.UUID(sid), uuid.UUID(tid)))
    count_r = e2e_client.get(f"/api/tables/{real_id}/documents/count", headers=headers)
    assert count_r.status_code == 200, (
        f"Table should be accessible after deploy with builtin ref, got {count_r.status_code}: {count_r.text}"
    )

    # Verify the stored access still carries the $ref form (not the inlined policy).
    # TablePublic serializes the access JSONB under the key "policies" (not "access").
    # PolicyRuleRef has ref=Field(alias="$ref"), so the API returns {"$ref": name}.
    tbl_r = e2e_client.get(f"/api/tables/{real_id}", headers=headers)
    assert tbl_r.status_code == 200, tbl_r.text
    tbl_data = tbl_r.json()
    stored_policies = (tbl_data.get("policies") or {}).get("policies", [])
    # The stored document must contain a $ref entry, not an expanded inline policy.
    assert any("$ref" in p for p in stored_policies), (
        f"Stored access should preserve $ref form but got: {stored_policies} "
        f"(full table response: {tbl_data})"
    )
