"""Skim admin backend: per-org onboarding status + save (provider-admin only).

Runs as the caller's org; scope overrides reach target orgs for provider
callers. Secrets stay server-side: api_token flows mapping-config -> the
connection's secret schema, never into results or logs.
"""

from __future__ import annotations

from typing import Any

from bifrost import config, integrations, organizations, roles, workflow
from modules.scim_sync import expand_grant

DIALECTS = ("scim-lite", "keycloak")


@workflow(category="Identity", tags=["scim", "admin"])
async def scim_onboarding_status(org_id: str) -> dict:
    """Read one org's SCIM onboarding state (mapping + group config + roles)."""
    mapping = await integrations.get_mapping("scim-idp", scope=org_id)
    group_roles = await config.get("SCIM_GROUP_ROLES", scope=org_id, default={})
    all_roles = await roles.list()
    return {
        "org_id": org_id,
        "mapped": mapping is not None,
        "entity_id": mapping.entity_id if mapping else None,
        "base_url": (mapping.config or {}).get("base_url") if mapping else None,
        "dialect": (mapping.config or {}).get("dialect", "scim-lite") if mapping else None,
        "domain": (mapping.config or {}).get("domain") if mapping else None,
        "api_token_set": bool((mapping.config or {}).get("api_token")) if mapping else False,
        "group_roles": dict(group_roles) if isinstance(group_roles, dict) else {},
        "roles": sorted(r.name for r in all_roles),
    }


@workflow(category="Identity", tags=["scim", "admin"])
async def scim_onboarding_save(
    org_id: str,
    entity_id: str,
    base_url: str,
    group_roles: dict,
    dialect: str = "scim-lite",
    domain: str | None = None,
    api_token: str | None = None,
) -> dict:
    """Validate + save one org's IdP mapping and group→role map.

    Raises on the first problem; writes mapping first, group config second.
    """
    if not isinstance(entity_id, str) or not entity_id or len(entity_id) > 128:
        raise ValueError("entity_id missing or invalid")
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url must be http(s)")
    if dialect not in DIALECTS:
        raise ValueError(f"dialect must be one of {DIALECTS}")
    if domain is not None and (not isinstance(domain, str) or not domain):
        raise ValueError("domain must be a non-empty string or omitted")
    if not isinstance(group_roles, dict):
        raise ValueError("group_roles must be an object")
    for group, grant in group_roles.items():
        if not isinstance(group, str) or not group or len(group) > 100:
            raise ValueError(f"invalid group: {group!r}")
        role, org_refs = expand_grant(grant)
        if org_refs is not None:
            resolved = await _resolve_orgs(org_refs)
            missing = [o for o in org_refs if o not in resolved]
            if missing:
                raise ValueError(f"unknown orgs in grant for {group!r}: {missing}")

    mapping_cfg: dict[str, Any] = {"base_url": base_url, "dialect": dialect}
    if domain:
        mapping_cfg["domain"] = domain
    if api_token:
        mapping_cfg["api_token"] = api_token
    mapping = await integrations.upsert_mapping(
        "scim-idp", scope=org_id, entity_id=entity_id, config=mapping_cfg
    )
    await config.set("SCIM_GROUP_ROLES", group_roles, scope=org_id)
    return {
        "org_id": org_id,
        "mapping_id": mapping.id,
        "entity_id": mapping.entity_id,
        "groups": sorted(group_roles),
    }


async def _resolve_orgs(refs: list[str]) -> dict[str, str]:
    orgs = await organizations.list()
    by_ref = {o.id: o.id for o in orgs} | {o.name: o.id for o in orgs}
    return {r: by_ref[r] for r in refs if r in by_ref}
