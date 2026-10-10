"""The closed permission-domain vocabulary the R2/R3 roles catalog builds on.

Every ``AccessEntry.permission`` domain (and every operation-catalog
``action_scopes`` domain) must be one of these. New domains are added here
deliberately, not invented ad hoc in an access-list entry — the roles UI
will eventually list exactly this set.

Names follow Microsoft Graph. A permission string is
``<resource>.<action>[.all]``, all lowercase, and the resource is one word
with no dot or underscore: a sensitive sub-area is a resource of its own
(``userlifecycle``), not a dotted child of its parent. Platform-operations
surfaces with no resource of their own are collapsed into three buckets
(``settings``, ``metrics``, ``platform``) rather than getting one each — see
each bucket's ``description`` for exactly what it covers.

The actions are ``read`` and ``readwrite`` on every resource, plus
``readbasic`` (the everyday view of items shared with the holder) on a
resource whose ``basic`` is set, plus each verb the resource declares in
``verbs`` (``workflows.execute``, ``apps.publish``, ``solutions.deploy``).

The optional ``.all`` suffix follows ``read`` or ``readwrite`` on a resource
whose ``private`` is set, and means other people's private items of that
resource (another user's private agents, workflow runs or artifacts). It
never widens which organizations the holder reaches. There is no implicit
hierarchy: ``readwrite`` does not imply ``read`` and ``read.all`` does not
imply ``read``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PermissionArea = Literal[
    "Identity & Access",
    "Automation",
    "Data & Content",
    "Integrations & Secrets",
    "Platform",
]


class PermissionDomain(BaseModel):
    model_config = ConfigDict(frozen=True)

    # The resource, in Title Case ("Workflow Runs").
    title: str
    area: PermissionArea
    description: str
    who_should_hold: str
    # The verb actions the resource declares, each with its own display
    # name ("execute" -> "Run Workflows").
    verbs: dict[str, str] = Field(default_factory=dict)
    # True when the resource has a ``readbasic`` view.
    basic: bool = False
    # True when the resource has private items, so ``.all`` applies.
    private: bool = False


PERMISSION_DOMAINS: dict[str, PermissionDomain] = {
    "roles": PermissionDomain(
        title="Roles",
        area="Identity & Access",
        description=(
            "Role definitions and their permission sets. Assigning roles to "
            "users is `roleassignments`; sharing an entity with a role is "
            "that entity's own domain (workflows, forms, apps, agents)."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "roleassignments": PermissionDomain(
        title="Role Assignments",
        area="Identity & Access",
        description="Assigning roles to users and setting where each assignment applies (its boundaries), distinct from authoring the role definitions themselves.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role may assign only roles that carry no permissions, and never to a privileged user.",
    ),
    "users": PermissionDomain(
        title="Users",
        area="Identity & Access",
        description=(
            "The user directory and limited support actions on ordinary "
            "users: inviting an ordinary user into an organization, sending, "
            "resending, regenerating and revoking invites, changing a name, "
            "resetting a password or MFA, deactivating, and forcing sessions "
            "to sign out. Impersonate Users runs a workflow or an agent as "
            "another user in an organization where the holder has this "
            "permission. Running as a user who holds privileged access also "
            "needs Manage Privileged Access."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role gets user support at Managed organizations, never Impersonate Users.",
        verbs={"impersonate": "Impersonate Users"},
    ),
    "userlifecycle": PermissionDomain(
        title="User Lifecycle",
        area="Identity & Access",
        description="Elevated user changes: creating platform/Global users, moving a user between organizations or into Global, changing a user's identity (email, verification, External), changing a user's base role, and permanently deleting a user.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role or the Platform Operator role.",
    ),
    "privilegedaccess": PermissionDomain(
        title="Privileged Access",
        area="Identity & Access",
        description="Assigning or removing a privileged role (Platform Admin included) and changing an account that holds one.",
        who_should_hold="Platform admins only.",
    ),
    "integrations": PermissionDomain(
        title="Integrations",
        area="Integrations & Secrets",
        description="Integration definitions, config schema, and per-org OAuth/API mappings.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "repository": PermissionDomain(
        title="Workspace Files",
        area="Data & Content",
        description="Raw `_repo/` workspace file content (apps/workflows/.bifrost source) and its module cache.",
        who_should_hold="Platform admins at the Platform boundary, and running workflows through workflow permissions. Never ordinary users.",
    ),
    "organizations": PermissionDomain(
        title="Organizations",
        area="Identity & Access",
        description="Organization records and their lifecycle: creating, renaming, configuring and disabling organizations. Users inside an organization are `users`.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role reads Managed organizations.",
    ),
    "solutions": PermissionDomain(
        title="Solutions",
        area="Automation",
        description=(
            "Solution catalog entries: install records, updates, connection "
            "references. Deploy Solutions triggers a Solution's install/sync/"
            "uninstall deploy job; Build Solutions builds or packages a "
            "Solution from its source."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. Builder roles get Build Solutions once Builder ships.",
        verbs={"deploy": "Deploy Solutions", "build": "Build Solutions"},
    ),
    "events": PermissionDomain(
        title="Events",
        area="Automation",
        description="Event sources, subscriptions, webhook adapters, and emitting/publishing an event onto a topic.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "apps": PermissionDomain(
        title="Apps",
        area="Automation",
        description=(
            "V2 App definitions: source, dependencies, draft/publish state. "
            "Read Basic opens apps shared with the holder; Read is the full "
            "view (source, bundle manifest). Publish Apps publishes a built "
            "App version live."
        ),
        who_should_hold="Write and Publish Apps: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read Basic: members, where the object is shared with them.",
        verbs={"publish": "Publish Apps"},
        basic=True,
    ),
    "workflows": PermissionDomain(
        title="Workflows",
        area="Automation",
        description=(
            "Workflow definitions: registration, validation, role grants, "
            "execution. Run Workflows starts a workflow at all, in an "
            "organization; the workflow's access setting still decides which "
            "ones."
        ),
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them. Run Workflows: members, and the Platform Operator role at Managed organizations.",
        verbs={"execute": "Run Workflows"},
    ),
    "policyrules": PermissionDomain(
        title="Policy Rules",
        area="Data & Content",
        description="Reusable named policy-rule fragments referenced by table/file policies.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "knowledge": PermissionDomain(
        title="Knowledge",
        area="Data & Content",
        description="Knowledge-base namespaces and documents (the RAG corpus).",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them.",
    ),
    "filepolicies": PermissionDomain(
        title="File Policies",
        area="Data & Content",
        description="Access-policy documents governing `_repo`/uploaded file paths.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "tables": PermissionDomain(
        title="Tables",
        area="Data & Content",
        description="Table (structured-data) definitions — not the row data inside them (that's table_policy-governed).",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them. Row access is decided by table policies.",
    ),
    "tableattribution": PermissionDomain(
        title="Table Row Attribution",
        area="Data & Content",
        description="Who a table row is recorded against: recording a different person as a row's creator or editor.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "forms": PermissionDomain(
        title="Forms",
        area="Automation",
        description=(
            "Form definitions and their runtime configuration. Read Basic "
            "opens forms shared with the holder; Read is the full view "
            "(publication review, raw provider errors)."
        ),
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read Basic: members, where the object is shared with them.",
        basic=True,
    ),
    "configs": PermissionDomain(
        title="Configuration",
        area="Integrations & Secrets",
        description="Config key/value definitions and their per-org values, including the workflow SDK's runtime read/write path.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. Running workflows read and write configs through workflow permissions.",
    ),
    "claims": PermissionDomain(
        title="Claims",
        area="Data & Content",
        description="Insurance/service claim records (a first-class platform entity).",
        who_should_hold="Write: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read: members, where the object is shared with them.",
    ),
    "agents": PermissionDomain(
        title="Agents",
        area="Automation",
        description=(
            "Agent definitions. Read Basic is the everyday view of agents "
            "shared with the holder; Read reaches agents not shared with them, "
            "in reach. Read All reaches other people's private agents; Read "
            "and Write All edits, tunes or promotes them. Run Agents starts an "
            "agent run: enqueue, execute, rerun, dry run, chat, gateway tool "
            "calls."
        ),
        who_should_hold="Write and the All permissions: platform admins, or an admin-assigned role at a specific boundary; never the User base role. Read Basic and Run Agents: members, where the object is shared with them. Members may also create and edit their own private agents (own-private-agent class).",
        verbs={"execute": "Run Agents"},
        basic=True,
        private=True,
    ),
    "executions": PermissionDomain(
        title="Workflow Runs",
        area="Automation",
        description=(
            "Workflow execution history and diagnostics. Read Basic is the "
            "holder's own runs without diagnostics; Read adds debug logs, "
            "variables, context and resources. Read All reaches other "
            "people's runs, with diagnostics; Read and Write All cancels them."
        ),
        who_should_hold="Read Basic: members, for their own runs. The All permissions and write/cleanup: platform admins, or an admin-assigned role at a specific boundary; never the User base role.",
        basic=True,
        private=True,
    ),
    "mcp": PermissionDomain(
        title="MCP Servers",
        area="Integrations & Secrets",
        description=(
            "MCP server templates, org-level shared connections, and the MCP "
            "gateway surface. Read Basic is the everyday MCP view; Read is the "
            "MCP client configuration."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. Read Basic: members.",
        basic=True,
    ),
    "agentruns": PermissionDomain(
        title="Agent Runs",
        area="Automation",
        description=(
            "Agent-run history, verdicts, and moderation state. Read All "
            "reaches other people's agent runs; Read and Write All records "
            "verdicts, reruns, cancels and backfills summaries on them."
        ),
        who_should_hold="Read: members see their own agent runs. The All permissions: platform admins, or an admin-assigned role at a specific boundary; never the User base role.",
        private=True,
    ),
    "artifacts": PermissionDomain(
        title="Artifacts",
        area="Data & Content",
        description=(
            "Files that runs and chats create. Read All lists, reads and "
            "downloads artifacts other people created; Read and Write All "
            "writes artifacts into someone else's workspace."
        ),
        who_should_hold="Members reach the artifacts they created. The All permissions: platform admins only; never the User base role or the Platform Operator role.",
        private=True,
    ),
    "home": PermissionDomain(
        title="Home Collections",
        area="Data & Content",
        description=(
            "Home page collections. Read and Write creates and edits shared "
            "collections; Read All reaches other people's collections and "
            "Read and Write All edits them."
        ),
        who_should_hold="Members keep their own collections. Shared collections and the All permissions: platform admins, or an admin-assigned role at a specific boundary; never the User base role.",
        private=True,
    ),
    "platformjobs": PermissionDomain(
        title="Platform Jobs",
        area="Platform",
        description=(
            "Background platform jobs (deploys, builds, exports) and who "
            "started them. Read All reaches other people's platform jobs; "
            "Read and Write All cancels them."
        ),
        who_should_hold="Members see the jobs they started. The All permissions: platform admins, or an admin-assigned role at a specific boundary; never the User base role.",
        private=True,
    ),
    "settings": PermissionDomain(
        title="Settings",
        area="Platform",
        description=(
            "Platform or org configuration that isn't a first-class entity of its "
            "own: AI model routing/pricing/behavior, branding, OAuth/SSO provider "
            "config, required-instructions content, decorator "
            "properties, the tool catalog, workflow signing keys, and app/form "
            "embed secrets. Some of these are inherently global (branding, AI "
            "pricing); others are inherently per-org (OAuth SSO config, embed "
            "secrets, workflow keys) — boundary follows what the specific route "
            "actually scopes, not the domain. Read Basic is the everyday "
            "settings (required-instructions content); Read is the admin "
            "settings views."
        ),
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. Read Basic: members.",
        basic=True,
    ),
    "ai": PermissionDomain(
        title="AI Model Information",
        area="Platform",
        description=(
            "AI use and model information. Read shows which AI models and "
            "profiles are configured, without secrets; Use AI spends AI "
            "credit from the SDK (completion and streaming). Configuring "
            "model routing and pricing is `settings`."
        ),
        who_should_hold="Read and Use AI: members. Others only through an admin-assigned role at a specific boundary.",
        verbs={"execute": "Use AI"},
    ),
    "secrets": PermissionDomain(
        title="Secret Values",
        area="Integrations & Secrets",
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
        title="ROI Reports",
        area="Platform",
        description="ROI reporting: per-organization and per-workflow ROI summaries and trends, and the ROI settings they are computed from.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role.",
    ),
    "metrics": PermissionDomain(
        title="Usage and Audit Logs",
        area="Platform",
        description="Aggregate usage/cost reporting, the audit log, and scheduler diagnostics. ROI reporting is `reports`.",
        who_should_hold="Platform admins. Others only through an admin-assigned role at a specific boundary; never the User base role. The Platform Operator role reads metrics at Managed organizations.",
    ),
    "platform": PermissionDomain(
        title="Platform Operations",
        area="Platform",
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


PERMISSION_ACTIONS = ("read", "readwrite")
"""The actions every resource has; ``.all`` may follow only these."""

BASIC_ACTION = "readbasic"
"""The everyday view, on a resource whose ``basic`` is set."""

ALL_SUFFIX = "all"

ACTION_VERBS = {
    "read": "Read",
    "readwrite": "Read and Write",
    "readbasic": "Read Basic",
    "read.all": "Read All",
    "readwrite.all": "Read and Write All",
}
"""How a non-verb permission's display name begins: ``{verb} {resource title}``."""


@dataclass(frozen=True)
class ParsedPermission:
    domain: str
    action: str
    # True when the string carries the ``.all`` suffix.
    extended: bool


def domain_actions(domain: str) -> tuple[str, ...]:
    """Every action ``domain`` allows, ``.all`` variants included, in
    display order: read, readwrite, readbasic, verbs, read.all, readwrite.all."""
    info = PERMISSION_DOMAINS[domain]
    actions = [*PERMISSION_ACTIONS]
    if info.basic:
        actions.append(BASIC_ACTION)
    actions.extend(info.verbs)
    if info.private:
        actions.extend(f"{action}.{ALL_SUFFIX}" for action in PERMISSION_ACTIONS)
    return tuple(actions)


def parse_permission(permission: str) -> ParsedPermission:
    """Parse ``<resource>.<action>[.all]`` (see the module docstring).

    Raises ``ValueError`` when the resource is not in ``PERMISSION_DOMAINS``
    or the resource does not allow the action.
    """
    domain, _, action = permission.partition(".")
    if domain not in PERMISSION_DOMAINS:
        raise ValueError(
            f"Unknown permission domain {domain!r} (not in PERMISSION_DOMAINS)"
        )
    if action not in domain_actions(domain):
        raise ValueError(
            f"Invalid permission format: {permission!r} ({domain!r} allows "
            f"{', '.join(domain_actions(domain))})"
        )
    base, _, suffix = action.partition(".")
    return ParsedPermission(domain=domain, action=base, extended=bool(suffix))


def permission_display_name(permission: str) -> str:
    """Name a permission the way Microsoft Graph does: "Read and Write Users".

    A verb is named by the resource's ``verbs`` ("Run Workflows").
    """
    parsed = parse_permission(permission)
    domain = PERMISSION_DOMAINS[parsed.domain]
    if parsed.action in domain.verbs:
        return domain.verbs[parsed.action]
    action = f"{parsed.action}.{ALL_SUFFIX}" if parsed.extended else parsed.action
    return f"{ACTION_VERBS[action]} {domain.title}"


def domain_display_names(domain: str) -> dict[str, str]:
    """The display name of every permission ``domain`` allows, whether or not
    a route checks it (a role can hold any well-formed permission)."""
    return {
        f"{domain}.{action}": permission_display_name(f"{domain}.{action}")
        for action in domain_actions(domain)
    }


DECRYPT_PERMISSION = "secrets.read"

WILDCARD_EXCLUDED_PERMISSIONS: frozenset[str] = frozenset({DECRYPT_PERMISSION})
"""Permissions the Platform Admin wildcard does not satisfy: they must be
held explicitly (see the ``secrets`` domain)."""

PRIVILEGED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "users.readwrite",
        "users.impersonate",
        "userlifecycle.readwrite",
        "privilegedaccess.readwrite",
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
        "solutions.deploy",
        "agents.readwrite.all",
        "executions.readwrite",
        "executions.read.all",
        "executions.readwrite.all",
        "artifacts.read.all",
        "artifacts.readwrite.all",
        "tableattribution.readwrite",
        "mcp.readwrite",
    }
)
"""Permissions that make whoever holds them, at any boundary, a privileged
principal (see ``src.services.authorization.privilege``). The Platform
Admin wildcard is privileged too. A privileged user is a protected target:
limited user-support and role-assignment permissions do not reach them."""


class PermissionCatalogEntry(BaseModel):
    """One permission domain as the roles and access screens present it."""

    domain: str
    title: str
    area: PermissionArea
    description: str
    who_should_hold: str
    # Actions the platform checks for this domain (read, readwrite,
    # readbasic, verbs, with any ``.all`` variants kept as listed).
    actions: list[str]
    # The display name of every permission the domain allows, keyed by
    # permission ("tables.readwrite" -> "Read and Write Tables"); not
    # limited to ``actions``.
    names: dict[str, str]
    # The domain's permissions that make a holder a privileged principal.
    privileged: list[str]
    # per_organization: only checked at an organization; platform_wide: only
    # at Global; varies: both, or no route checks it yet.
    scope: Literal["per_organization", "platform_wide", "varies"]
    # True once any route of the domain is decided by the evaluator.
    enforced: bool
