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
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PermissionDomain(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    who_should_hold: str


PERMISSION_DOMAINS: dict[str, PermissionDomain] = {
    "roles": PermissionDomain(
        description="Role definitions and their entity/agent grants (workflows, forms, apps, agents, knowledge, users).",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
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
        description="Organization and user lifecycle: creating orgs, inviting/managing/removing users, forcing a user's sessions revoked.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role gets limited user support at Managed organizations.",
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
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them. Running a workflow is `execute`.",
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
            "config, ROI targets, required-instructions content, decorator "
            "properties, the tool catalog, workflow signing keys, and app/form "
            "embed secrets. Some of these are inherently global (branding, AI "
            "pricing); others are inherently per-org (OAuth SSO config, embed "
            "secrets, workflow keys) — boundary follows what the specific route "
            "actually scopes, not the domain."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "metrics": PermissionDomain(
        description="Aggregate usage/cost/ROI reporting, the audit log, and scheduler diagnostics.",
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
