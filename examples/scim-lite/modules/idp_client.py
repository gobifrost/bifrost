"""IdP read client (engine-only: uses httpx; translate helpers are pure)."""

from __future__ import annotations

from typing import Any


def translate_keycloak_user(u: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    """Map a Keycloak admin-API user (+ group names) to a scim-lite payload."""
    first = (u.get("firstName") or "").strip()
    last = (u.get("lastName") or "").strip()
    name = " ".join(p for p in (first, last) if p) or u.get("username", "")
    email = u.get("email") or f"{u.get('username', 'unknown')}@unknown.invalid"
    return {
        "external_id": str(u.get("id", "")),
        "email": email,
        "name": name,
        "active": bool(u.get("enabled", True)),
        "groups": list(groups),
    }


async def fetch_user_page(
    base_url: str,
    *,
    token: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    dialect: str = "scim-lite",
    domain: str | None = None,
) -> dict[str, Any]:
    """Fetch one IdP user page. Returns {"users": [...], "next_cursor": str | None}."""
    import httpx

    limit = max(1, min(limit, 200))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base = base_url.rstrip("/")

    if dialect == "keycloak":
        offset = int(cursor or 0)
        path = "/users"
        params: dict[str, Any] = {"first": offset, "max": limit, "briefRepresentation": "false"}
    elif dialect == "scim-lite":
        path = "/users"
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if domain:
            params["domain"] = domain
    else:
        raise ValueError(f"unknown dialect: {dialect}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
        resp = await client.get(f"{base}{path}", params=params, headers=headers)
    if resp.status_code == 401:
        raise RuntimeError("IdP rejected credentials (401); check the mapping token")
    if resp.status_code == 429:
        raise RuntimeError("IdP rate limit hit; retry later")
    if resp.status_code >= 400:
        raise RuntimeError(f"IdP page fetch failed: {resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict) or not isinstance(data.get("users"), list):
        raise ValueError("IdP returned an unexpected page shape")

    users = data["users"]
    if dialect == "keycloak":
        out = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            for u in users:
                groups: list[str] = []
                if isinstance(u, dict) and u.get("id"):
                    gresp = await client.get(
                        f"{base}/users/{u['id']}/groups", headers=headers
                    )
                    if gresp.status_code < 400:
                        gdata = gresp.json()
                        groups = [
                            g.get("name", "")
                            for g in (gdata if isinstance(gdata, list) else [])
                            if isinstance(g, dict) and g.get("name")
                        ]
                out.append(translate_keycloak_user(u, groups))
        got = len(users)
        return {"users": out, "next_cursor": str(offset + got) if got == limit else None}
    return {"users": users, "next_cursor": data.get("next_cursor")}
