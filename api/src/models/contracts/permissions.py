"""The closed permission-domain vocabulary the R2/R3 roles catalog builds on.

Every ``AccessEntry.permission`` domain (and every operation-catalog
``action_scopes`` domain) must be one of these. New domains are added here
deliberately, not invented ad hoc in an access-list entry — the roles UI
will eventually list exactly this set.

Naming follows the operation catalog's existing domains where one exists
(``src.services.operation_catalog.OPERATION_CATALOG``): no underscores, and
a catalog sub-domain like ``apps.deploy`` is its own entry, not folded into
its parent. Platform-operations surfaces with no catalog domain of their
own are collapsed into three buckets (``settings``, ``metrics``,
``platform``) rather than getting one domain each — see each bucket's
``description`` for exactly what it covers.

A permission string is ``<domain>.<read|readwrite|execute>[.all]``. The
optional ``.all`` suffix means extended management detail on objects the
holder can already reach (an app's source, a form's publication review);
it never widens which organizations the holder reaches. There is no
implicit hierarchy: ``readwrite`` does not imply ``read`` and ``read.all``
does not imply ``read``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


class PermissionDomain(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    who_should_hold: str


PERMISSION_DOMAINS: dict[str, PermissionDomain] = {
    "roles": PermissionDomain(
        description=(
            "Role definitions and their permission sets. Assigning roles to "
            "users is `roleassignments`; sharing an entity with a role is "
            "that entity's own domain (workflows, forms, apps, agents)."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "roleassignments": PermissionDomain(
        description="Assigning roles to users and setting where each assignment applies (its boundaries), distinct from authoring the role definitions themselves.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role may assign only roles that carry no permissions, and never to a privileged user.",
    ),
    "users": PermissionDomain(
        description=(
            "The user directory and limited support actions on ordinary "
            "users: inviting an ordinary user into an organization, sending, "
            "resending, regenerating and revoking invites, changing a name, "
            "resetting a password or MFA, deactivating, and forcing sessions "
            "to sign out."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role gets user support at Managed organizations.",
    ),
    "users.lifecycle": PermissionDomain(
        description="Elevated user changes: creating platform/Global users, moving a user between organizations or into Global, changing a user's identity (email, verification, External), changing a user's base role, and permanently deleting a user.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role or the Platform Operator role.",
    ),
    "integrations": PermissionDomain(
        description="Integration definitions, config schema, and per-org OAuth/API mappings.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "repository": PermissionDomain(
        description="Raw `_repo/` workspace file content (apps/workflows/.bifrost source) and its module cache.",
        who_should_hold="Platform admins at the Platform boundary, and running workflows through workflow permissions. Never ordinary users.",
    ),
    "organizations": PermissionDomain(
        description="Organization records and their lifecycle: creating, renaming, configuring and disabling organizations. Users inside an organization are `users`.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role reads Managed organizations.",
    ),
    "solutions": PermissionDomain(
        description="Solution catalog entries: install records, updates, connection references.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "solutions.deploy": PermissionDomain(
        description="Triggering a Solution's install/sync/uninstall deploy job.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "solutions.build": PermissionDomain(
        description="Building/packaging a Solution from its source.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. Builder roles get this once Builder ships.",
    ),
    "events": PermissionDomain(
        description="Event sources, subscriptions, webhook adapters, and emitting/publishing an event onto a topic.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "apps": PermissionDomain(
        description="V2 App definitions: source, dependencies, draft/publish state.",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them.",
    ),
    "apps.deploy": PermissionDomain(
        description="Publishing a built App version live.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "workflows": PermissionDomain(
        description="Workflow definitions: registration, validation, role grants, execution.",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them. Running a workflow is `execute`; the Platform Operator role runs workflows at Managed organizations.",
    ),
    "policyrules": PermissionDomain(
        description="Reusable named policy-rule fragments referenced by table/file policies.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "knowledge": PermissionDomain(
        description="Knowledge-base namespaces and documents (the RAG corpus).",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them.",
    ),
    "filepolicies": PermissionDomain(
        description="Access-policy documents governing `_repo`/uploaded file paths.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "tables": PermissionDomain(
        description="Table (structured-data) definitions — not the row data inside them (that's table_policy-governed).",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them. Row access is decided by table policies.",
    ),
    "forms": PermissionDomain(
        description="Form definitions and their runtime configuration.",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them.",
    ),
    "configs": PermissionDomain(
        description="Config key/value definitions and their per-org values, including the workflow SDK's runtime read/write path.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. Running workflows read and write configs through workflow permissions.",
    ),
    "claims": PermissionDomain(
        description="Insurance/service claim records (a first-class platform entity).",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them.",
    ),
    "agents": PermissionDomain(
        description="The shared (non-private) agent catalog — definitions, independent of who owns a given agent.",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them. Members may also create and edit their own private agents (own-private-agent class).",
    ),
    "executions": PermissionDomain(
        description="Workflow execution history and diagnostics.",
        who_should_hold="Read: members see their own runs; platform admins and roles at a boundary see others'. Write/cleanup: platform admins.",
    ),
    "mcp": PermissionDomain(
        description="MCP server templates, org-level shared connections, and the MCP gateway surface.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "agentruns": PermissionDomain(
        description="Agent-run history, verdicts, and moderation state.",
        who_should_hold="Read: members see their own agent runs; platform admins and roles at a boundary see others'. Write (verdicts, reruns): same rule.",
    ),
    "settings": PermissionDomain(
        description=(
            "Platform or org configuration that isn't a first-class entity of its "
            "own: AI model routing/pricing/behavior, branding, OAuth/SSO provider "
            "config, required-instructions content, decorator "
            "properties, the tool catalog, workflow signing keys, and app/form "
            "embed secrets. Some of these are inherently global (branding, AI "
            "pricing); others are inherently per-org (OAuth SSO config, embed "
            "secrets, workflow keys) — boundary follows what the specific route "
            "actually scopes, not the domain."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "secrets": PermissionDomain(
        description=(
            "Decrypting secret values: secret config values, integration "
            "OAuth tokens and client secrets returned in plain text. "
            "Metadata about a secret (that it exists, its key, whether it is "
            "set) is the owning domain's `read`, not this. Only these "
            "decrypt paths return a secret in plain text: UI and admin "
            "routes redact secret values for everyone, admins included."
        ),
        who_should_hold=(
            "The engine execution principal, for running workflows. Humans "
            "only through an explicit role assignment; the Platform Admin "
            "wildcard never implies it."
        ),
    ),
    "reports": PermissionDomain(
        description="ROI reporting: per-organization and per-workflow ROI summaries and trends, and the ROI settings they are computed from.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "metrics": PermissionDomain(
        description="Aggregate usage/cost reporting, the audit log, and scheduler diagnostics. ROI reporting is `reports`.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role reads metrics at Managed organizations.",
    ),
    "platform": PermissionDomain(
        description=(
            "Platform operations with no per-org meaning: maintenance jobs, "
            "worker/Kubernetes execution infrastructure, the package registry, "
            "dependency-usage lookups, git-sync export/import, background job/"
            "schedule administration, the GitHub app connection, platform-wide "
            "notifications/websocket channels, bulk OAuth-token maintenance, and "
            "the org-scoped external-service registry (registered per org, but "
            "administered platform-admin-only today)."
        ),
        who_should_hold="Platform admins only.",
    ),
}


PERMISSION_ACTIONS = ("read", "readwrite", "execute")
"""The actions a permission string may name, before an optional ``.all``."""

ALL_SUFFIX = "all"


@dataclass(frozen=True)
class ParsedPermission:
    domain: str
    action: str
    # True when the string carries the ``.all`` suffix.
    extended: bool


def parse_permission(permission: str) -> ParsedPermission:
    """Parse ``<domain>.<read|readwrite|execute>[.all]``.

    Raises ``ValueError`` when the string is malformed or its domain is not
    in ``PERMISSION_DOMAINS``. A domain may itself contain a dot
    (``solutions.deploy``, ``users.lifecycle``).
    """
    body, _, last = permission.rpartition(".")
    extended = last == ALL_SUFFIX
    if not extended:
        body = permission
    domain, _, action = body.rpartition(".")
    if not domain or action not in PERMISSION_ACTIONS:
        raise ValueError(
            f"Invalid permission format: {permission!r} "
            "(expected '<domain>.<read|readwrite|execute>[.all]')"
        )
    if domain not in PERMISSION_DOMAINS:
        raise ValueError(
            f"Unknown permission domain {domain!r} (not in PERMISSION_DOMAINS)"
        )
    return ParsedPermission(domain=domain, action=action, extended=extended)


DECRYPT_PERMISSION = "secrets.read"

WILDCARD_EXCLUDED_PERMISSIONS: frozenset[str] = frozenset({DECRYPT_PERMISSION})
"""Permissions the Platform Admin wildcard does not satisfy: they must be
held explicitly (see the ``secrets`` domain)."""

PRIVILEGED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "users.readwrite",
        "users.lifecycle.readwrite",
        "roles.readwrite",
        "roleassignments.readwrite",
        "organizations.readwrite",
        DECRYPT_PERMISSION,
        "configs.readwrite",
        "integrations.readwrite",
        "settings.readwrite",
        "platform.read",
        "platform.readwrite",
        "repository.read",
        "repository.readwrite",
        "claims.readwrite",
        "filepolicies.readwrite",
        "policyrules.readwrite",
        "solutions.deploy.execute",
        "executions.readwrite",
        "mcp.readwrite",
    }
)
"""Permissions that make whoever holds them, at any boundary, a privileged
principal (see ``src.services.authorization.privilege``). The Platform
Admin wildcard is privileged too. A privileged user is a protected target:
limited user-support and role-assignment permissions do not reach them."""
