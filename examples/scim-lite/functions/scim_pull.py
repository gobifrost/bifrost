"""Scheduled IdP pull-sync for a GLOBAL install (thin orchestration).

One run walks every org mapping of the scim-idp integration and reconciles
that org through the shared sync_one_user path. Per-org cursors, per-org
group mappings (org config cascade with global fallback), per-org failure
containment: one org's bad page never stops the others.
"""

from datetime import UTC, datetime, timedelta

from bifrost import config, integrations, tables, users, workflow
from modules.idp_client import fetch_user_page
from modules.scim_provision import sync_one_user


@workflow(category="Identity", tags=["scim", "users", "sync"])
async def scim_pull(limit: int = 200, full: bool = False, org_id: str | None = None) -> dict:
    """Pull IdP users for one org (org_id) or every mapped org; reconcile each."""
    limit = max(1, min(limit, 200))
    mappings = await integrations.list_mappings("scim-idp")
    if not mappings:
        raise RuntimeError(
            "No scim-idp integration mappings; "
            "ask an admin to run the onboarding first"
        )
    targets = [m for m in mappings if m.organization_id and (not org_id or m.organization_id == org_id)]
    if org_id and not targets:
        raise ValueError(f"no scim-idp mapping for org {org_id}")
    if not targets:
        raise RuntimeError("scim-idp has no org mappings (global-only); map at least one org")

    org_results: dict[str, dict] = {}
    for m in targets:
        assert m.organization_id
        org_results[m.organization_id] = await _pull_org(
            m.organization_id, m, limit=limit, full=full
        )
    return {"orgs": org_results}


async def _pull_org(org_id: str, mapping, limit: int, full: bool) -> dict:
    cfg = mapping.config or {}
    base_url = cfg.get("base_url", "")
    if not isinstance(base_url, str) or not base_url:
        return {"error": "mapping needs config.base_url"}
    token = cfg.get("api_token")
    dialect = cfg.get("dialect", "scim-lite")
    domain = cfg.get("domain")
    if dialect not in ("scim-lite", "keycloak"):
        return {"error": f"unknown dialect: {dialect}"}

    group_mapping = await _group_mapping(org_id)
    cursor_key = f"pull-cursor:{org_id}"
    cursor: str | None = None
    if not full:
        state = await tables.get("scim_sync_state", cursor_key)
        if state:
            cursor = state.data.get("cursor") or None

    seen: set[str] = set()
    unknown_roles: set[str] = set()
    processed = created = updated = ignored = 0
    failed: list[dict] = []
    pages = 0
    while True:
        if _deadline_close():
            failed.append({"external_id": "*", "reason": "deadline; resume on next run"})
            break
        try:
            page = await fetch_user_page(
                base_url, token=token, cursor=cursor, limit=limit,
                dialect=dialect, domain=domain,
            )
        except Exception as exc:  # noqa: BLE001 - page failure is data
            failed.append({"external_id": "*", "reason": str(exc)})
            break
        pages += 1
        for raw in page["users"]:
            if not isinstance(raw, dict):
                continue
            try:
                result = await sync_one_user(raw, org_id=org_id, group_mapping=group_mapping)
                unknown_roles.update(result.get("unknown_roles", []))
                processed += 1
                if result["status"] == "created":
                    created += 1
                elif result["status"] == "updated":
                    updated += 1
                else:
                    ignored += 1
                ext = raw.get("external_id", raw.get("id"))
                if isinstance(ext, str):
                    seen.add(ext)
            except Exception as exc:  # noqa: BLE001 - per-item failure is data
                failed.append({"external_id": str(raw.get("external_id", "?")), "reason": str(exc)})
        cursor = page.get("next_cursor")
        if not cursor:
            break
        await tables.upsert("scim_sync_state", cursor_key, {"cursor": cursor, "org_id": org_id})

    deactivated = 0
    if full and not failed:
        rows = await tables.query(
            "scim_identities", where={"org_id": {"eq": org_id}}, limit=1000
        )
        for doc in rows.documents:
            ext = doc.id.split(":", 1)[-1]
            if ext not in seen and doc.data.get("active"):
                uid = doc.data.get("user_id")
                if uid:
                    await users.update(str(uid), is_active=False)
                    await tables.upsert(
                        "scim_identities", doc.id, {**doc.data, "active": False}
                    )
                    deactivated += 1

    await tables.upsert("scim_sync_state", cursor_key, {"cursor": cursor or "", "org_id": org_id})
    return {
        "processed": processed,
        "created": created,
        "updated": updated,
        "ignored": ignored,
        "deactivated": deactivated,
        "failed": failed,
        "unknown_roles": sorted(unknown_roles),
        "pages": pages,
    }


def _deadline_close() -> bool:
    from bifrost import context

    return bool(
        context.workflow_deadline
        and datetime.now(UTC)
        > (context.workflow_deadline - timedelta(seconds=60))
    )


async def _group_mapping(org_id: str) -> dict[str, str]:
    raw = await config.get("SCIM_GROUP_ROLES", scope=org_id, default={})
    return dict(raw) if isinstance(raw, dict) else {}
