"""
Manifest parser for .bifrost/ metadata files.

Provides Pydantic models and functions for reading, writing, and validating
the workspace manifest. The manifest declares all platform entities,
their file paths, UUIDs, org bindings, roles, and runtime config.

Supports both split format (one file per entity type in .bifrost/) and
legacy single-file format (.bifrost/metadata.yaml).

Stateless — no DB or S3 dependency.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from bifrost.field_classes import FieldClass, classify
from bifrost.manifest_codec import Destination, EntityCodec, ImportFields

logger = logging.getLogger(__name__)


class ClaimQuery(BaseModel):
    """Portable copy of the server's ``ClaimQuery`` (the lookup producing a
    custom claim's value).

    The CLI/manifest cannot import ``src.models.contracts.claims`` — the packaged
    ``bifrost`` distribution has no ``src`` on its path, so a top-level import
    crashed ``bifrost export`` the moment a bundle carried manifest files. The
    portable manifest only needs to round-trip the data, so ``where`` is a loose
    dict AST here (the server validates it on import) — mirroring the server
    shape (``table``/``where``/``select``) without the server dependency.
    """

    table: str = Field(min_length=1, description="Source table name (org-scoped)")
    where: dict | None = Field(default=None, description="Filter AST (same shape as policies)")
    select: str = Field(min_length=1, description="Column or JSON path on the source table")

# =============================================================================
# Constants
# =============================================================================

MANIFEST_FILES: dict[str, str] = {
    "organizations": "organizations.yaml",
    "roles": "roles.yaml",
    "workflows": "workflows.yaml",
    "integrations": "integrations.yaml",
    "configs": "configs.yaml",
    "claims": "claims.yaml",
    "tables": "tables.yaml",
    "events": "events.yaml",
    "forms": "forms.yaml",
    "agents": "agents.yaml",
    "apps": "apps.yaml",
    "mcp_servers": "mcp-servers.yaml",
}
MANIFEST_LEGACY_FILE = "metadata.yaml"


# =============================================================================
# Pydantic Models
# =============================================================================


class ManifestOrganization(EntityCodec, BaseModel):
    """Organization entry in manifest."""
    id: str = Field(**classify(FieldClass.IDENTITY))
    name: str = Field(**classify(FieldClass.CONTENT, match_key=True))
    is_active: bool = Field(default=True, **classify(FieldClass.ENVIRONMENT))

    @classmethod
    def from_orm(cls, org) -> "ManifestOrganization":
        return cls(id=str(org.id), name=org.name, is_active=org.is_active)

    def to_orm_values(self, dest: Destination) -> ImportFields:
        return ImportFields(direct={"id": self.id, "name": self.name, "is_active": self.is_active})


class ManifestRole(EntityCodec, BaseModel):
    """Role entry in manifest."""
    id: str = Field(**classify(FieldClass.IDENTITY))
    name: str = Field(**classify(FieldClass.CONTENT, match_key=True))

    @classmethod
    def from_orm(cls, role) -> "ManifestRole":
        return cls(id=str(role.id), name=role.name)

    def to_orm_values(self, dest: Destination) -> ImportFields:
        return ImportFields(direct={"id": self.id, "name": self.name})


class ManifestWorkflow(BaseModel):
    """Workflow entry in manifest."""
    id: str = Field(description="Workflow UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="MCP tool name; defaults to function_name on registration", **classify(FieldClass.CONTENT))
    path: str = Field(description="Relative path to Python file (e.g. 'workflows/onboard.py')", **classify(FieldClass.CONTENT, match_key=True))
    function_name: str = Field(description="Python function name decorated with @workflow/@tool/@data_provider", **classify(FieldClass.CONTENT, match_key=True))
    type: str = Field(default="workflow", description="workflow | tool | data_provider", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this workflow", **classify(FieldClass.ENVIRONMENT))
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
        **classify(FieldClass.ENVIRONMENT, keep_on_portable=True),
    )
    access_level: str = Field(default="authenticated", description="role_based | authenticated | everyone | public", **classify(FieldClass.CONTENT))
    endpoint_enabled: bool = Field(default=False, description="Expose as HTTP API endpoint", **classify(FieldClass.CONTENT))
    timeout_seconds: int = Field(default=1800, description="Max execution time in seconds. 0 = no timeout. Default 1800 (30 min), max 86400 (24h).", **classify(FieldClass.CONTENT))
    public_endpoint: bool = Field(default=False, description="Allow unauthenticated API access", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="Workflow description", **classify(FieldClass.CONTENT))
    tool_description: str | None = Field(
        default=None,
        description="LLM/agent-facing tool description (portable). API/UI-set, not derived from source.",
        **classify(FieldClass.CONTENT),
    )
    category: str = Field(default="General", description="Category for organization", **classify(FieldClass.CONTENT))
    tags: list[str] = Field(default_factory=list, description="Tags for filtering", **classify(FieldClass.CONTENT))


class ManifestForm(BaseModel):
    """Form entry in manifest.

    Carries portable form content (description, workflow bindings, schema) inline
    under the form's UUID. Environment-specific fields (organization_id, roles,
    access_level) live alongside but are NOT serialized into a portable artifact —
    they describe how this environment binds the form. The ``path`` field is
    deprecated: content is now inline and ``forms/{uuid}.form.yaml`` is no longer
    written by the manifest generator.
    """
    id: str = Field(description="Form UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="Form display name", **classify(FieldClass.CONTENT))
    path: str | None = Field(
        default=None,
        description="DEPRECATED: relative path to form YAML. Content is now inline.",
        **classify(FieldClass.CONTENT),
    )
    # -- Environment-specific fields (NOT portable; do not include when sharing) --
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this form", **classify(FieldClass.ENVIRONMENT))
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
        **classify(FieldClass.ENVIRONMENT, keep_on_portable=True),
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | everyone | public", **classify(FieldClass.CONTENT))
    # -- Portable content (inline) --
    description: str | None = Field(default=None, description="Form description", **classify(FieldClass.CONTENT))
    workflow_id: str | None = Field(default=None, description="Workflow UUID to execute on submit", **classify(FieldClass.REFERENCE))
    launch_workflow_id: str | None = Field(default=None, description="Workflow UUID to run on form load", **classify(FieldClass.REFERENCE))
    default_launch_params: dict | None = Field(default=None, description="Default params for launch workflow", **classify(FieldClass.CONTENT))
    allowed_query_params: list[str] | None = Field(default=None, description="Form fields populatable via URL query params", **classify(FieldClass.CONTENT))
    form_schema: dict | None = Field(default=None, description="Form schema (fields list, etc.)", **classify(FieldClass.CONTENT))


class ManifestAgent(BaseModel):
    """Agent entry in manifest.

    Carries portable agent content (system prompt, channels, tool bindings, etc.)
    inline under the agent's UUID. Environment-specific fields (organization_id,
    roles, access_level) live alongside but are NOT serialized into a portable
    artifact. The ``path`` field is deprecated: content is now inline and
    ``agents/{uuid}.agent.yaml`` is no longer written by the manifest generator.
    """
    id: str = Field(description="Agent UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="Agent display name", **classify(FieldClass.CONTENT))
    path: str | None = Field(
        default=None,
        description="DEPRECATED: relative path to agent YAML. Content is now inline.",
        **classify(FieldClass.CONTENT),
    )
    # -- Environment-specific fields (NOT portable; do not include when sharing) --
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this agent", **classify(FieldClass.ENVIRONMENT))
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
        **classify(FieldClass.ENVIRONMENT, keep_on_portable=True),
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | everyone | public", **classify(FieldClass.CONTENT))
    # -- Portable content (inline) --
    description: str | None = Field(default=None, description="Agent description", **classify(FieldClass.CONTENT))
    system_prompt: str | None = Field(default=None, description="LLM system prompt", **classify(FieldClass.CONTENT))
    channels: list[str] = Field(default_factory=list, description="Channels the agent runs on (chat, email, …)", **classify(FieldClass.CONTENT))
    tool_ids: list[str] = Field(default_factory=list, description="Workflow UUIDs exposed as tools", **classify(FieldClass.REFERENCE))
    delegated_agent_ids: list[str] = Field(default_factory=list, description="Agent UUIDs this agent can delegate to", **classify(FieldClass.REFERENCE))
    knowledge_sources: list[str] = Field(default_factory=list, description="Knowledge namespaces searchable via RAG", **classify(FieldClass.CONTENT))
    system_tools: list[str] = Field(default_factory=list, description="System tool names enabled (e.g. 'execute_workflow')", **classify(FieldClass.CONTENT))
    mcp_connection_ids: list[str] = Field(
        default_factory=list,
        description=(
            "MCP connection UUIDs explicitly granted to this agent. Empty "
            "list means the agent surfaces no external MCP tools."
        ),
        **classify(FieldClass.REFERENCE),
    )
    llm_model: str | None = Field(default=None, description="Override LLM model (null = global default)", **classify(FieldClass.CONTENT))
    llm_max_tokens: int | None = Field(default=None, description="Override LLM max tokens (null = global default)", **classify(FieldClass.CONTENT))
    max_iterations: int | None = Field(default=None, description="Max LLM iterations for autonomous runs", **classify(FieldClass.CONTENT))
    max_token_budget: int | None = Field(default=None, description="Max token budget for autonomous runs", **classify(FieldClass.CONTENT))


class ManifestApp(BaseModel):
    """App entry in manifest."""
    id: str = Field(description="App UUID", **classify(FieldClass.IDENTITY))
    path: str = Field(description="App source directory (e.g. 'apps/my-dashboard'), not app.yaml", **classify(FieldClass.CONTENT))
    slug: str | None = Field(default=None, description="URL slug (auto-generated from name if omitted)", **classify(FieldClass.CONTENT, match_key=True))
    name: str | None = Field(default=None, description="Display name", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="App description", **classify(FieldClass.CONTENT))
    dependencies: dict[str, str] = Field(default_factory=dict, description="NPM packages {name: version}", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this app", **classify(FieldClass.ENVIRONMENT))
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
        **classify(FieldClass.ENVIRONMENT, keep_on_portable=True),
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | everyone | public", **classify(FieldClass.CONTENT))
    app_model: str = Field(default="inline_v1", description="Render model: inline_v1 | standalone_v2", **classify(FieldClass.CONTENT))
    logo: str | None = Field(
        default=None,
        description="Path to a logo image (png/jpeg/svg) relative to the app dir, e.g. 'public/logo.svg'. Shown in BifrostHeader.",
        **classify(FieldClass.CONTENT),
    )


# -- New entity types for manifest expansion --


class ManifestIntegrationConfigSchema(BaseModel):
    """Config schema item within an integration."""
    key: str = Field(description="Config key name", **classify(FieldClass.CONTENT, match_key=True))
    type: str = Field(description="string | int | bool | json | secret", **classify(FieldClass.CONTENT))
    required: bool = Field(default=False, description="Whether this config must be set", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    options: list[str] | None = Field(default=None, description="Allowed values (for string type)", **classify(FieldClass.CONTENT))
    position: int = Field(default=0, description="Display order in UI", **classify(FieldClass.CONTENT))


class ManifestOAuthProvider(BaseModel):
    """OAuth provider structure within an integration.

    client_id uses "__NEEDS_SETUP__" sentinel for new instances.
    client_secret is never serialized.
    """
    provider_name: str = Field(description="Provider identifier", **classify(FieldClass.CONTENT, match_key=True))
    display_name: str | None = Field(default=None, description="UI display name", **classify(FieldClass.CONTENT))
    oauth_flow_type: str = Field(default="authorization_code", description="OAuth flow type", **classify(FieldClass.CONTENT))
    client_id: str = Field(default="__NEEDS_SETUP__", description="OAuth client ID (set via UI)", **classify(FieldClass.REFERENCE))
    authorization_url: str | None = Field(default=None, description="OAuth authorization endpoint", **classify(FieldClass.CONTENT))
    token_url: str | None = Field(default=None, description="OAuth token endpoint", **classify(FieldClass.CONTENT))
    token_url_defaults: dict | None = Field(default=None, description="Default params for token request", **classify(FieldClass.CONTENT))
    scopes: list[str] = Field(default_factory=list, description="OAuth scopes", **classify(FieldClass.CONTENT))
    redirect_uri: str | None = Field(default=None, description="OAuth redirect URI", **classify(FieldClass.CONTENT))


class ManifestIntegrationMapping(BaseModel):
    """Integration mapping to an org + external entity."""
    organization_id: str | None = Field(default=None, description="Org UUID this mapping belongs to", **classify(FieldClass.ENVIRONMENT))
    entity_id: str = Field(description="External entity identifier (e.g. tenant ID)", **classify(FieldClass.REFERENCE))
    entity_name: str | None = Field(default=None, description="Display name for the entity", **classify(FieldClass.CONTENT))
    oauth_token_id: str | None = Field(default=None, description="Linked OAuth token (set via UI)", **classify(FieldClass.REFERENCE))


class ManifestIntegration(BaseModel):
    """Integration entry in manifest."""
    id: str = Field(description="Integration UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="Integration display name", **classify(FieldClass.CONTENT, match_key=True))
    entity_id: str | None = Field(default=None, description="Field name for entity identifier", **classify(FieldClass.REFERENCE))
    entity_id_name: str | None = Field(default=None, description="Display label for entity ID field", **classify(FieldClass.CONTENT))
    default_entity_id: str | None = Field(default=None, description="Default entity ID value", **classify(FieldClass.REFERENCE))
    list_entities_data_provider_id: str | None = Field(default=None, description="Workflow UUID for entity dropdown", **classify(FieldClass.REFERENCE))
    config_schema: list[ManifestIntegrationConfigSchema] = Field(default_factory=list, description="Configuration fields", **classify(FieldClass.CONTENT))
    oauth_provider: ManifestOAuthProvider | None = Field(default=None, description="OAuth provider config", **classify(FieldClass.CONTENT))
    mappings: list[ManifestIntegrationMapping] = Field(default_factory=list, description="Per-org entity mappings", **classify(FieldClass.CONTENT))


class ManifestConfig(BaseModel):
    """Config entry in manifest."""
    id: str = Field(description="Config UUID", **classify(FieldClass.IDENTITY))
    integration_id: str | None = Field(default=None, description="Parent integration UUID (if integration config)", **classify(FieldClass.REFERENCE, match_key=True))
    key: str = Field(description="Config key name", **classify(FieldClass.CONTENT, match_key=True))
    config_type: str = Field(default="string", description="string | int | bool | json | secret", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT, match_key=True))
    value: object | None = Field(default=None, description="Config value (null for secret type)", **classify(FieldClass.CONTENT, predicate="config_value"))


class ManifestSolutionConfigSchema(BaseModel):
    """A solution-owned config DECLARATION (portable; never a value)."""
    id: str = Field(description="Config schema UUID", **classify(FieldClass.IDENTITY))
    key: str = Field(description="Config key name", **classify(FieldClass.CONTENT, match_key=True))
    # Intentionally named ``type`` (not ``config_type`` like ManifestConfig) to
    # match the SolutionConfigSchema ORM column and the collector's body.get("type").
    type: str = Field(default="string", description="string | int | bool | json | secret", **classify(FieldClass.CONTENT))
    required: bool = Field(default=False, description="Whether a value must be supplied at install time", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    # ``object`` not ``str``: a non-string declared type (int/bool/json) needs a
    # matching default (mirrors ManifestConfig.value being typed ``object | None``).
    default: object | None = Field(default=None, description="Default value used when none is supplied", **classify(FieldClass.CONTENT))
    position: int = Field(default=0, description="Display ordering within the solution", **classify(FieldClass.CONTENT))


class ManifestCustomClaim(BaseModel):
    """Custom Claim entry in manifest."""
    id: str = Field(description="Custom Claim UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(description="Claim name, unique per org", **classify(FieldClass.CONTENT, match_key=True))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    organization_id: str = Field(description="Org UUID", **classify(FieldClass.ENVIRONMENT, match_key=True))
    type: Literal["list", "scalar"] = Field(default="list", description="list | scalar", **classify(FieldClass.CONTENT))
    query: ClaimQuery = Field(description="Source table query that resolves the claim", **classify(FieldClass.CONTENT))


class ManifestPolicy(BaseModel):
    """Single policy entry within a table's policies list.

    Mirrors :class:`src.models.contracts.policies.Policy`. The ``when`` field
    holds the policy AST as a plain dict (validated server-side at import).
    """
    name: str = Field(description="Unique policy name within the table", **classify(FieldClass.CONTENT, match_key=True))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    actions: list[Literal["read", "create", "update", "delete"]] = Field(
        description="Actions this policy applies to (read/create/update/delete)",
        **classify(FieldClass.CONTENT),
    )
    when: dict | None = Field(
        default=None,
        description="Policy AST as JSON-compatible dict; null = always allow for matching actions",
        **classify(FieldClass.CONTENT),
    )


class ManifestTable(BaseModel):
    """Table entry in manifest.

    Uses ``table_schema`` in Python but serializes as ``schema`` in YAML
    via the alias, matching the DB column name.

    Policies are a flat list at the manifest level; the serializer wraps
    them as ``{"policies": [...]}`` when writing to ``Table.access`` JSONB
    and unwraps on export. This keeps the YAML readable without the
    redundant ``policies.policies`` nesting.
    """
    id: str = Field(description="Table UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="Table display name", **classify(FieldClass.CONTENT, match_key=True))
    description: str | None = Field(default=None, description="Table description", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT, match_key=True))
    table_schema: dict | None = Field(default=None, alias="schema", description="Column definitions and validation hints", **classify(FieldClass.CONTENT))
    policies: list[ManifestPolicy] | None = Field(
        default=None,
        description="Access policies (flat list). When null on import, the seed admin_bypass policy is written.",
        **classify(FieldClass.CONTENT),
    )

    model_config = {"populate_by_name": True}


class ManifestEventSubscription(BaseModel):
    """Event subscription within an event source."""
    id: str = Field(description="Subscription UUID", **classify(FieldClass.IDENTITY))
    target_type: str = Field(default="workflow", description="'workflow' or 'agent'", **classify(FieldClass.CONTENT))
    workflow_id: str | None = Field(default=None, description="Workflow UUID to trigger (when target_type='workflow')", **classify(FieldClass.REFERENCE))
    agent_id: str | None = Field(default=None, description="Agent UUID to run (when target_type='agent')", **classify(FieldClass.REFERENCE))
    event_type: str | None = Field(default=None, description="Filter by event type (e.g. 'ticket.created')", **classify(FieldClass.CONTENT))
    filter_expression: str | None = Field(default=None, description="JSONPath filter expression", **classify(FieldClass.CONTENT))
    input_mapping: dict | None = Field(default=None, description="Map event fields to workflow params", **classify(FieldClass.CONTENT))
    is_active: bool = Field(default=True, description="Enable/disable this subscription", **classify(FieldClass.ENVIRONMENT))


class ManifestEventSource(BaseModel):
    """Event source entry in manifest."""
    id: str = Field(description="Event source UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="Event source display name", **classify(FieldClass.CONTENT))
    source_type: str = Field(description="webhook | schedule | topic", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    is_active: bool = Field(default=True, description="Enable/disable this source", **classify(FieldClass.ENVIRONMENT))
    # Schedule config
    cron_expression: str | None = Field(default=None, description="Cron schedule (e.g. '0 9 * * *')", **classify(FieldClass.CONTENT))
    timezone: str | None = Field(default=None, description="Timezone (e.g. 'America/New_York')", **classify(FieldClass.CONTENT))
    schedule_enabled: bool | None = Field(default=None, description="Enable/disable schedule", **classify(FieldClass.ENVIRONMENT))
    overlap_policy: str | None = Field(default=None, description="Overlap policy: skip | queue | replace", **classify(FieldClass.CONTENT))
    # Webhook config
    adapter_name: str | None = Field(default=None, description="Webhook adapter (e.g. 'generic', 'halopsa')", **classify(FieldClass.CONTENT))
    webhook_integration_id: str | None = Field(default=None, description="Integration UUID for webhook auth", **classify(FieldClass.REFERENCE))
    webhook_config: dict | None = Field(default=None, description="Adapter-specific config", **classify(FieldClass.CONTENT))
    rate_limit_per_minute: int | None = Field(default=60, description="Max events per window. Null disables.", **classify(FieldClass.CONTENT))
    rate_limit_window_seconds: int = Field(default=60, description="Window in seconds.", **classify(FieldClass.CONTENT))
    rate_limit_enabled: bool = Field(default=True, description="Per-source kill switch.", **classify(FieldClass.CONTENT))
    # Subscriptions
    subscriptions: list[ManifestEventSubscription] = Field(default_factory=list, description="Workflow subscriptions", **classify(FieldClass.CONTENT))


class ManifestMCPConnectionTool(BaseModel):
    """Tool catalog row inside an MCP connection.

    Populated from the vendor's ``tools/list`` and synced into the manifest
    so an importing environment knows the schema of every tool the connection
    is bound to (without re-calling the vendor at import time).
    """
    tool_name: str = Field(description="Tool name as published by the vendor", **classify(FieldClass.CONTENT, match_key=True))
    tool_schema: dict = Field(default_factory=dict, description="JSON schema for the tool", **classify(FieldClass.CONTENT))
    enabled: bool = Field(default=True, description="Whether the tool is enabled in this connection", **classify(FieldClass.CONTENT))
    disabled_reason: str | None = Field(default=None, description="Reason the tool is disabled (admin-set or auto-set)", **classify(FieldClass.CONTENT))


class ManifestMCPConnection(BaseModel):
    """Per-org MCP connection nested under a server template.

    Carries the per-org OAuth client_id and the visibility flags. The
    encrypted client_secret is intentionally NOT serialized — secrets stay
    out of the manifest, in the same way ``Config`` values do.
    """
    organization_id: str = Field(description="Organization UUID this connection belongs to", **classify(FieldClass.ENVIRONMENT))
    client_id: str = Field(description="Vendor-issued OAuth client_id for this org", **classify(FieldClass.REFERENCE))
    server_url_override: str | None = Field(default=None, description="Per-org URL override (regional/sovereign)", **classify(FieldClass.CONTENT))
    available_in_chat: bool = Field(default=False, description="Chat fallback to shared service token when user not connected", **classify(FieldClass.CONTENT))
    available_to_autonomous: bool = Field(default=False, description="Autonomous runs may use shared service token", **classify(FieldClass.CONTENT))
    service_oauth_token_id: str | None = Field(default=None, description="FK to oauth_tokens for shared service token", **classify(FieldClass.REFERENCE))
    tools: list[ManifestMCPConnectionTool] = Field(default_factory=list, description="Per-connection tool catalog", **classify(FieldClass.CONTENT))


class ManifestMCPServer(BaseModel):
    """External MCP server template (top-level manifest entry)."""
    id: str = Field(description="Server template UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(description="Display name (unique)", **classify(FieldClass.CONTENT))
    server_url: str = Field(description="MCP server URL (Streamable HTTP endpoint)", **classify(FieldClass.CONTENT))
    oauth_provider_id: str | None = Field(default=None, description="OAuthProvider UUID; absent for unauthenticated servers", **classify(FieldClass.REFERENCE))
    redirect_url: str | None = Field(default=None, description="Deterministic redirect URL for OAuth callback", **classify(FieldClass.CONTENT))
    discovery_metadata: dict | None = Field(default=None, description="Snapshot of /.well-known payloads at create time", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = platform-level template)", **classify(FieldClass.ENVIRONMENT))
    is_active: bool = Field(default=True, description="Active flag", **classify(FieldClass.ENVIRONMENT))
    connections: dict[str, ManifestMCPConnection] = Field(
        default_factory=dict,
        description="Per-org connections keyed by connection UUID",
        **classify(FieldClass.CONTENT),
    )


class Manifest(BaseModel):
    """The complete workspace manifest."""
    organizations: list[ManifestOrganization] = Field(default_factory=list)
    roles: list[ManifestRole] = Field(default_factory=list)
    workflows: dict[str, ManifestWorkflow] = Field(default_factory=dict)
    integrations: dict[str, ManifestIntegration] = Field(default_factory=dict)
    configs: dict[str, ManifestConfig] = Field(default_factory=dict)
    claims: dict[str, ManifestCustomClaim] = Field(default_factory=dict)
    tables: dict[str, ManifestTable] = Field(default_factory=dict)
    events: dict[str, ManifestEventSource] = Field(default_factory=dict)
    forms: dict[str, ManifestForm] = Field(default_factory=dict)
    agents: dict[str, ManifestAgent] = Field(default_factory=dict)
    apps: dict[str, ManifestApp] = Field(default_factory=dict)
    mcp_servers: dict[str, ManifestMCPServer] = Field(default_factory=dict)


# =============================================================================
# Parse / Serialize
# =============================================================================


def parse_manifest(yaml_str: str) -> Manifest:
    """Parse a YAML string into a Manifest object."""
    if not yaml_str or not yaml_str.strip():
        return Manifest()

    data = yaml.safe_load(yaml_str)
    if not data or not isinstance(data, dict):
        return Manifest()

    return Manifest(**data)


def serialize_manifest(manifest: Manifest) -> str:
    """Serialize a Manifest object to a YAML string.

    Uses exclude_defaults=True so that fields at their default values
    (empty lists, default strings, None) are omitted.  This keeps the
    output stable — re-serializing the same logical manifest always
    produces the same bytes, avoiding false conflicts during sync.
    """
    data = manifest.model_dump(mode="json", exclude_defaults=True, by_alias=True)
    return yaml.dump(data, default_flow_style=False, sort_keys=True, allow_unicode=True)


def filter_manifest_by_ids(manifest: Manifest, entity_ids: set[str]) -> Manifest:
    """Return a new Manifest containing only entities whose IDs are in entity_ids.

    Dict-based entities (workflows, integrations, etc.) are filtered by key.
    List-based entities (organizations, roles) are filtered by id attribute.
    """
    return Manifest(
        organizations=[o for o in manifest.organizations if o.id in entity_ids],
        roles=[r for r in manifest.roles if r.id in entity_ids],
        workflows={k: v for k, v in manifest.workflows.items() if k in entity_ids},
        integrations={k: v for k, v in manifest.integrations.items() if k in entity_ids},
        configs={k: v for k, v in manifest.configs.items() if k in entity_ids},
        claims={k: v for k, v in manifest.claims.items() if k in entity_ids},
        tables={k: v for k, v in manifest.tables.items() if k in entity_ids},
        events={k: v for k, v in manifest.events.items() if k in entity_ids},
        forms={k: v for k, v in manifest.forms.items() if k in entity_ids},
        agents={k: v for k, v in manifest.agents.items() if k in entity_ids},
        apps={k: v for k, v in manifest.apps.items() if k in entity_ids},
        mcp_servers={k: v for k, v in manifest.mcp_servers.items() if k in entity_ids},
    )


# =============================================================================
# Split-file serialize / parse
# =============================================================================


def serialize_manifest_dir(manifest: Manifest) -> dict[str, str]:
    """Serialize a Manifest into per-entity-type YAML files.

    Returns ``{filename: yaml_content}`` for non-empty entity types.
    Empty entity types are omitted (no file created).
    """
    data = manifest.model_dump(mode="json", exclude_defaults=True, by_alias=True)
    files: dict[str, str] = {}
    for key, filename in MANIFEST_FILES.items():
        section = data.get(key)
        if not section:
            continue
        # Sort top-level entity dicts by key (UUID) for deterministic YAML output
        if isinstance(section, dict):
            section = dict(sorted(section.items()))
        files[filename] = yaml.dump(
            {key: section},
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        ).rstrip("\n") + "\n"
    return files


def parse_manifest_dir(files: dict[str, str]) -> Manifest:
    """Parse split YAML files into a single Manifest.

    ``files`` maps filename → YAML content (e.g. ``{"workflows.yaml": "..."}``).
    Missing files are treated as empty.
    """
    merged: dict[str, object] = {}
    for key, filename in MANIFEST_FILES.items():
        content = files.get(filename, "")
        if not content or not content.strip():
            continue
        data = yaml.safe_load(content)
        if data and isinstance(data, dict):
            merged[key] = data.get(key)
    return Manifest(**merged)  # type: ignore[arg-type]


def write_manifest_to_dir(manifest: Manifest, bifrost_dir: Path) -> None:
    """Write split manifest files to a directory. Removes legacy metadata.yaml."""
    bifrost_dir.mkdir(parents=True, exist_ok=True)

    files = serialize_manifest_dir(manifest)
    for filename, content in files.items():
        (bifrost_dir / filename).write_text(content)

    # Remove split files that are now empty (entity type was cleared)
    for filename in MANIFEST_FILES.values():
        if filename not in files:
            path = bifrost_dir / filename
            if path.exists():
                path.unlink()

    # Clean up legacy single-file manifest
    legacy = bifrost_dir / MANIFEST_LEGACY_FILE
    if legacy.exists():
        legacy.unlink()


def read_manifest_from_dir(bifrost_dir: Path) -> Manifest:
    """Read manifest from a directory, auto-detecting split vs legacy format.

    Detection: if any split file exists, use split format.
    Otherwise fall back to legacy metadata.yaml.
    Empty/missing directory returns empty Manifest.
    """
    if not bifrost_dir.exists():
        return Manifest()

    # Check for split files
    split_files: dict[str, str] = {}
    for filename in MANIFEST_FILES.values():
        path = bifrost_dir / filename
        if path.exists():
            split_files[filename] = path.read_text()

    if split_files:
        return parse_manifest_dir(split_files)

    # Fall back to legacy single file
    legacy = bifrost_dir / MANIFEST_LEGACY_FILE
    if legacy.exists():
        return parse_manifest(legacy.read_text())

    return Manifest()


# =============================================================================
# Validation
# =============================================================================


def validate_manifest(manifest: Manifest) -> list[str]:
    """
    Validate cross-references within the manifest.

    Checks:
    - All organization_id references point to declared organizations
    - All role references point to declared roles
    - Integration data_provider refs point to declared workflows
    - Config integration_id refs point to declared integrations
    - Table org/app refs point to declared entities
    - Event source webhook integration_id refs point to declared integrations
    - Event subscription workflow_id refs point to declared workflows

    Returns a list of human-readable error strings. Empty list = valid.
    """
    errors: list[str] = []

    org_ids = {org.id for org in manifest.organizations}
    role_ids = {role.id for role in manifest.roles}
    wf_ids = {wf.id for wf in manifest.workflows.values()}
    integration_ids = {integ.id for integ in manifest.integrations.values()}
    agent_ids = {a.id for a in manifest.agents.values()}

    # Check organization references
    for _key, wf in manifest.workflows.items():
        wf_label = wf.name or wf.id
        if wf.organization_id and wf.organization_id not in org_ids:
            errors.append(f"Workflow '{wf_label}' references unknown organization: {wf.organization_id}")
        for role_id in wf.roles:
            if role_id not in role_ids:
                errors.append(f"Workflow '{wf_label}' references unknown role: {role_id}")

    for _key, form in manifest.forms.items():
        form_label = form.name or form.id
        if form.organization_id and form.organization_id not in org_ids:
            errors.append(f"Form '{form_label}' references unknown organization: {form.organization_id}")
        for role_id in form.roles:
            if role_id not in role_ids:
                errors.append(f"Form '{form_label}' references unknown role: {role_id}")

    for _key, agent in manifest.agents.items():
        agent_label = agent.name or agent.id
        if agent.organization_id and agent.organization_id not in org_ids:
            errors.append(f"Agent '{agent_label}' references unknown organization: {agent.organization_id}")
        for role_id in agent.roles:
            if role_id not in role_ids:
                errors.append(f"Agent '{agent_label}' references unknown role: {role_id}")

    for _key, app in manifest.apps.items():
        app_label = app.name or app.id
        if app.organization_id and app.organization_id not in org_ids:
            errors.append(f"App '{app_label}' references unknown organization: {app.organization_id}")
        for role_id in app.roles:
            if role_id not in role_ids:
                errors.append(f"App '{app_label}' references unknown role: {role_id}")

    # Integrations: data_provider must be a known workflow
    for _key, integ in manifest.integrations.items():
        integ_label = integ.name or integ.id
        if integ.list_entities_data_provider_id and integ.list_entities_data_provider_id not in wf_ids:
            errors.append(
                f"Integration '{integ_label}' references unknown data provider workflow: "
                f"{integ.list_entities_data_provider_id}"
            )
        for mapping in integ.mappings:
            if mapping.organization_id and mapping.organization_id not in org_ids:
                errors.append(
                    f"Integration '{integ_label}' mapping references unknown organization: "
                    f"{mapping.organization_id}"
                )

    # Configs: integration_id and organization_id
    for key, cfg in manifest.configs.items():
        if cfg.integration_id and cfg.integration_id not in integration_ids:
            errors.append(f"Config '{key}' references unknown integration: {cfg.integration_id}")
        if cfg.organization_id and cfg.organization_id not in org_ids:
            errors.append(f"Config '{key}' references unknown organization: {cfg.organization_id}")

    # Claims: organization_id
    for _key, claim in manifest.claims.items():
        if claim.organization_id not in org_ids:
            errors.append(
                f"Custom claim '{claim.name}' references unknown organization: "
                f"{claim.organization_id}"
            )

    # Tables: organization_id only (Table.application_id was removed)
    for _key, table in manifest.tables.items():
        table_label = table.name or table.id
        if table.organization_id and table.organization_id not in org_ids:
            errors.append(f"Table '{table_label}' references unknown organization: {table.organization_id}")

    # MCP Servers: organization_id refs and per-connection org refs
    for _key, server in manifest.mcp_servers.items():
        server_label = server.name or server.id
        if server.organization_id and server.organization_id not in org_ids:
            errors.append(
                f"MCP server '{server_label}' references unknown organization: "
                f"{server.organization_id}"
            )
        for conn_id, conn in server.connections.items():
            if conn.organization_id not in org_ids:
                errors.append(
                    f"MCP connection '{conn_id}' (under server '{server_label}') "
                    f"references unknown organization: {conn.organization_id}"
                )

    # Events: source + subscription refs
    for _key, evt in manifest.events.items():
        evt_label = evt.name or evt.id
        if evt.organization_id and evt.organization_id not in org_ids:
            errors.append(f"Event source '{evt_label}' references unknown organization: {evt.organization_id}")
        if evt.webhook_integration_id and evt.webhook_integration_id not in integration_ids:
            errors.append(
                f"Event source '{evt_label}' references unknown webhook integration: "
                f"{evt.webhook_integration_id}"
            )
        for sub in evt.subscriptions:
            if sub.target_type == "agent":
                if sub.agent_id and sub.agent_id not in agent_ids:
                    errors.append(
                        f"Event source '{evt_label}' subscription '{sub.id}' references unknown agent: "
                        f"{sub.agent_id}"
                    )
            else:
                if sub.workflow_id and sub.workflow_id not in wf_ids:
                    errors.append(
                        f"Event source '{evt_label}' subscription '{sub.id}' references unknown workflow: "
                        f"{sub.workflow_id}"
                    )

    return errors


# =============================================================================
# Utilities
# =============================================================================


def get_all_entity_ids(manifest: Manifest) -> set[str]:
    """Get all entity UUIDs declared in the manifest."""
    ids: set[str] = set()
    for wf in manifest.workflows.values():
        ids.add(wf.id)
    for integ in manifest.integrations.values():
        ids.add(integ.id)
    for cfg in manifest.configs.values():
        ids.add(cfg.id)
    for claim in manifest.claims.values():
        ids.add(claim.id)
    for table in manifest.tables.values():
        ids.add(table.id)
    for evt in manifest.events.values():
        ids.add(evt.id)
        for sub in evt.subscriptions:
            ids.add(sub.id)
    for form in manifest.forms.values():
        ids.add(form.id)
    for agent in manifest.agents.values():
        ids.add(agent.id)
    for app in manifest.apps.values():
        ids.add(app.id)
    for server in manifest.mcp_servers.values():
        ids.add(server.id)
        for connection_id in server.connections.keys():
            ids.add(connection_id)
    return ids


def get_all_paths(manifest: Manifest) -> set[str]:
    """Get all file paths declared in the manifest.

    Form/agent ``path`` is deprecated and may be ``None`` once the manifest is
    regenerated under the inline-content layout — those entries are skipped.
    """
    paths: set[str] = set()
    for wf in manifest.workflows.values():
        paths.add(wf.path)
    for form in manifest.forms.values():
        if form.path:
            paths.add(form.path)
    for agent in manifest.agents.values():
        if agent.path:
            paths.add(agent.path)
    for app in manifest.apps.values():
        paths.add(app.path)
    return paths
