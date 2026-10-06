"""
E2E for the new role-consumer endpoints:

- /api/roles/{id}/apps         GET / POST / DELETE
- /api/roles/{id}/workflows    GET / POST / DELETE

Plus: GET /api/roles now includes consumer_counts per role.
"""

from __future__ import annotations

import uuid

import pytest


def _create_role(e2e_client, headers, suffix: str) -> str:
    resp = e2e_client.post(
        "/api/roles",
        headers=headers,
        json={
            "name": f"ConsumerRole {suffix} {uuid.uuid4().hex[:6]}",
            "description": "consumer-tab test",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_app(e2e_client, headers, name: str) -> str:
    slug = f"role-app-{uuid.uuid4().hex[:8]}"
    resp = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={"name": name, "slug": slug, "app_model": "inline_v1"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _make_workflow(e2e_client, headers, function_name: str) -> str:
    """Write a tiny workflow file + register it, return its DB id."""
    path = f"workflows/role_consumer_{uuid.uuid4().hex[:8]}.py"
    content = (
        "from bifrost.decorators import workflow\n\n"
        f"@workflow(name='{function_name}')\n"
        f"def {function_name}():\n"
        "    return {'ok': True}\n"
    )
    write = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": path, "content": content, "encoding": "utf-8"},
    )
    assert write.status_code in (200, 201), write.text

    reg = e2e_client.post(
        "/api/workflows/register",
        headers=headers,
        json={"path": path, "function_name": function_name},
    )
    if reg.status_code == 409:
        listing = e2e_client.get("/api/workflows", headers=headers).json()
        for w in listing:
            if w.get("function_name") == function_name:
                return w["id"]
        raise AssertionError("409 but workflow not found")
    assert reg.status_code in (200, 201), reg.text
    return reg.json()["id"]


# =============================================================================
# Apps
# =============================================================================


@pytest.mark.e2e
class TestRoleApps:
    def test_assign_list_unassign_apps(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "Apps")
        a1 = _create_app(e2e_client, platform_admin.headers, "BulkRoleApp1")
        a2 = _create_app(e2e_client, platform_admin.headers, "BulkRoleApp2")

        # Initial GET — empty.
        initial = e2e_client.get(
            f"/api/roles/{role}/apps", headers=platform_admin.headers
        )
        assert initial.status_code == 200
        assert initial.json() == {"app_ids": []}

        # Assign both.
        post = e2e_client.post(
            f"/api/roles/{role}/apps",
            headers=platform_admin.headers,
            json={"app_ids": [a1, a2]},
        )
        assert post.status_code == 204, post.text

        # GET reflects assignments.
        after = e2e_client.get(
            f"/api/roles/{role}/apps", headers=platform_admin.headers
        ).json()["app_ids"]
        assert sorted(after) == sorted([a1, a2])

        # Idempotent re-assign (no error, no dupes).
        again = e2e_client.post(
            f"/api/roles/{role}/apps",
            headers=platform_admin.headers,
            json={"app_ids": [a1]},
        )
        assert again.status_code == 204

        # Bulk unassign a1.
        dele = e2e_client.request(
            "DELETE",
            f"/api/roles/{role}/apps",
            headers=platform_admin.headers,
            json={"app_ids": [a1]},
        )
        assert dele.status_code == 204, dele.text

        final = e2e_client.get(
            f"/api/roles/{role}/apps", headers=platform_admin.headers
        ).json()["app_ids"]
        assert final == [a2]

    def test_assign_unknown_app_404(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "AppsMiss")
        fake = str(uuid.uuid4())
        resp = e2e_client.post(
            f"/api/roles/{role}/apps",
            headers=platform_admin.headers,
            json={"app_ids": [fake]},
        )
        assert resp.status_code == 404


# =============================================================================
# Workflows
# =============================================================================


@pytest.mark.e2e
class TestRoleWorkflows:
    def test_assign_list_unassign_workflows(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "WF")
        wf_name = f"role_wf_{uuid.uuid4().hex[:8]}"
        wf_id = _make_workflow(e2e_client, platform_admin.headers, wf_name)

        try:
            post = e2e_client.post(
                f"/api/roles/{role}/workflows",
                headers=platform_admin.headers,
                json={"workflow_ids": [wf_id]},
            )
            assert post.status_code == 204, post.text

            after = e2e_client.get(
                f"/api/roles/{role}/workflows", headers=platform_admin.headers
            ).json()["workflow_ids"]
            assert wf_id in after

            dele = e2e_client.request(
                "DELETE",
                f"/api/roles/{role}/workflows",
                headers=platform_admin.headers,
                json={"workflow_ids": [wf_id]},
            )
            assert dele.status_code == 204, dele.text

            final = e2e_client.get(
                f"/api/roles/{role}/workflows", headers=platform_admin.headers
            ).json()["workflow_ids"]
            assert wf_id not in final
        finally:
            # Stale workflow→role links survive across the whole session
            # otherwise — `test_cli_import` exports every workflow with its
            # role membership and rejects dangling refs.
            e2e_client.delete(
                f"/api/roles/{role}", headers=platform_admin.headers
            )
            e2e_client.delete(
                f"/api/workflows/{wf_id}", headers=platform_admin.headers
            )

    def test_assign_unknown_workflow_404(self, e2e_client, platform_admin):
        role = _create_role(e2e_client, platform_admin.headers, "WFMiss")
        fake = str(uuid.uuid4())
        resp = e2e_client.post(
            f"/api/roles/{role}/workflows",
            headers=platform_admin.headers,
            json={"workflow_ids": [fake]},
        )
        assert resp.status_code == 404


# =============================================================================
# Inline consumer counts on GET /api/roles
# =============================================================================


@pytest.mark.e2e
class TestRoleConsumerCounts:
    def test_counts_reflect_assignments(
        self, e2e_client, platform_admin, org1
    ):
        role = _create_role(e2e_client, platform_admin.headers, "Counts")
        wf_id: str | None = None
        try:
            # Baseline — every count zero.
            roles_resp = e2e_client.get(
                "/api/roles", headers=platform_admin.headers
            ).json()
            match = next(r for r in roles_resp if r["id"] == role)
            assert match["consumer_counts"] == {
                "users": 0,
                "forms": 0,
                "agents": 0,
                "apps": 0,
                "workflows": 0,
            }

            # Add one of each type.
            user_id = e2e_client.post(
                "/api/users",
                headers=platform_admin.headers,
                json={
                    "email": f"counts-{uuid.uuid4().hex[:6]}@cnt.gobifrost.dev",
                    "name": "Counts user",
                    "organization_id": org1["id"],
                    "is_superuser": False,
                    "invite": False,
                },
            ).json()["id"]
            e2e_client.post(
                f"/api/roles/{role}/users",
                headers=platform_admin.headers,
                json={"user_ids": [user_id]},
            )

            form_id = e2e_client.post(
                "/api/forms",
                headers=platform_admin.headers,
                json={
                    "name": f"CntForm {uuid.uuid4().hex[:6]}",
                    "description": "",
                    "workflow_id": None,
                    "form_schema": {"fields": []},
                    "access_level": "role_based",
                },
            ).json()["id"]
            e2e_client.post(
                f"/api/roles/{role}/forms",
                headers=platform_admin.headers,
                json={"form_ids": [form_id]},
            )

            agent_id = e2e_client.post(
                "/api/agents",
                headers=platform_admin.headers,
                json={
                    "name": f"CntAgent {uuid.uuid4().hex[:6]}",
                    "description": "",
                    "system_prompt": "Test",
                    "channels": ["chat"],
                    "access_level": "authenticated",
                },
            ).json()["id"]
            e2e_client.post(
                f"/api/roles/{role}/agents",
                headers=platform_admin.headers,
                json={"agent_ids": [agent_id]},
            )

            app_id = _create_app(e2e_client, platform_admin.headers, "CntApp")
            e2e_client.post(
                f"/api/roles/{role}/apps",
                headers=platform_admin.headers,
                json={"app_ids": [app_id]},
            )

            wf_id = _make_workflow(
                e2e_client, platform_admin.headers, f"cnt_wf_{uuid.uuid4().hex[:6]}"
            )
            e2e_client.post(
                f"/api/roles/{role}/workflows",
                headers=platform_admin.headers,
                json={"workflow_ids": [wf_id]},
            )

            # Now every count should be 1.
            roles_after = e2e_client.get(
                "/api/roles", headers=platform_admin.headers
            ).json()
            match2 = next(r for r in roles_after if r["id"] == role)
            assert match2["consumer_counts"] == {
                "users": 1,
                "forms": 1,
                "agents": 1,
                "apps": 1,
                "workflows": 1,
            }

            # Unassign the user — counts.users drops back to 0.
            e2e_client.request(
                "DELETE",
                f"/api/roles/{role}/users",
                headers=platform_admin.headers,
                json={"user_ids": [user_id]},
            )

            roles_final = e2e_client.get(
                "/api/roles", headers=platform_admin.headers
            ).json()
            match3 = next(r for r in roles_final if r["id"] == role)
            assert match3["consumer_counts"]["users"] == 0
            assert match3["consumer_counts"]["forms"] == 1
        finally:
            # Sweep state pollution before `test_cli_import` runs and trips on
            # a workflow→role link pointing at a non-existent role.
            e2e_client.delete(
                f"/api/roles/{role}", headers=platform_admin.headers
            )
            if wf_id:
                e2e_client.delete(
                    f"/api/workflows/{wf_id}", headers=platform_admin.headers
                )


# =============================================================================
# Holders, permissions and placements on GET /api/roles
# =============================================================================

USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"


def _listed(e2e_client, headers, role_id: str) -> dict:
    roles = e2e_client.get(
        "/api/roles", params={"include_builtin": True}, headers=headers
    ).json()
    return next(r for r in roles if r["id"] == role_id)


def _create_user(e2e_client, headers, org_id: str) -> str:
    resp = e2e_client.post(
        "/api/users",
        headers=headers,
        json={
            "email": f"holders-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Holder",
            "organization_id": org_id,
            "invite": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.e2e
class TestRoleAccessSummary:
    def test_holders_permissions_and_placements(
        self, e2e_client, platform_admin, org1
    ):
        role = _create_role(e2e_client, platform_admin.headers, "Summary")
        user_ids: list[str] = []
        try:
            assert _listed(e2e_client, platform_admin.headers, role)[
                "placements"
            ] == {"organizations": 0, "managed": False, "platform": False}

            set_resp = e2e_client.put(
                f"/api/roles/{role}/permissions",
                headers=platform_admin.headers,
                json={"permissions": ["users.read", "roles.read"]},
            )
            assert set_resp.status_code == 200, set_resp.text

            placements = [
                [{"kind": "organization", "organization_id": org1["id"]}],
                [{"kind": "managed_organizations"}],
            ]
            for boundaries in placements:
                user_id = _create_user(e2e_client, platform_admin.headers, org1["id"])
                user_ids.append(user_id)
                resp = e2e_client.put(
                    f"/api/users/{user_id}/role-assignments",
                    headers=platform_admin.headers,
                    json={
                        "base_role_id": USER_ROLE_ID,
                        "additional": [{"role_id": role, "boundaries": boundaries}],
                    },
                )
                assert resp.status_code == 200, resp.text

            listed = _listed(e2e_client, platform_admin.headers, role)
            assert listed["holders"] == 2
            assert listed["permissions"] == ["roles.read", "users.read"]
            assert listed["placements"] == {
                "organizations": 1,
                "managed": True,
                "platform": False,
            }

            single = e2e_client.get(
                f"/api/roles/{role}", headers=platform_admin.headers
            ).json()
            assert single["holders"] == 2
            assert single["placements"] == listed["placements"]
        finally:
            for user_id in user_ids:
                e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)
            e2e_client.delete(f"/api/roles/{role}", headers=platform_admin.headers)

    def test_builtin_user_role_holders_count_base_role_users(
        self, e2e_client, platform_admin, org1
    ):
        before = _listed(e2e_client, platform_admin.headers, USER_ROLE_ID)["holders"]
        user_id = _create_user(e2e_client, platform_admin.headers, org1["id"])
        try:
            after = _listed(e2e_client, platform_admin.headers, USER_ROLE_ID)["holders"]
            assert after == before + 1
        finally:
            e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)
