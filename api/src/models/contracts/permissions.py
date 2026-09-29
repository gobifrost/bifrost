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
        who_should_hold="Org admins managing who can do what within their own organization.",
    ),
    "integrations": PermissionDomain(
        description="Integration definitions, config schema, and per-org OAuth/API mappings.",
        who_should_hold="Org admins connecting and configuring third-party services for their own org.",
    ),
    "repository": PermissionDomain(
        description="Raw `_repo/` workspace file content (apps/workflows/.bifrost source) and its module cache.",
        who_should_hold="Platform admins and the executing workflow/engine — not ordinary users.",
    ),
    "organizations": PermissionDomain(
        description="Organization and user lifecycle: creating orgs, inviting/managing/removing users, forcing a user's sessions revoked.",
        who_should_hold="Platform admins today; org admins for their own org once R3 lands.",
    ),
    "solutions": PermissionDomain(
        description="Solution catalog entries: install records, updates, connection references.",
        who_should_hold="Org admins installing/managing Solutions in their own org.",
    ),
    "solutions.deploy": PermissionDomain(
        description="Triggering a Solution's install/sync/uninstall deploy job.",
        who_should_hold="Org admins deploying a Solution into their own org.",
    ),
    "solutions.build": PermissionDomain(
        description="Building/packaging a Solution from its source.",
        who_should_hold="Solution authors and platform admins building a Solution.",
    ),
    "events": PermissionDomain(
        description="Event sources, subscriptions, webhook adapters, and emitting/publishing an event onto a topic.",
        who_should_hold="Org admins managing their own org's event sources/subscriptions.",
    ),
    "apps": PermissionDomain(
        description="V2 App definitions: source, dependencies, draft/publish state.",
        who_should_hold="Org admins/app builders managing their own org's apps.",
    ),
    "apps.deploy": PermissionDomain(
        description="Publishing a built App version live.",
        who_should_hold="Org admins/app builders publishing their own org's apps.",
    ),
    "workflows": PermissionDomain(
        description="Workflow definitions: registration, validation, role grants, execution.",
        who_should_hold="Org admins/workflow authors managing their own org's workflows.",
    ),
    "policyrules": PermissionDomain(
        description="Reusable named policy-rule fragments referenced by table/file policies.",
        who_should_hold="Org admins authoring shared policy building blocks for their own org.",
    ),
    "knowledge": PermissionDomain(
        description="Knowledge-base namespaces and documents (the RAG corpus).",
        who_should_hold="Org admins/knowledge curators managing their own org's corpus.",
    ),
    "filepolicies": PermissionDomain(
        description="Access-policy documents governing `_repo`/uploaded file paths.",
        who_should_hold="Platform admins today (global); org admins for their own org once R3 lands.",
    ),
    "tables": PermissionDomain(
        description="Table (structured-data) definitions — not the row data inside them (that's table_policy-governed).",
        who_should_hold="Org admins/table owners managing their own org's tables.",
    ),
    "forms": PermissionDomain(
        description="Form definitions and their runtime configuration.",
        who_should_hold="Org admins/form authors managing their own org's forms.",
    ),
    "configs": PermissionDomain(
        description="Config key/value definitions and their per-org values, including the workflow SDK's runtime read/write path.",
        who_should_hold="Org admins managing their own org's config; the executing workflow reads/writes it at runtime.",
    ),
    "claims": PermissionDomain(
        description="Insurance/service claim records (a first-class platform entity).",
        who_should_hold="Org admins/claim handlers managing their own org's claims.",
    ),
    "agents": PermissionDomain(
        description="The shared (non-private) agent catalog — definitions, independent of who owns a given agent.",
        who_should_hold="Org admins managing the shared agent catalog for their own org.",
    ),
    "executions": PermissionDomain(
        description="Workflow execution history and diagnostics.",
        who_should_hold="Org admins auditing their own org's execution history; platform admins for cross-org cleanup.",
    ),
    "mcp": PermissionDomain(
        description="MCP server templates, org-level shared connections, and the MCP gateway surface.",
        who_should_hold="Org admins configuring their own org's MCP integrations.",
    ),
    "agentruns": PermissionDomain(
        description="Agent-run history, verdicts, and moderation state.",
        who_should_hold="Org admins/reviewers auditing their own org's agent runs.",
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
        who_should_hold="Platform admins for the global settings in this bucket; org admins for the per-org ones.",
    ),
    "metrics": PermissionDomain(
        description="Aggregate usage/cost/ROI reporting, the audit log, and scheduler diagnostics.",
        who_should_hold="Org admins for their own org's numbers; platform admins for cross-org rollups and the audit log.",
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
