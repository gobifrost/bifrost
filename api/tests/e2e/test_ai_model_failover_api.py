"""HTTP coverage for the profile failover pointer (router + response shape)."""


def _connection(e2e_client, platform_admin, name="Failover E2E"):
    response = e2e_client.post(
        "/api/admin/ai/connections",
        headers=platform_admin.headers,
        json={"name": name, "provider": "openrouter", "api_key": "sk-test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _profile(e2e_client, platform_admin, connection_id, name):
    response = e2e_client.post(
        "/api/admin/ai/profiles",
        headers=platform_admin.headers,
        json={
            "name": name,
            "connection_id": connection_id,
            "model": "openai/gpt-4o-mini",
            "enabled_for_chat": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_failover_pointer_round_trip(e2e_client, platform_admin):
    connection = _connection(e2e_client, platform_admin)
    primary = _profile(e2e_client, platform_admin, connection["id"], "Primary FO")
    fallback = _profile(e2e_client, platform_admin, connection["id"], "Fallback FO")

    assert primary["failover_profile_id"] is None
    assert primary["failover_profile_name"] is None

    set_resp = e2e_client.patch(
        f"/api/admin/ai/profiles/{primary['id']}",
        headers=platform_admin.headers,
        json={"failover_profile_id": fallback["id"]},
    )
    assert set_resp.status_code == 200, set_resp.text
    assert set_resp.json()["failover_profile_id"] == fallback["id"]
    assert set_resp.json()["failover_profile_name"] == fallback["name"]

    listed = e2e_client.get(
        "/api/admin/ai/profiles", headers=platform_admin.headers
    ).json()
    assert next(
        item for item in listed if item["id"] == primary["id"]
    )["failover_profile_name"] == fallback["name"]

    clear_resp = e2e_client.patch(
        f"/api/admin/ai/profiles/{primary['id']}",
        headers=platform_admin.headers,
        json={"failover_profile_id": None},
    )
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["failover_profile_id"] is None


def test_failover_self_reference_rejected(e2e_client, platform_admin):
    connection = _connection(e2e_client, platform_admin, name="Failover E2E Self")
    profile = _profile(e2e_client, platform_admin, connection["id"], "Self FO")
    response = e2e_client.patch(
        f"/api/admin/ai/profiles/{profile['id']}",
        headers=platform_admin.headers,
        json={"failover_profile_id": profile["id"]},
    )
    assert response.status_code == 400, response.text


def test_delete_guarded_while_used_as_fallback(e2e_client, platform_admin):
    connection = _connection(e2e_client, platform_admin, name="Failover E2E Guard")
    primary = _profile(e2e_client, platform_admin, connection["id"], "Guard Primary")
    fallback = _profile(e2e_client, platform_admin, connection["id"], "Guard Fallback")
    set_resp = e2e_client.patch(
        f"/api/admin/ai/profiles/{primary['id']}",
        headers=platform_admin.headers,
        json={"failover_profile_id": fallback["id"]},
    )
    assert set_resp.status_code == 200, set_resp.text

    delete_resp = e2e_client.delete(
        f"/api/admin/ai/profiles/{fallback['id']}",
        headers=platform_admin.headers,
    )
    assert delete_resp.status_code == 400, delete_resp.text
    assert "failover" in delete_resp.text.lower()
