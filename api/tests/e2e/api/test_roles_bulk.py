"""
E2E for the new bulk-unassign endpoints on /api/roles/{id}/{users,forms,agents}.

The per-id DELETE paths still work (covered by existing tests). These tests
exercise the list-body DELETE forms added in Block 3a.
"""

from __future__ import annotations

import uuid

import pytest


def _create_role(e2e_client, headers, name_suffix: str) -> str:
    resp = e2e_client.post(
        "/api/roles",
        headers=headers,
        json={
            "name": f"BulkRole {name_suffix} {uuid.uuid4().hex[:6]}",
            "description": "bulk role",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_user(e2e_client, headers, org_id: str, prefix: str) -> str:
    resp = e2e_client.post(
        "/api/users",
        headers=headers,
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:6]}@bulkrole.gobifrost.dev",
            "name": f"{prefix}",
            "organization_id": org_id,
            "is_superuser": False,
            "invite": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_form(e2e_client, headers, name: str) -> str:
    resp = e2e_client.post(
        "/api/forms",
        headers=headers,
        json={
            "name": f"{name} {uuid.uuid4().hex[:6]}",
            "description": "bulk role form",
            "workflow_id": None,
            "form_schema": {"fields": []},
            "access_level": "role_based",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_agent(e2e_client, headers, name: str) -> str:
    resp = e2e_client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": f"{name} {uuid.uuid4().hex[:6]}",
            "description": "bulk role agent",
            "system_prompt": "Test",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.e2e
class TestBulkUnassignUsers:
    def test_bulk_unassign_users(self, e2e_client, platform_admin, org1):
        role = _create_role(e2e_client, platform_admin.headers, "UnassignU")
        u1 = _create_user(e2e_client, platform_admin.headers, org1["id"], "u1")
        u2 = _create_user(e2e_client, platform_admin.headers, org1["id"], "u2")
        u3 = _create_user(e2e_client, platform_admin.headers, org1["id"], "u3")

        assign = e2e_client.post(
            f"/api/roles/{role}/users",
            headers=platform_admin.headers,
            json={
                "user_ids": [u1, u2, u3],
                "boundaries": [
                    {
                        "boundary_kind": "organization",
                        "organization_id": org1["id"],
                    }
                ],
            },
        )
        assert assign.status_code == 204

        # Bulk unassign u1 + u2 in one call.
        resp = e2e_client.request(
            "DELETE",
            f"/api/roles/{role}/users",
            headers=platform_admin.headers,
            json={"user_ids": [u1, u2]},
        )
        assert resp.status_code == 204, resp.text

        remaining = e2e_client.get(
            f"/api/roles/{role}/users", headers=platform_admin.headers
        ).json()["user_ids"]
        assert u1 not in remaining
        assert u2 not in remaining
        assert u3 in remaining

    def test_bulk_unassign_empty_body_rejected(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "EmptyU")
        resp = e2e_client.request(
            "DELETE",
            f"/api/roles/{role}/users",
            headers=platform_admin.headers,
            json={"user_ids": []},
        )
        assert resp.status_code == 422, resp.text


@pytest.mark.e2e
class TestBulkUnassignForms:
    def test_assign_list_unassign_forms(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "AssignF")
        f1 = _create_form(e2e_client, platform_admin.headers, "RoleForm1")
        f2 = _create_form(e2e_client, platform_admin.headers, "RoleForm2")

        assign = e2e_client.post(
            f"/api/roles/{role}/forms",
            headers=platform_admin.headers,
            json={"form_ids": [f1, f2]},
        )
        assert assign.status_code == 204, assign.text

        assigned = e2e_client.get(
            f"/api/roles/{role}/forms", headers=platform_admin.headers
        )
        assert assigned.status_code == 200, assigned.text
        assert sorted(assigned.json()["form_ids"]) == sorted([f1, f2])

        resp = e2e_client.request(
            "DELETE",
            f"/api/roles/{role}/forms",
            headers=platform_admin.headers,
            json={"form_ids": [f1]},
        )
        assert resp.status_code == 204, resp.text

        remaining = e2e_client.get(
            f"/api/roles/{role}/forms", headers=platform_admin.headers
        ).json()["form_ids"]
        assert f1 not in remaining
        assert f2 in remaining

    def test_bulk_unassign_forms(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "UnassignF")
        f1 = _create_form(e2e_client, platform_admin.headers, "BulkF1")
        f2 = _create_form(e2e_client, platform_admin.headers, "BulkF2")

        e2e_client.post(
            f"/api/roles/{role}/forms",
            headers=platform_admin.headers,
            json={"form_ids": [f1, f2]},
        )

        resp = e2e_client.request(
            "DELETE",
            f"/api/roles/{role}/forms",
            headers=platform_admin.headers,
            json={"form_ids": [f1]},
        )
        assert resp.status_code == 204, resp.text

        remaining = e2e_client.get(
            f"/api/roles/{role}/forms", headers=platform_admin.headers
        ).json()["form_ids"]
        assert f1 not in remaining
        assert f2 in remaining


@pytest.mark.e2e
class TestBulkUnassignAgents:
    def test_assign_list_unassign_agents(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "AssignA")
        a1 = _create_agent(e2e_client, platform_admin.headers, "RoleAgent1")
        a2 = _create_agent(e2e_client, platform_admin.headers, "RoleAgent2")

        assign = e2e_client.post(
            f"/api/roles/{role}/agents",
            headers=platform_admin.headers,
            json={"agent_ids": [a1, a2]},
        )
        assert assign.status_code == 204, assign.text

        assigned = e2e_client.get(
            f"/api/roles/{role}/agents", headers=platform_admin.headers
        )
        assert assigned.status_code == 200, assigned.text
        assert sorted(assigned.json()["agent_ids"]) == sorted([a1, a2])

        resp = e2e_client.request(
            "DELETE",
            f"/api/roles/{role}/agents",
            headers=platform_admin.headers,
            json={"agent_ids": [a1, a2]},
        )
        assert resp.status_code == 204, resp.text

        remaining = e2e_client.get(
            f"/api/roles/{role}/agents", headers=platform_admin.headers
        ).json()["agent_ids"]
        assert a1 not in remaining
        assert a2 not in remaining

    def test_bulk_unassign_agents(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "UnassignA")
        a1 = _create_agent(e2e_client, platform_admin.headers, "BulkA1")
        a2 = _create_agent(e2e_client, platform_admin.headers, "BulkA2")

        e2e_client.post(
            f"/api/roles/{role}/agents",
            headers=platform_admin.headers,
            json={"agent_ids": [a1, a2]},
        )

        resp = e2e_client.request(
            "DELETE",
            f"/api/roles/{role}/agents",
            headers=platform_admin.headers,
            json={"agent_ids": [a1, a2]},
        )
        assert resp.status_code == 204, resp.text

        remaining = e2e_client.get(
            f"/api/roles/{role}/agents", headers=platform_admin.headers
        ).json()["agent_ids"]
        assert a1 not in remaining
        assert a2 not in remaining
