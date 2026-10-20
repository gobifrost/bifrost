"""The same sharing decision governs discovery and exact resource reads.

Metadata-only resources: no deploy, publish, build, or execution jobs.
"""

from uuid import uuid4

import pytest


@pytest.mark.e2e
@pytest.mark.parametrize("kind", ["app", "form", "agent"])
def test_role_sharing_matches_list_and_open_after_grant_and_revocation(
    kind, e2e_client, platform_admin, org1, org1_user
):
    suffix = uuid4().hex[:8]
    role_response = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": f"Resource Sharing {suffix}"},
    )
    assert role_response.status_code == 201, role_response.text
    role = role_response.json()
    collection = {"app": "/api/applications", "form": "/api/forms", "agent": "/api/agents"}[kind]
    payload = {
        "name": f"Shared {kind} {suffix}",
        "organization_id": org1["id"],
        "access_level": "role_based",
        "role_ids": [role["id"]],
    }
    if kind == "app":
        payload.update(slug=f"resource-sharing-{suffix}", app_model="standalone_v2")
    elif kind == "form":
        payload.update(workflow_id=None, form_schema={"fields": []})
    else:
        payload.update(system_prompt="Help the user.", channels=["chat"])

    resource = None
    try:
        created = e2e_client.post(collection, headers=platform_admin.headers, json=payload)
        assert created.status_code == 201, created.text
        resource = created.json()
        detail = f"{collection}/{resource['slug'] if kind == 'app' else resource['id']}"
        admin_list = e2e_client.get(collection, headers=platform_admin.headers)
        assert admin_list.status_code == 200, admin_list.text
        admin_items = admin_list.json()["applications"] if kind == "app" else admin_list.json()
        assert resource["id"] in {item["id"] for item in admin_items}

        for phase, visible in (("unassigned", False), ("assigned", True), ("revoked", False)):
            if phase == "assigned":
                grant = e2e_client.post(
                    f"/api/roles/{role['id']}/users",
                    headers=platform_admin.headers,
                    json={"user_ids": [str(org1_user.user_id)]},
                )
                assert grant.status_code in (200, 201, 204), grant.text
            elif phase == "revoked":
                revoke = e2e_client.delete(
                    f"/api/roles/{role['id']}/users/{org1_user.user_id}",
                    headers=platform_admin.headers,
                )
                assert revoke.status_code in (200, 204), revoke.text

            for params in ({}, {"scope": org1["id"]}):
                listed = e2e_client.get(collection, headers=org1_user.headers, params=params)
                assert listed.status_code == 200, listed.text
                items = listed.json()["applications"] if kind == "app" else listed.json()
                assert (resource["id"] in {item["id"] for item in items}) is visible, phase
            opened = e2e_client.get(detail, headers=org1_user.headers)
            denied_status = 403 if kind == "form" else 404
            assert opened.status_code == (200 if visible else denied_status), (phase, opened.text)
    finally:
        if resource is not None:
            deleted = e2e_client.delete(f"{collection}/{resource['id']}", headers=platform_admin.headers)
            assert deleted.status_code in (200, 204), deleted.text
        deleted_role = e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)
        assert deleted_role.status_code in (200, 204), deleted_role.text
