"""Legacy Role authorization-field translation for the packaged CLI."""

from __future__ import annotations

from typing import Any


LEGACY_CAPABILITY_RENAMES: dict[str, str] = {
    "agents.write": "agents.readwrite",
    "apps.publish": "apps.deploy.execute",
    "apps.write": "apps.readwrite",
    "claims.write": "claims.readwrite",
    "configs.write": "configs.readwrite",
    "events.write": "events.readwrite",
    "files.content.read": "managedfiles.read",
    "files.content.write": "managedfiles.readwrite",
    "files.policies.read": "filepolicies.read",
    "files.policies.write": "filepolicies.readwrite",
    "forms.write": "forms.readwrite",
    "integrations.write": "integrations.readwrite",
    "organizations.write": "organizations.readwrite",
    "policy.rules.read": "policyrules.read",
    "policy.rules.write": "policyrules.readwrite",
    "roles.write": "roles.readwrite",
    "tables.documents.read": "tabledocuments.read",
    "tables.documents.write": "tabledocuments.readwrite",
    "tables.write": "tables.readwrite",
    "workflows.write": "workflows.readwrite",
}


def translate_legacy_role_capabilities(
    capabilities: list[str] | None,
    permissions: dict[str, Any] | None,
) -> list[str]:
    """Translate shipped legacy Role scopes/permissions to capabilities."""

    translated: set[str] = set()
    for capability in capabilities or []:
        if capability == "organization.impersonation":
            continue
        if capability == "solutions.build":
            translated.update(
                {
                    "builder.execute",
                    "solutions.build.execute",
                    "solutions.deploy.execute",
                    "solutions.readwrite",
                }
            )
            continue
        translated.add(LEGACY_CAPABILITY_RENAMES.get(capability, capability))
    if permissions and permissions.get("can_promote_agent") is True:
        translated.add("agents.readwrite")
    return sorted(translated)
