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

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

MANIFEST_FILES: dict[str, str] = {
    "organizations": "organizations.yaml",
    "roles": "roles.yaml",
    "workflows": "workflows.yaml",
    "integrations": "integrations.yaml",
    "configs": "configs.yaml",
    "tables": "tables.yaml",
    "events": "events.yaml",
    "forms": "forms.yaml",
    "agents": "agents.yaml",
    "apps": "apps.yaml",
}
MANIFEST_LEGACY_FILE = "metadata.yaml"


# =============================================================================
# Pydantic Models
# =============================================================================


class ManifestOrganization(BaseModel):
    """Organization entry in manifest."""
    id: str
    name: str
    is_active: bool = True


class ManifestRole(BaseModel):
    """Role entry in manifest."""
    id: str
    name: str


class ManifestWorkflow(BaseModel):
    """Workflow entry in manifest."""
    id: str = Field(description="Workflow UUID")
    name: str = Field(default="", description="Workflow display name")
    path: str = Field(description="Relative path to Python file (e.g. 'workflows/onboard.py')")
    function_name: str = Field(description="Python function name decorated with @workflow/@tool/@data_provider")
    type: str = Field(default="workflow", description="workflow | tool | data_provider")
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)")
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this workflow")
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
    )
    access_level: str = Field(default="authenticated", description="role_based | authenticated | public")
    endpoint_enabled: bool = Field(default=False, description="Expose as HTTP API endpoint")
    timeout_seconds: int = Field(default=1800, description="Max execution time in seconds. 0 = no timeout. Default 1800 (30 min), max 86400 (24h).")
    public_endpoint: bool = Field(default=False, description="Allow unauthenticated API access")
    description: str | None = Field(default=None, description="Workflow description")
    category: str = Field(default="General", description="Category for organization")
    tags: list[str] = Field(default_factory=list, description="Tags for filtering")


class ManifestForm(BaseModel):
    """Form entry in manifest.

    Carries portable form content (description, workflow bindings, schema) inline
    under the form's UUID. Environment-specific fields (organization_id, roles,
    access_level) live alongside but are NOT serialized into a portable artifact —
    they describe how this environment binds the form. The ``path`` field is
    deprecated: content is now inline and ``forms/{uuid}.form.yaml`` is no longer
    written by the manifest generator.
    """
    id: str = Field(description="Form UUID")
    name: str = Field(default="", description="Form display name")
    path: str | None = Field(
        default=None,
        description="DEPRECATED: relative path to form YAML. Content is now inline.",
    )
    # -- Environment-specific fields (NOT portable; do not include when sharing) --
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)")
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this form")
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | public")
    # -- Portable content (inline) --
    description: str | None = Field(default=None, description="Form description")
    workflow_id: str | None = Field(default=None, description="Workflow UUID to execute on submit")
    launch_workflow_id: str | None = Field(default=None, description="Workflow UUID to run on form load")
    default_launch_params: dict | None = Field(default=None, description="Default params for launch workflow")
    allowed_query_params: list[str] | None = Field(default=None, description="Form fields populatable via URL query params")
    form_schema: dict | None = Field(default=None, description="Form schema (fields list, etc.)")


class ManifestAgent(BaseModel):
    """Agent entry in manifest.

    Carries portable agent content (system prompt, channels, tool bindings, etc.)
    inline under the agent's UUID. Environment-specific fields (organization_id,
    roles, access_level) live alongside but are NOT serialized into a portable
    artifact. The ``path`` field is deprecated: content is now inline and
    ``agents/{uuid}.agent.yaml`` is no longer written by the manifest generator.
    """
    id: str = Field(description="Agent UUID")
    name: str = Field(default="", description="Agent display name")
    path: str | None = Field(
        default=None,
        description="DEPRECATED: relative path to agent YAML. Content is now inline.",
    )
    # -- Environment-specific fields (NOT portable; do not include when sharing) --
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)")
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this agent")
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | public")
    # -- Portable content (inline) --
    description: str | None = Field(default=None, description="Agent description")
    system_prompt: str | None = Field(default=None, description="LLM system prompt")
    channels: list[str] = Field(default_factory=list, description="Channels the agent runs on (chat, email, …)")
    tool_ids: list[str] = Field(default_factory=list, description="Workflow UUIDs exposed as tools")
    delegated_agent_ids: list[str] = Field(default_factory=list, description="Agent UUIDs this agent can delegate to")
    knowledge_sources: list[str] = Field(default_factory=list, description="Knowledge namespaces searchable via RAG")
    system_tools: list[str] = Field(default_factory=list, description="System tool names enabled (e.g. 'execute_workflow')")
    llm_model: str | None = Field(default=None, description="Override LLM model (null = global default)")
    llm_max_tokens: int | None = Field(default=None, description="Override LLM max tokens (null = global default)")
    max_iterations: int | None = Field(default=None, description="Max LLM iterations for autonomous runs")
    max_token_budget: int | None = Field(default=None, description="Max token budget for autonomous runs")


class ManifestApp(BaseModel):
    """App entry in manifest."""
    id: str = Field(description="App UUID")
    path: str = Field(description="App source directory (e.g. 'apps/my-dashboard'), not app.yaml")
    slug: str | None = Field(default=None, description="URL slug (auto-generated from name if omitted)")
    name: str | None = Field(default=None, description="Display name")
    description: str | None = Field(default=None, description="App description")
    dependencies: dict[str, str] = Field(default_factory=dict, description="NPM packages {name: version}")
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)")
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this app")
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | public")


# -- New entity types for manifest expansion --


class ManifestIntegrationConfigSchema(BaseModel):
    """Config schema item within an integration."""
    key: str = Field(description="Config key name")
    type: str = Field(description="string | int | bool | json | secret")
    required: bool = Field(default=False, description="Whether this config must be set")
    description: str | None = Field(default=None, description="Human-readable description")
    options: list[str] | None = Field(default=None, description="Allowed values (for string type)")
    position: int = Field(default=0, description="Display order in UI")


class ManifestOAuthProvider(BaseModel):
    """OAuth provider structure within an integration.

    client_id uses "__NEEDS_SETUP__" sentinel for new instances.
    client_secret is never serialized.
    """
    provider_name: str = Field(description="Provider identifier")
    display_name: str | None = Field(default=None, description="UI display name")
    oauth_flow_type: str = Field(default="authorization_code", description="OAuth flow type")
    client_id: str = Field(default="__NEEDS_SETUP__", description="OAuth client ID (set via UI)")
    authorization_url: str | None = Field(default=None, description="OAuth authorization endpoint")
    token_url: str | None = Field(default=None, description="OAuth token endpoint")
    token_url_defaults: dict | None = Field(default=None, description="Default params for token request")
    scopes: list[str] = Field(default_factory=list, description="OAuth scopes")
    redirect_uri: str | None = Field(default=None, description="OAuth redirect URI")


class ManifestIntegrationMapping(BaseModel):
    """Integration mapping to an org + external entity."""
    organization_id: str | None = Field(default=None, description="Org UUID this mapping belongs to")
    entity_id: str = Field(description="External entity identifier (e.g. tenant ID)")
    entity_name: str | None = Field(default=None, description="Display name for the entity")
    oauth_token_id: str | None = Field(default=None, description="Linked OAuth token (set via UI)")


class ManifestIntegration(BaseModel):
    """Integration entry in manifest."""
    id: str = Field(description="Integration UUID")
    name: str = Field(default="", description="Integration display name")
    entity_id: str | None = Field(default=None, description="Field name for entity identifier")
    entity_id_name: str | None = Field(default=None, description="Display label for entity ID field")
    default_entity_id: str | None = Field(default=None, description="Default entity ID value")
    list_entities_data_provider_id: str | None = Field(default=None, description="Workflow UUID for entity dropdown")
    config_schema: list[ManifestIntegrationConfigSchema] = Field(default_factory=list, description="Configuration fields")
    oauth_provider: ManifestOAuthProvider | None = Field(default=None, description="OAuth provider config")
    mappings: list[ManifestIntegrationMapping] = Field(default_factory=list, description="Per-org entity mappings")


class ManifestConfig(BaseModel):
    """Config entry in manifest."""
    id: str = Field(description="Config UUID")
    integration_id: str | None = Field(default=None, description="Parent integration UUID (if integration config)")
    key: str = Field(description="Config key name")
    config_type: str = Field(default="string", description="string | int | bool | json | secret")
    description: str | None = Field(default=None, description="Human-readable description")
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)")
    value: object | None = Field(default=None, description="Config value (null for secret type)")


class ManifestTable(BaseModel):
    """Table entry in manifest.

    Uses ``table_schema`` in Python but serializes as ``schema`` in YAML
    via the alias, matching the DB column name.
    """
    id: str = Field(description="Table UUID")
    name: str = Field(default="", description="Table display name")
    description: str | None = Field(default=None, description="Table description")
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)")
    application_id: str | None = Field(default=None, description="App UUID (for app-scoped tables)")
    table_schema: dict | None = Field(default=None, alias="schema", description="Column definitions and validation hints")

    model_config = {"populate_by_name": True}


class ManifestEventSubscription(BaseModel):
    """Event subscription within an event source."""
    id: str = Field(description="Subscription UUID")
    target_type: str = Field(default="workflow", description="'workflow' or 'agent'")
    workflow_id: str | None = Field(default=None, description="Workflow UUID to trigger (when target_type='workflow')")
    agent_id: str | None = Field(default=None, description="Agent UUID to run (when target_type='agent')")
    event_type: str | None = Field(default=None, description="Filter by event type (e.g. 'ticket.created')")
    filter_expression: str | None = Field(default=None, description="JSONPath filter expression")
    input_mapping: dict | None = Field(default=None, description="Map event fields to workflow params")
    is_active: bool = Field(default=True, description="Enable/disable this subscription")


class ManifestEventSource(BaseModel):
    """Event source entry in manifest."""
    id: str = Field(description="Event source UUID")
    name: str = Field(default="", description="Event source display name")
    source_type: str = Field(description="webhook | schedule | internal")
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)")
    is_active: bool = Field(default=True, description="Enable/disable this source")
    # Schedule config
    cron_expression: str | None = Field(default=None, description="Cron schedule (e.g. '0 9 * * *')")
    timezone: str | None = Field(default=None, description="Timezone (e.g. 'America/New_York')")
    schedule_enabled: bool | None = Field(default=None, description="Enable/disable schedule")
    overlap_policy: str | None = Field(default=None, description="Overlap policy: skip | queue | replace")
    # Webhook config
    adapter_name: str | None = Field(default=None, description="Webhook adapter (e.g. 'generic', 'halopsa')")
    webhook_integration_id: str | None = Field(default=None, description="Integration UUID for webhook auth")
    webhook_config: dict | None = Field(default=None, description="Adapter-specific config")
    rate_limit_per_minute: int | None = Field(default=60, description="Max events per window. Null disables.")
    rate_limit_window_seconds: int = Field(default=60, description="Window in seconds.")
    rate_limit_enabled: bool = Field(default=True, description="Per-source kill switch.")
    # Subscriptions
    subscriptions: list[ManifestEventSubscription] = Field(default_factory=list, description="Workflow subscriptions")


class Manifest(BaseModel):
    """The complete workspace manifest."""
    organizations: list[ManifestOrganization] = Field(default_factory=list)
    roles: list[ManifestRole] = Field(default_factory=list)
    workflows: dict[str, ManifestWorkflow] = Field(default_factory=dict)
    integrations: dict[str, ManifestIntegration] = Field(default_factory=dict)
    configs: dict[str, ManifestConfig] = Field(default_factory=dict)
    tables: dict[str, ManifestTable] = Field(default_factory=dict)
    events: dict[str, ManifestEventSource] = Field(default_factory=dict)
    forms: dict[str, ManifestForm] = Field(default_factory=dict)
    agents: dict[str, ManifestAgent] = Field(default_factory=dict)
    apps: dict[str, ManifestApp] = Field(default_factory=dict)


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
        tables={k: v for k, v in manifest.tables.items() if k in entity_ids},
        events={k: v for k, v in manifest.events.items() if k in entity_ids},
        forms={k: v for k, v in manifest.forms.items() if k in entity_ids},
        agents={k: v for k, v in manifest.agents.items() if k in entity_ids},
        apps={k: v for k, v in manifest.apps.items() if k in entity_ids},
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
    app_ids = {app.id for app in manifest.apps.values()}
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

    # Tables: organization_id and application_id
    for _key, table in manifest.tables.items():
        table_label = table.name or table.id
        if table.organization_id and table.organization_id not in org_ids:
            errors.append(f"Table '{table_label}' references unknown organization: {table.organization_id}")
        if table.application_id and table.application_id not in app_ids:
            errors.append(f"Table '{table_label}' references unknown application: {table.application_id}")

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
