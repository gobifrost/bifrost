"""Shared user-provisioning orchestration (engine-only: imports bifrost SDK).

Thin workflows (webhook trickle, scheduled pull) normalize via scim_sync
(pure) and call sync_one_user here. Tested live, not under plain pytest.
"""

from __future__ import annotations

from typing import Any

from bifrost import api, organizations, roles, tables, users
from modules.scim_sync import (
    expand_grant,
    mapped_role_names,
    normalize_user_event,
    registry_id,
    resolve_grants,
)


async def sync_one_user(
    raw: dict[str, Any], *, org_id: str, group_mapping: dict[str, str]
) -> dict[str, Any]:
    """Create/update/deactivate one IdP user + enforce group→role grants."""
    user = normalize_user_event(raw)
    reg_id = registry_id(org_id, user["external_id"])

    mapping = await tables.get("scim_identities", reg_id)
    user_id = mapping.data.get("user_id") if mapping else None
    if user_id is None:
        # Email fallback must not hijack another org's user with the same email.
        existing = await users.get(user["email"])
        if existing is not None and str(existing.organization_id) == org_id:
            user_id = str(existing.id)

    if user_id is None:
        if not user["active"]:
            return {"status": "ignored", "email": user["email"]}
        created = await users.create(
            email=user["email"], name=user["name"], org_id=org_id
        )
        user_id = str(created.id)
        created_new = True
    else:
        await users.update(user_id, name=user["name"], is_active=user["active"])
        created_new = False

    await tables.upsert(
        "scim_identities",
        reg_id,
        {
            "org_id": org_id,
            "user_id": user_id,
            "email": user["email"],
            "active": user["active"],
        },
    )

    grants = resolve_grants(user["groups"], group_mapping)
    all_roles = await roles.list()
    by_name = {r.name: str(r.id) for r in all_roles}
    unknown_roles = sorted(
        {role for role, _ in grants if role not in by_name}
        | {f"{role}@{ref}" for role, refs in grants for ref in (refs or []) if await _unresolvable_org(ref)}
    )
    desired: set[tuple[str, str]] = set()
    if user["active"]:
        for role_name, org_refs in grants:
            role_id = by_name.get(role_name)
            if not role_id:
                continue
            if org_refs is None:
                desired.add((role_id, org_id))
            else:
                for ref in org_refs:
                    resolved = await _resolve_org(ref)
                    if resolved:
                        desired.add((role_id, resolved))
    # "Managed" = every role this mapping can ever grant, not just the roles
    # this user holds right now. Otherwise losing the last group leaves the
    # old grant looking like a manual one and it is never revoked.
    managed = {
        by_name[role]
        for role in mapped_role_names(group_mapping)
        if role in by_name
    }

    current = await _get_assignments(user_id)
    current_pairs = {
        (entry["role_id"], b["organization_id"])
        for entry in current["additional"]
        for b in entry.get("boundaries", [])
        if b.get("kind") == "organization" and b.get("organization_id")
    }
    unmanaged = {
        (rid, oid) for rid, oid in current_pairs if rid not in managed
    }
    new_pairs = unmanaged | desired
    granted = sorted({await _role_name(rid, by_name) for rid, _ in new_pairs - current_pairs})
    revoked = sorted({await _role_name(rid, by_name) for rid, _ in current_pairs - new_pairs if rid in managed})
    if new_pairs != current_pairs:
        await _put_assignments(user_id, current["base_role_id"], new_pairs)

    return {
        "status": "created" if created_new else "updated",
        "user_id": user_id,
        "email": user["email"],
        "active": user["active"],
        "roles_granted": granted,
        "roles_revoked": revoked,
        "unknown_roles": unknown_roles,
    }


_ORG_CACHE: dict[str, str | None] | None = None


async def _org_map() -> dict[str, str]:
    """Name-or-UUID to org UUID. No TTL: template processes are short-lived."""
    global _ORG_CACHE
    if _ORG_CACHE is None:
        orgs = await organizations.list()
        _ORG_CACHE = {o.id: o.id for o in orgs} | {o.name: o.id for o in orgs}
    return {k: v for k, v in _ORG_CACHE.items() if v is not None}


async def _resolve_org(ref: str) -> str | None:
    return (await _org_map()).get(ref)


async def _unresolvable_org(ref: str) -> bool:
    return (await _resolve_org(ref)) is None


async def _role_name(role_id: str, by_name: dict[str, str]) -> str:
    for name, rid in by_name.items():
        if rid == role_id:
            return name
    return role_id


async def _get_assignments(user_id: str) -> dict:
    resp = await api.get(f"/api/users/{user_id}/role-assignments")
    if resp.status_code != 200:
        raise RuntimeError(f"read assignments failed: {resp.status_code}")
    data = resp.json()
    return {"base_role_id": data["base_role"]["id"], "additional": data.get("additional", [])}


async def _put_assignments(user_id: str, base_role_id: str, pairs: set[tuple[str, str]]) -> None:
    by_role: dict[str, list[str]] = {}
    for rid, oid in sorted(pairs):
        by_role.setdefault(rid, []).append(oid)
    resp = await api.put(
        f"/api/users/{user_id}/role-assignments",
        json={
            "base_role_id": base_role_id,
            "additional": [
                {
                    "role_id": rid,
                    "boundaries": [
                        {"kind": "organization", "organization_id": oid} for oid in oids
                    ],
                }
                for rid, oids in by_role.items()
            ],
        },
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"replace assignments failed: {resp.status_code}")
