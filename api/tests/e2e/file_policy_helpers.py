"""Helpers for E2E tests that intentionally exercise policy-gated file APIs."""

from __future__ import annotations

from urllib.parse import quote


def grant_file_policy(
    e2e_client,
    headers: dict[str, str],
    *,
    location: str = "workspace",
    scope: str | None = None,
    prefix: str = "",
    actions: list[str] | None = None,
    when: dict | None = None,
    allow_all: bool = False,
) -> None:
    encoded = quote(prefix or "/", safe="")
    params: dict[str, str] = {"location": location}
    if scope is not None:
        params["scope"] = scope
    response = e2e_client.put(
        f"/api/files/policies/{encoded}",
        headers=headers,
        params=params,
        json={
            "policies": {
                "policies": [
                    {
                        "name": "test_admin_file_access",
                        "actions": actions or ["read", "write", "delete", "list"],
                        "when": None if allow_all else ({"user": "is_platform_admin"} if when is None else when),
                    }
                ]
            }
        },
    )
    assert response.status_code == 200, response.text
