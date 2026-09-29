"""Catalog-backed providers, model lists, reasoning choices, and refresh."""

from uuid import uuid4


def test_catalog_provider_to_reasoning_profile_round_trip(e2e_client, platform_admin):
    headers = platform_admin.headers
    catalog_resp = e2e_client.get("/api/admin/ai/catalog", headers=headers)
    assert catalog_resp.status_code == 200, catalog_resp.text
    catalog = catalog_resp.json()
    native_ids = {p["id"] for p in catalog["providers"] if p["native"]}
    assert {"openai", "anthropic", "google", "openrouter", "opencode-go"} <= native_ids
    assert catalog["providers"][0]["native"] is True  # native providers list first

    # Anthropic's catalog lists reasoning-capable models with context limits.
    connection_resp = e2e_client.post(
        "/api/admin/ai/connections",
        headers=headers,
        json={
            "name": f"Catalog Anthropic {uuid4().hex[:8]}",
            "provider": "anthropic",
            "api_key": "sk-test",
            "catalog_provider_id": "anthropic",
        },
    )
    assert connection_resp.status_code == 201, connection_resp.text
    connection = connection_resp.json()
    assert connection["catalog_provider_id"] == "anthropic"
    assert connection["endpoint"] == "https://api.anthropic.com"

    # Served from the catalog: the fake key would fail a live listing.
    models_resp = e2e_client.get(
        f"/api/admin/ai/connections/{connection['id']}/models", headers=headers
    )
    assert models_resp.status_code == 200, models_resp.text
    assert models_resp.json()["source"] == "catalog"
    models = models_resp.json()["models"]
    reasoning_model = next(m for m in models if m["reasoning_choices"])
    assert reasoning_model["context_window"]

    choice = reasoning_model["reasoning_choices"][-1]
    profile_resp = e2e_client.post(
        "/api/admin/ai/profiles",
        headers=headers,
        json={
            "name": f"Catalog Reasoning {uuid4().hex[:8]}",
            "connection_id": connection["id"],
            "model": reasoning_model["id"],
            "reasoning_effort": choice,
        },
    )
    assert profile_resp.status_code == 201, profile_resp.text
    assert profile_resp.json()["reasoning_effort"] == choice

    rejected = e2e_client.patch(
        f"/api/admin/ai/profiles/{profile_resp.json()['id']}",
        headers=headers,
        json={"reasoning_effort": "not-a-level"},
    )
    assert rejected.status_code == 400, rejected.text
    assert "does not accept reasoning" in rejected.json()["detail"]


def test_community_provider_uses_catalog_endpoint(e2e_client, platform_admin):
    headers = platform_admin.headers
    providers = e2e_client.get("/api/admin/ai/catalog", headers=headers).json()["providers"]
    community = next(
        p for p in providers if not p["native"] and p["adapter"] == "openai_compatible"
    )

    connection_resp = e2e_client.post(
        "/api/admin/ai/connections",
        headers=headers,
        json={
            "name": f"Community {uuid4().hex[:8]}",
            "provider": "openai_compatible",
            "api_key": "sk-test",
            "catalog_provider_id": community["id"],
        },
    )
    assert connection_resp.status_code == 201, connection_resp.text
    assert connection_resp.json()["endpoint"] == community["endpoint"].rstrip("/")


def test_catalog_refresh_is_an_observable_platform_job(e2e_client, platform_admin):
    response = e2e_client.post("/api/admin/ai/catalog/refresh", headers=platform_admin.headers)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert response.headers["location"] == f"/api/platform-jobs/{job_id}"

    job = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=platform_admin.headers)
    assert job.status_code == 200, job.text
    assert job.json()["job_type"] == "model_catalog.refresh"


def test_catalog_is_platform_admin_only(e2e_client, org1_user):
    response = e2e_client.get("/api/admin/ai/catalog", headers=org1_user.headers)
    assert response.status_code == 403, response.text
