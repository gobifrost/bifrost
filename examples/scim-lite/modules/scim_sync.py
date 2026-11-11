"""Pure SCIM-lite mapping logic (no platform imports; plain-pytest safe)."""

from __future__ import annotations

MAX_EMAIL_LEN = 254
MAX_NAME_LEN = 200
MAX_GROUPS = 50
MAX_GROUP_LEN = 100


def registry_id(org_id: str, external_id: str) -> str:
    """Composite registry key so one shared table serves all orgs."""
    return f"{org_id}:{external_id}"


def expand_grant(value: str | dict) -> tuple[str, list[str] | None]:
    """Normalize one mapping value to (role_name, org_refs).

    "Support" -> ("Support", None): home-org grant.
    {"role": "Support", "orgs": ["Org A", "Org B"]} -> ("Support", ["Org A", "Org B"]).
    """
    if isinstance(value, str):
        if not value:
            raise ValueError("empty role name")
        return value, None
    if isinstance(value, dict):
        role = value.get("role")
        orgs = value.get("orgs", [])
        if not isinstance(role, str) or not role:
            raise ValueError("grant needs a role name")
        if not isinstance(orgs, list) or not orgs or not all(isinstance(o, str) and o for o in orgs):
            raise ValueError("grant needs a non-empty orgs list")
        return role, list(orgs)
    raise ValueError(f"invalid grant: {value!r}")


def resolve_grants(groups: list[str], mapping: dict) -> list[tuple[str, list[str] | None]]:
    """Map IdP groups to (role, org_refs) grants; drop unmapped groups."""
    return [expand_grant(mapping[g]) for g in groups if g in mapping]


def mapped_role_names(mapping: dict) -> set[str]:
    """Every role this mapping can ever grant: the SCIM-managed set.

    Must not depend on the user's current groups, or removing the last
    group would leave the old grant looking manual and never revoked.
    """
    return {expand_grant(value)[0] for value in mapping.values()}


def normalize_user_event(payload: dict) -> dict:
    """Validate one IdP user payload into a stable shape.

    Accepts SCIM-ish or flat keys: external_id/id, email/userName,
    name/displayName, active, groups. Raises ValueError on bad input.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    external_id = payload.get("external_id", payload.get("id"))
    email = payload.get("email", payload.get("userName"))
    name = payload.get("name", payload.get("displayName", ""))
    active = payload.get("active", True)
    groups = payload.get("groups", [])

    if not isinstance(external_id, str) or not external_id or len(external_id) > 128:
        raise ValueError("external_id missing or invalid")
    if not isinstance(email, str) or "@" not in email or len(email) > MAX_EMAIL_LEN:
        raise ValueError("email missing or invalid")
    if not isinstance(name, str) or len(name) > MAX_NAME_LEN:
        raise ValueError("name invalid")
    if not isinstance(active, bool):
        raise ValueError("active must be boolean")
    if not isinstance(groups, list) or len(groups) > MAX_GROUPS:
        raise ValueError("groups must be a list (max 50)")
    clean_groups: list[str] = []
    for g in groups:
        if not isinstance(g, str) or not g or len(g) > MAX_GROUP_LEN:
            raise ValueError(f"invalid group: {g!r}")
        if g not in clean_groups:
            clean_groups.append(g)

    return {
        "external_id": external_id,
        "email": email.lower(),
        "name": name or email.split("@")[0],
        "active": active,
        "groups": clean_groups,
    }


def resolve_roles(groups: list[str], mapping: dict[str, str]) -> list[str]:
    """Map IdP group names to Bifrost role names; drop unmapped groups."""
    return [mapping[g] for g in groups if g in mapping]


def roles_to_revoke(assigned: list[str], desired: list[str]) -> list[str]:
    """Role names the user holds but the IdP groups no longer grant."""
    return [r for r in assigned if r not in desired]
