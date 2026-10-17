"""Provider membership alone does not grant access to another org's agent."""

import uuid

import pytest

pytestmark = pytest.mark.e2e


def test_provider_membership_does_not_expand_agent_reach(
    e2e_client, platform_admin, provider_org_user, org1
) -> None:
    response = e2e_client.post(
        "/api/agents",
        headers=platform_admin.headers,
        json={
            "name": f"Reach check {uuid.uuid4().hex[:8]}",
            "system_prompt": "Reply briefly.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
    )
    assert response.status_code == 201, response.text
    agent = response.json()
    try:
        response = e2e_client.get(
            f"/api/agents/{agent['id']}", headers=provider_org_user.headers
        )
        assert response.status_code == 404, response.text
        assert "system_prompt" not in response.json()
    finally:
        deleted = e2e_client.delete(
            f"/api/agents/{agent['id']}", headers=platform_admin.headers
        )
        assert deleted.status_code == 204, deleted.text
