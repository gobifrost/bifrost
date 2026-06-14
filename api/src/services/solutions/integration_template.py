"""Build a secret-scrubbed, portable skeleton of an Integration for a Solution
connection declaration. Carries the fill-out shape (config schema, OAuth provider
shape, data provider) but NEVER client_id/client_secret/tokens/mappings/org ids.
"""
from __future__ import annotations

from typing import Any

# Safe OAuthProvider fields to carry. Everything not listed is dropped — in
# particular client_id, encrypted_client_secret, organization_id, status*,
# tokens, last_token_refresh.
_SAFE_OAUTH_FIELDS = (
    "provider_name", "display_name", "oauth_flow_type", "authorization_url",
    "token_url", "audience", "token_url_defaults", "entity_id_source",
    "scopes", "redirect_uri",
)


def build_integration_template(integration: Any) -> dict[str, Any]:
    config_schema = [
        {
            "key": s.key, "type": s.type, "required": bool(s.required),
            "description": s.description, "options": s.options,
            "position": s.position,
        }
        for s in (integration.config_schema or [])
    ]
    oauth = None
    prov = getattr(integration, "oauth_provider", None)
    if prov is not None:
        oauth = {f: getattr(prov, f, None) for f in _SAFE_OAUTH_FIELDS}
    return {
        "name": integration.name,
        "entity_id_name": getattr(integration, "entity_id_name", None),
        "default_entity_id": getattr(integration, "default_entity_id", None),
        "data_provider_id": (
            str(integration.list_entities_data_provider_id)
            if getattr(integration, "list_entities_data_provider_id", None)
            else None
        ),
        "config_schema": config_schema,
        "oauth": oauth,
    }
