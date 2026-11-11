"""SCIM-lite user + group sync (thin orchestration; logic in modules/)."""

from bifrost import config, context, workflow
from modules.scim_provision import sync_one_user


@workflow(category="Identity", tags=["scim", "users"])
async def scim_user_event(org_id: str | None = None) -> dict:
    """Create/update/deactivate one user from the IdP webhook payload.

    org_id is admin wiring (subscription input_mapping), never request input:
    global-install webhook runs carry no execution org of their own.
    """
    body = context.event.data if context.event else {}
    if not isinstance(body, dict):
        raise ValueError("webhook payload must be an object")
    resolved = org_id or (str(context.org_id) if context.org_id else None)
    if not resolved:
        raise ValueError("no org: bind org_id in the subscription input_mapping")
    result = await sync_one_user(
        body, org_id=resolved, group_mapping=await _group_mapping(resolved)
    )
    return {**result, "resolved_org": resolved}


async def _group_mapping(org_id: str) -> dict[str, str]:
    raw = await config.get("SCIM_GROUP_ROLES", scope=org_id, default={})
    return dict(raw) if isinstance(raw, dict) else {}
