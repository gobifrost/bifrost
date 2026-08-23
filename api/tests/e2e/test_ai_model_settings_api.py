def test_failed_provider_verification_does_not_save(e2e_client, platform_admin):
    response = e2e_client.post(
        "/api/admin/ai/connections/verify",
        headers=platform_admin.headers,
        json={
            "name": "Unsaved Provider",
            "provider": "openai",
            "api_key": "sk-test",
            "endpoint": "https://api.openai.com/v1",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is False
    assert "failed" in response.json()["message"].lower()

    connections = e2e_client.get(
        "/api/admin/ai/connections",
        headers=platform_admin.headers,
    ).json()
    assert all(connection["name"] != "Unsaved Provider" for connection in connections)


def test_ai_model_settings_crud_and_assignment(e2e_client, platform_admin):
    connection_resp = e2e_client.post(
        "/api/admin/ai/connections",
        headers=platform_admin.headers,
        json={
            "name": "Default E2E",
            "provider": "openrouter",
            "api_key": "sk-test",
        },
    )
    assert connection_resp.status_code == 201, connection_resp.text
    connection = connection_resp.json()
    assert connection["api_key_set"] is True
    assert "api_key" not in connection
    assert connection["endpoint"] == "https://openrouter.ai/api/v1"

    update_resp = e2e_client.patch(
        f"/api/admin/ai/connections/{connection['id']}",
        headers=platform_admin.headers,
        json={"name": "Default E2E Renamed"},
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["api_key_set"] is True

    previous_profile_resp = e2e_client.post(
        "/api/admin/ai/profiles",
        headers=platform_admin.headers,
        json={
            "name": "Previous Chat E2E",
            "connection_id": connection["id"],
            "model": "openai/gpt-4o-mini",
            "enabled_for_chat": True,
        },
    )
    assert previous_profile_resp.status_code == 201, previous_profile_resp.text
    assert previous_profile_resp.json()["enabled_for_chat"] is True
    initial_assignments_resp = e2e_client.get(
        "/api/admin/ai/assignments",
        headers=platform_admin.headers,
    )
    assert initial_assignments_resp.status_code == 200, initial_assignments_resp.text
    assert {
        assignment["assignment_key"]
        for assignment in initial_assignments_resp.json()
        if assignment["profile_id"] == previous_profile_resp.json()["id"]
    } == {
        "primary",
        "summarization",
        "tuning",
        "image_generation",
        "video_generation",
        "chat_default",
    }
    previous_assignment_resp = e2e_client.put(
        "/api/admin/ai/assignments/chat_default",
        headers=platform_admin.headers,
        json={"profile_id": previous_profile_resp.json()["id"]},
    )
    assert previous_assignment_resp.status_code == 200, previous_assignment_resp.text

    profile_resp = e2e_client.post(
        "/api/admin/ai/profiles",
        headers=platform_admin.headers,
        json={
            "name": "Balanced E2E",
            "connection_id": connection["id"],
            "model": "openai/gpt-4o-mini",
            "enabled_for_chat": True,
        },
    )
    assert profile_resp.status_code == 201, profile_resp.text
    profile = profile_resp.json()
    assert profile["connection"]["name"] == "Default E2E Renamed"
    assert profile["enabled_for_chat"] is True

    assignment_resp = e2e_client.put(
        "/api/admin/ai/assignments/chat_default",
        headers=platform_admin.headers,
        json={"profile_id": profile["id"]},
    )
    assert assignment_resp.status_code == 200, assignment_resp.text
    assert assignment_resp.json()["profile"]["id"] == profile["id"]

    delete_profile_resp = e2e_client.delete(
        f"/api/admin/ai/profiles/{profile['id']}",
        headers=platform_admin.headers,
    )
    assert delete_profile_resp.status_code == 400
    assert "used by assignments" in delete_profile_resp.text

    clear_resp = e2e_client.delete(
        "/api/admin/ai/assignments/chat_default",
        headers=platform_admin.headers,
    )
    assert clear_resp.status_code == 400
    assert "required" in clear_resp.text

    delete_connection_in_use_resp = e2e_client.delete(
        f"/api/admin/ai/connections/{connection['id']}",
        headers=platform_admin.headers,
    )
    assert delete_connection_in_use_resp.status_code == 400
    assert "model profiles" in delete_connection_in_use_resp.text
