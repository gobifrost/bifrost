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
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field

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
    "file_policies": "file-policies.yaml",
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
    def from_row(cls, org) -> "ManifestOrganization":
        return cls(id=str(org.id), name=org.name, is_active=org.is_active)

    def to_orm_values(self, dest: Destination) -> ImportFields:
        return ImportFields(direct={"id": self.id, "name": self.name, "is_active": self.is_active})


class ManifestRole(EntityCodec, BaseModel):
    """Role entry in manifest."""
    id: str = Field(**classify(FieldClass.IDENTITY))
    name: str = Field(**classify(FieldClass.CONTENT, match_key=True))

    @classmethod
    def from_row(cls, role) -> "ManifestRole":
        return cls(id=str(role.id), name=role.name)

    def to_orm_values(self, dest: Destination) -> ImportFields:
        return ImportFields(direct={"id": self.id, "name": self.name})


class ManifestWorkflow(EntityCodec, BaseModel):
    """Workflow entry in manifest."""
    id: str = Field(description="Workflow UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="MCP tool name; defaults to function_name on registration", **classify(FieldClass.CONTENT))
    path: str = Field(description="Relative path to Python file (e.g. 'workflows/onboard.py')", **classify(FieldClass.CONTENT, match_key=True))
    function_name: str = Field(description="Python function name decorated with @workflow/@tool/@data_provider", **classify(FieldClass.CONTENT, match_key=True))
    type: str = Field(default="workflow", description="workflow | tool | data_provider", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    # roles/role_names are ENVIRONMENT but install MUST carry them — they are the
    # access grant the deployer re-binds in the target org (capture also passes
    # them via extras). keep_empty_list mirrors the legacy view's `x or []`.
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this workflow", **classify(FieldClass.ENVIRONMENT, install_view="keep_empty_list"))
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
    tags: list[str] = Field(default_factory=list, description="Tags for filtering", **classify(FieldClass.CONTENT, install_view="keep_empty_list"))

    @classmethod
    def from_row(cls, wf, *, roles: list[str] | None = None) -> "ManifestWorkflow":
        """Build from a Workflow ORM row, mirroring serialize_workflow exactly."""
        return cls(
            id=str(wf.id),
            name=wf.name,
            path=wf.path,
            function_name=wf.function_name,
            type=wf.type or "workflow",
            description=wf.description,
            tool_description=wf.tool_description,
            organization_id=str(wf.organization_id) if wf.organization_id else None,
            roles=roles or [],
            access_level=wf.access_level or "authenticated",
            endpoint_enabled=wf.endpoint_enabled or False,
            # NOT `or 1800` — 0 means "no timeout" and `or` would clobber it.
            timeout_seconds=wf.timeout_seconds if wf.timeout_seconds is not None else 1800,
            public_endpoint=wf.public_endpoint or False,
            category=wf.category or "General",
            tags=wf.tags or [],
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        """Column values for ORM upsert — all direct (no indexer split).

        GIT_SYNC: mirrors _resolve_workflow's wf_values.
          description / tool_description omitted when None (resolver only sets
          them when the manifest explicitly provides them).
          name supplied as self.name; resolver overrides with manifest_name.
        INSTALL: mirrors _upsert_workflows values dict.
          description / tool_description always present (full-replace — clearing
          is intentional on redeploy).
          access_level present-only (absent = leave DB column at its default).
        """
        if dest is Destination.GIT_SYNC:
            direct: dict = {
                "name": self.name,
                "function_name": self.function_name,
                "path": self.path,
                "type": self.type,
                "is_active": True,
                "organization_id": self.organization_id,
                "endpoint_enabled": self.endpoint_enabled,
                "timeout_seconds": self.timeout_seconds,
                "public_endpoint": self.public_endpoint,
                "category": self.category,
                "tags": self.tags,
                "access_level": self.access_level,
            }
            if self.description is not None:
                direct["description"] = self.description
            if self.tool_description is not None:
                direct["tool_description"] = self.tool_description
            return ImportFields(direct=direct)
        # INSTALL
        direct = {
            "name": self.name,
            "function_name": self.function_name,
            "path": self.path,
            "type": self.type,
            "is_active": True,
            "description": self.description,
            "tool_description": self.tool_description,
            "endpoint_enabled": self.endpoint_enabled,
            "public_endpoint": self.public_endpoint,
            "timeout_seconds": self.timeout_seconds,
            "category": self.category,
            "tags": self.tags or [],
        }
        if self.access_level is not None:
            direct["access_level"] = self.access_level
        return ImportFields(direct=direct)


class ManifestForm(EntityCodec, BaseModel):
    """Form entry in manifest.

    Carries portable form content (description, workflow bindings, schema) inline
    under the form's UUID. Environment-specific fields (organization_id, roles,
    access_level) live alongside but are NOT serialized into a portable artifact —
    they describe how this environment binds the form. The ``path`` field is
    deprecated: content is now inline and ``forms/{uuid}.form.yaml`` is no longer
    written by the manifest generator.

    Import is INDEXER-ONLY (to_orm_values returns only indexer_content):
    - indexer_content: id, name (always), + description/workflow_id/launch_workflow_id/
      default_launch_params/allowed_query_params/form_schema (drop-none) — fed to FormIndexer.
    organization_id/access_level are re-stamped on the Form row directly AFTER the
    indexer by the importers (manifest_import/deploy) — orchestration-owned, NOT
    sourced from this method. (The ``import_owner="restamp"`` tags on those fields
    document that import role; the partition dict no longer carries them.)
    """
    id: str = Field(description="Form UUID", **classify(FieldClass.IDENTITY, import_owner="indexer"))
    name: str = Field(default="", description="Form display name", **classify(FieldClass.CONTENT, import_owner="indexer"))
    path: str | None = Field(
        default=None,
        description="DEPRECATED: relative path to form YAML. Content is now inline.",
        **classify(FieldClass.CONTENT, install_view="drop"),  # deprecated; install omits it
    )
    # -- Environment-specific fields (NOT portable; do not include when sharing) --
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT, import_owner="restamp"))
    # roles is ENVIRONMENT but install carries the access grant the deployer re-binds.
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this form", **classify(FieldClass.ENVIRONMENT, install_view="keep_empty_list"))
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
        **classify(FieldClass.ENVIRONMENT, keep_on_portable=True),
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | everyone | public", **classify(FieldClass.CONTENT, import_owner="restamp"))
    # -- Portable content (inline) --
    description: str | None = Field(default=None, description="Form description", **classify(FieldClass.CONTENT, import_owner="indexer"))
    workflow_id: str | None = Field(default=None, description="Workflow UUID to execute on submit", **classify(FieldClass.REFERENCE, import_owner="indexer"))
    launch_workflow_id: str | None = Field(default=None, description="Workflow UUID to run on form load", **classify(FieldClass.REFERENCE, import_owner="indexer"))
    default_launch_params: dict | None = Field(default=None, description="Default params for launch workflow", **classify(FieldClass.CONTENT, import_owner="indexer"))
    allowed_query_params: list[str] | None = Field(default=None, description="Form fields populatable via URL query params", **classify(FieldClass.CONTENT, import_owner="indexer"))
    form_schema: dict | None = Field(default=None, description="Form schema (fields list, etc.)", **classify(FieldClass.CONTENT, import_owner="indexer"))

    @classmethod
    def from_row(cls, form, *, roles: list[str] | None = None, fields: list | None = None) -> "ManifestForm":
        """Build from a Form ORM row, mirroring serialize_form exactly.

        ``fields`` should be the FormField rows for this form, ordered by position.
        They are inlined into ``form_schema.fields`` via ``_form_field_to_schema_dict``
        (the manifest_generator helper — same shape as the git_sync writer).
        """
        from src.services.manifest_generator import _form_field_to_schema_dict

        schema: dict | None = None
        if fields:
            schema = {"fields": [_form_field_to_schema_dict(f) for f in fields]}

        return cls(
            id=str(form.id),
            name=form.name,
            organization_id=str(form.organization_id) if form.organization_id else None,
            roles=roles or [],
            access_level=form.access_level.value if form.access_level else "role_based",
            description=form.description,
            workflow_id=form.workflow_id,
            launch_workflow_id=form.launch_workflow_id,
            default_launch_params=form.default_launch_params,
            allowed_query_params=form.allowed_query_params,
            form_schema=schema,
        )

    def to_orm_values(self, dest: "Destination") -> "ImportFields":
        """Column values for import — INDEXER-ONLY (only indexer_content is emitted).

        indexer_content: id + name (always present) + the indexer-owned fields
          drop-none (description, workflow_id, launch_workflow_id, default_launch_params,
          allowed_query_params, form_schema). EXACTLY matches _form_content_from_manifest.
        organization_id/access_level are re-stamped on the Form row directly AFTER
        the indexer by _index_forms_from_manifest and _upsert_forms — orchestration
        owns that, so this method does not carry direct/restamp.
        """

        indexer: dict = {"id": self.id, "name": self.name or ""}
        if self.description is not None:
            indexer["description"] = self.description
        if self.workflow_id is not None:
            indexer["workflow_id"] = self.workflow_id
        if self.launch_workflow_id is not None:
            indexer["launch_workflow_id"] = self.launch_workflow_id
        if self.default_launch_params is not None:
            indexer["default_launch_params"] = self.default_launch_params
        if self.allowed_query_params is not None:
            indexer["allowed_query_params"] = self.allowed_query_params
        if self.form_schema is not None:
            indexer["form_schema"] = self.form_schema

        # Form import is INDEXER-ONLY: the importers (manifest_import
        # _index_forms_from_manifest, deploy _upsert_forms) feed indexer_content to
        # the FormIndexer, then re-stamp organization_id/access_level directly off
        # the manifest entry AFTER the indexer runs (the values aren't carried in
        # the indexer YAML). That post-index re-stamp is orchestration-owned — it
        # is NOT sourced from this method — so direct/restamp stay empty here rather
        # than expose an authoritative-looking surface nothing consumes.
        return ImportFields(indexer_content=indexer)


class ManifestAgent(EntityCodec, BaseModel):
    """Agent entry in manifest.

    Carries portable agent content (system prompt, channels, tool bindings, etc.)
    inline under the agent's UUID. Environment-specific fields (organization_id,
    roles, access_level) live alongside but are NOT serialized into a portable
    artifact. The ``path`` field is deprecated: content is now inline and
    ``agents/{uuid}.agent.yaml`` is no longer written by the manifest generator.

    Import is INDEXER-ONLY (to_orm_values returns only indexer_content):
    - indexer_content: id, name (always), + description/system_prompt/channels/
      tool_ids/delegated_agent_ids/knowledge_sources/system_tools/mcp_connection_ids/
      llm_model/llm_max_tokens (non-empty lists only, drop-none scalars) — fed to AgentIndexer.
    The importers resolve id/name/system_prompt on the metadata row and re-stamp
    access_level/max_iterations/max_token_budget (+ the max_run_timeout transport
    extra) directly AFTER the indexer; that direct-set + re-stamp is
    orchestration-owned (manifest_import/deploy), NOT sourced from this method.
    """
    id: str = Field(description="Agent UUID", **classify(FieldClass.IDENTITY, import_owner="direct"))
    name: str = Field(default="", description="Agent display name", **classify(FieldClass.CONTENT, import_owner="direct"))
    path: str | None = Field(
        default=None,
        description="DEPRECATED: relative path to agent YAML. Content is now inline.",
        # import_owner is the "direct" default and inert here (to_orm_values is
        # hardcoded and never emits path to import) — stated explicitly for
        # annotation uniformity with the other classified fields.
        # install_view="drop": deprecated; the install bundle never carried it.
        **classify(FieldClass.CONTENT, import_owner="direct", install_view="drop"),
    )
    # -- Environment-specific fields (NOT portable; do not include when sharing) --
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    # roles/role_names are ENVIRONMENT but install carries the access grant.
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this agent", **classify(FieldClass.ENVIRONMENT, install_view="keep"))
    role_names: list[str] | None = Field(
        default=None,
        description="Role display names (used by portable bundles; resolved to UUIDs on import)",
        **classify(FieldClass.ENVIRONMENT, keep_on_portable=True, install_view="keep_empty_list"),
    )
    access_level: str | None = Field(default=None, description="role_based | authenticated | everyone | public", **classify(FieldClass.CONTENT, import_owner="restamp"))
    # -- Portable content (inline) --
    description: str | None = Field(default=None, description="Agent description", **classify(FieldClass.CONTENT, import_owner="indexer"))
    system_prompt: str | None = Field(default=None, description="LLM system prompt", **classify(FieldClass.CONTENT, import_owner="direct"))
    channels: list[str] = Field(default_factory=list, description="Channels the agent runs on (chat, email, …)", **classify(FieldClass.CONTENT, import_owner="indexer"))
    tool_ids: list[str] = Field(default_factory=list, description="Workflow UUIDs exposed as tools", **classify(FieldClass.REFERENCE, import_owner="indexer"))
    delegated_agent_ids: list[str] = Field(default_factory=list, description="Agent UUIDs this agent can delegate to", **classify(FieldClass.REFERENCE, import_owner="indexer"))
    knowledge_sources: list[str] = Field(default_factory=list, description="Knowledge namespaces searchable via RAG", **classify(FieldClass.CONTENT, import_owner="indexer", install_view="keep_empty_list"))
    system_tools: list[str] = Field(default_factory=list, description="System tool names enabled (e.g. 'execute_workflow')", **classify(FieldClass.CONTENT, import_owner="indexer", install_view="keep_empty_list"))
    mcp_connection_ids: list[str] = Field(
        default_factory=list,
        description=(
            "MCP connection UUIDs explicitly granted to this agent. Empty "
            "list means the agent surfaces no external MCP tools."
        ),
        # install_view="drop": git_sync carries the grants; the install bundle
        # omits them (env-scoped grants deployed via _sync_agent_mcp_connections).
        **classify(FieldClass.REFERENCE, import_owner="indexer", install_view="drop"),
    )
    llm_model: str | None = Field(default=None, description="Override LLM model (null = global default)", **classify(FieldClass.CONTENT, import_owner="indexer"))
    llm_max_tokens: int | None = Field(default=None, description="Override LLM max tokens (null = global default)", **classify(FieldClass.CONTENT, import_owner="indexer"))
    # indexer-owned (carried in _agent_content_from_manifest / the AgentIndexer
    # YAML, per the spike's INDEXER_CONTENT_FIELDS); deploy/git-sync ALSO re-stamp
    # them after the indexer as a belt-and-suspenders safety (the Slice-2 fix), so
    # they appear in BOTH to_orm_values.indexer_content and .restamp. The tag
    # reflects their primary classification (indexer); the re-stamp is orchestration.
    max_iterations: int | None = Field(default=None, description="Max LLM iterations for autonomous runs", **classify(FieldClass.CONTENT, import_owner="indexer"))
    max_token_budget: int | None = Field(default=None, description="Max token budget for autonomous runs", **classify(FieldClass.CONTENT, import_owner="indexer"))

    @classmethod
    def from_row(
        cls,
        agent,
        *,
        roles: list[str] | None = None,
        tool_ids: Sequence[str | UUID] | None = None,
        delegated_agent_ids: Sequence[str | UUID] | None = None,
        mcp_connection_ids: Sequence[str | UUID] | None = None,
    ) -> "ManifestAgent":
        """Build from an Agent ORM row, mirroring serialize_agent exactly.

        ``tool_ids`` / ``delegated_agent_ids`` / ``mcp_connection_ids`` are passed
        in (rather than read from relationships) so the caller controls
        eager-loading and ordering — matching the pattern used for workflow/form roles.
        """
        return cls(
            id=str(agent.id),
            name=agent.name,
            organization_id=str(agent.organization_id) if agent.organization_id else None,
            roles=roles or [],
            access_level=agent.access_level.value if agent.access_level else "role_based",
            description=agent.description,
            system_prompt=agent.system_prompt,
            channels=list(agent.channels) if agent.channels else [],
            # Junction ids arrive as UUIDs from solution capture (_junction_ids)
            # and as strings from the git-sync generator — coerce so both callers
            # satisfy the list[str] fields without per-caller stringification.
            tool_ids=[str(t) for t in (tool_ids or [])],
            delegated_agent_ids=[str(d) for d in (delegated_agent_ids or [])],
            knowledge_sources=list(agent.knowledge_sources) if agent.knowledge_sources else [],
            system_tools=list(agent.system_tools) if agent.system_tools else [],
            mcp_connection_ids=[str(m) for m in (mcp_connection_ids or [])],
            llm_model=agent.llm_model,
            llm_max_tokens=agent.llm_max_tokens,
            max_iterations=agent.max_iterations,
            max_token_budget=agent.max_token_budget,
        )

    def to_orm_values(self, dest: "Destination") -> "ImportFields":
        """Column values for the three-way import partition.

        indexer_content: id + name (always present, name forced "") + indexer-owned
          fields drop-none; non-empty lists only (channels/tool_ids/etc.).
          mcp_connection_ids included when non-empty (git_sync carries it).
          EXACTLY matches _agent_content_from_manifest.
        direct: id, name, system_prompt — the scalar fields resolved directly by
          _resolve_agent before the indexer runs.
        restamp: access_level, max_iterations, max_token_budget — applied AFTER the
          indexer by both _index_agents_from_manifest and _upsert_agents.
          max_run_timeout is a transport extra (not a model field) — callers read it
          from the bundle dict key directly (deploy.py _upsert_agents).
        """
        indexer: dict = {"id": self.id, "name": self.name or ""}
        if self.description is not None:
            indexer["description"] = self.description
        if self.system_prompt is not None:
            indexer["system_prompt"] = self.system_prompt
        if self.channels:
            indexer["channels"] = list(self.channels)
        if self.tool_ids:
            indexer["tool_ids"] = list(self.tool_ids)
        if self.delegated_agent_ids:
            indexer["delegated_agent_ids"] = list(self.delegated_agent_ids)
        if self.knowledge_sources:
            indexer["knowledge_sources"] = list(self.knowledge_sources)
        if self.system_tools:
            indexer["system_tools"] = list(self.system_tools)
        if self.mcp_connection_ids:
            indexer["mcp_connection_ids"] = list(self.mcp_connection_ids)
        if self.llm_model is not None:
            indexer["llm_model"] = self.llm_model
        if self.llm_max_tokens is not None:
            indexer["llm_max_tokens"] = self.llm_max_tokens
        if self.max_iterations is not None:
            indexer["max_iterations"] = self.max_iterations
        if self.max_token_budget is not None:
            indexer["max_token_budget"] = self.max_token_budget

        # Agent import is INDEXER-ONLY: the importers (manifest_import
        # _resolve_agent/_index_agents_from_manifest, deploy _upsert_agents) feed
        # indexer_content to the AgentIndexer, then resolve id/name/system_prompt on
        # the metadata row and re-stamp access_level/max_iterations/max_token_budget
        # (and the max_run_timeout transport extra) directly AFTER the indexer. That
        # direct-set + re-stamp is orchestration-owned — it is NOT sourced from this
        # method — so direct/restamp stay empty rather than expose an
        # authoritative-looking surface nothing consumes.
        return ImportFields(indexer_content=indexer)


class ManifestApp(EntityCodec, BaseModel):
    """App entry in manifest."""
    id: str = Field(description="App UUID", **classify(FieldClass.IDENTITY))
    # install emits the transport extra `repo_path` instead of `path` — drop the
    # model field from the install view so it isn't duplicated.
    path: str = Field(description="App source directory (e.g. 'apps/my-dashboard'), not app.yaml", **classify(FieldClass.CONTENT, install_view="drop"))
    slug: str | None = Field(default=None, description="URL slug (auto-generated from name if omitted)", **classify(FieldClass.CONTENT, match_key=True))
    name: str | None = Field(default=None, description="Display name", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="App description", **classify(FieldClass.CONTENT))
    dependencies: dict[str, str] = Field(default_factory=dict, description="NPM packages {name: version}", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT))
    # roles is ENVIRONMENT but install carries the access grant (drop-none; never None via from_row).
    roles: list[str] = Field(default_factory=list, description="Role UUIDs that can access this app", **classify(FieldClass.ENVIRONMENT, install_view="keep"))
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
        # install never carried the logo path-string field — the bytes ride as the
        # logo_b64/logo_content_type transport extras. drop matches the legacy view
        # and guards against a future from_row that populates logo.
        **classify(FieldClass.CONTENT, install_view="drop"),
    )

    @classmethod
    def from_row(cls, app, *, roles: list[str] | None = None) -> "ManifestApp":
        """Build from an Application ORM row, mirroring serialize_app exactly.

        NOTE: ``path`` is set from ``app.repo_path`` (the manifest field is
        ``path``; the ORM column is ``repo_path``).  serialize_app does the
        same mapping.  ``logo`` (the path-string manifest field) is left None —
        the ORM stores ``logo_data`` bytes, not a path string.  The bytes travel
        as the transport extra ``logo_b64`` via ``extras=`` in _install_view.
        """
        return cls(
            id=str(app.id),
            path=app.repo_path.rstrip("/"),
            slug=app.slug,
            name=app.name,
            description=app.description,
            dependencies=app.dependencies or {},
            organization_id=str(app.organization_id) if app.organization_id else None,
            roles=roles or [],
            access_level=app.access_level if app.access_level else "authenticated",
            app_model=app.app_model or "inline_v1",
        )

    def to_orm_values(self, dest: "Destination") -> "ImportFields":
        """Column values for ORM upsert — all direct (no indexer split).

        GIT_SYNC: mirrors _resolve_app's app_values dict.
          ``repo_path`` comes from self.path (manifest field ``path`` maps to
          ORM column ``repo_path``).  access_level present-only.
          app_model always present.
        INSTALL: mirrors _upsert_apps values dict.
          repo_path = self.path.  description/dependencies always present
          (full-replace on redeploy).  access_level present-only.
        """
        from uuid import UUID

        from bifrost.manifest_codec import Destination, ImportFields

        if dest is Destination.GIT_SYNC:
            direct: dict = {
                "name": self.name or "",
                "description": self.description,
                "slug": self.slug,
                "repo_path": self.path,
                "organization_id": UUID(self.organization_id) if self.organization_id else None,
                "dependencies": self.dependencies or None,
                "app_model": self.app_model or "inline_v1",
            }
            if self.access_level is not None:
                direct["access_level"] = self.access_level
            return ImportFields(direct=direct)
        # INSTALL
        direct = {
            "name": self.name or self.slug or "",
            "slug": self.slug,
            "repo_path": self.path or f"apps/{self.slug}",
            "description": self.description,
            "dependencies": self.dependencies or None,
            "app_model": self.app_model or "inline_v1",
        }
        if self.access_level is not None:
            direct["access_level"] = self.access_level
        return ImportFields(direct=direct)


# -- New entity types for manifest expansion --


class ManifestIntegrationConfigSchema(EntityCodec, BaseModel):
    """Config schema item within an integration."""
    key: str = Field(description="Config key name", **classify(FieldClass.CONTENT, match_key=True))
    type: str = Field(description="string | int | bool | json | secret", **classify(FieldClass.CONTENT))
    required: bool = Field(default=False, description="Whether this config must be set", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    options: list[str] | None = Field(default=None, description="Allowed values (for string type)", **classify(FieldClass.CONTENT))
    position: int = Field(default=0, description="Display order in UI", **classify(FieldClass.CONTENT))

    @classmethod
    def from_row(cls, cs) -> "ManifestIntegrationConfigSchema":
        """Mirror serialize_integration config_schema item in manifest_generator.py."""
        return cls(
            key=cs.key,
            type=cs.type,
            required=cs.required,
            description=cs.description,
            options=cs.options,
            position=cs.position,
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        raise NotImplementedError(
            "ManifestIntegrationConfigSchema has no standalone orm path; "
            "child reconciliation is handled by _resolve_integration"
        )


class ManifestOAuthProvider(EntityCodec, BaseModel):
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

    @classmethod
    def from_row(cls, op) -> "ManifestOAuthProvider":
        """Mirror serialize_integration oauth_provider in manifest_generator.py.

        client_secret is NEVER serialized (security).
        """
        return cls(
            provider_name=op.provider_name,
            display_name=op.display_name,
            oauth_flow_type=op.oauth_flow_type,
            client_id=op.client_id or "__NEEDS_SETUP__",
            authorization_url=op.authorization_url,
            token_url=op.token_url,
            token_url_defaults=op.token_url_defaults or None,
            scopes=op.scopes or [],
            redirect_uri=op.redirect_uri,
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        raise NotImplementedError(
            "ManifestOAuthProvider has no standalone orm path; "
            "upsert is handled inline by _resolve_integration"
        )


class ManifestIntegrationMapping(EntityCodec, BaseModel):
    """Integration mapping to an org + external entity."""
    organization_id: str | None = Field(default=None, description="Org UUID this mapping belongs to", **classify(FieldClass.ENVIRONMENT))
    entity_id: str = Field(description="External entity identifier (e.g. tenant ID)", **classify(FieldClass.REFERENCE))
    entity_name: str | None = Field(default=None, description="Display name for the entity", **classify(FieldClass.CONTENT))
    oauth_token_id: str | None = Field(default=None, description="Linked OAuth token (set via UI)", **classify(FieldClass.REFERENCE))

    @classmethod
    def from_row(cls, im) -> "ManifestIntegrationMapping":
        """Mirror serialize_integration mappings item in manifest_generator.py."""
        return cls(
            organization_id=str(im.organization_id) if im.organization_id else None,
            entity_id=im.entity_id,
            entity_name=im.entity_name,
            oauth_token_id=str(im.oauth_token_id) if im.oauth_token_id else None,
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        raise NotImplementedError(
            "ManifestIntegrationMapping has no standalone orm path; "
            "upsert is handled inline by _resolve_integration"
        )


class ManifestIntegration(EntityCodec, BaseModel):
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

    @classmethod
    def from_row(
        cls,
        integ,
        *,
        config_schema=None,
        oauth_provider=None,
        mappings=None,
    ) -> "ManifestIntegration":
        """Mirror serialize_integration in manifest_generator.py exactly."""
        return cls(
            id=str(integ.id),
            name=integ.name,
            entity_id=integ.entity_id,
            entity_id_name=integ.entity_id_name,
            default_entity_id=integ.default_entity_id,
            list_entities_data_provider_id=(
                str(integ.list_entities_data_provider_id)
                if integ.list_entities_data_provider_id else None
            ),
            config_schema=[
                ManifestIntegrationConfigSchema.from_row(cs)
                for cs in (config_schema or [])
            ],
            oauth_provider=(
                ManifestOAuthProvider.from_row(oauth_provider)
                if oauth_provider else None
            ),
            mappings=[
                ManifestIntegrationMapping.from_row(im)
                for im in (mappings or [])
            ],
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        if dest is not Destination.GIT_SYNC:
            raise NotImplementedError(
                "ManifestIntegration has no install path — "
                "install uses connection_schema templates (_upsert_integration_shells), "
                "a different shape outside this entity's view scope."
            )
        return ImportFields(
            direct={
                "id": self.id,
                "name": self.name,
                "entity_id": self.entity_id,
                "entity_id_name": self.entity_id_name,
                "default_entity_id": self.default_entity_id,
                "list_entities_data_provider_id": self.list_entities_data_provider_id,
            },
        )


class ManifestConfig(EntityCodec, BaseModel):
    """Config entry in manifest."""
    id: str = Field(description="Config UUID", **classify(FieldClass.IDENTITY))
    integration_id: str | None = Field(default=None, description="Parent integration UUID (if integration config)", **classify(FieldClass.REFERENCE, match_key=True))
    key: str = Field(description="Config key name", **classify(FieldClass.CONTENT, match_key=True))
    config_type: str = Field(default="string", description="string | int | bool | json | secret", **classify(FieldClass.CONTENT))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, description="Org UUID (null = global)", **classify(FieldClass.ENVIRONMENT, match_key=True))
    value: object | None = Field(default=None, description="Config value (null for secret type)", **classify(FieldClass.CONTENT, predicate="config_value"))

    @classmethod
    def from_row(cls, cfg) -> "ManifestConfig":
        """Mirror serialize_config in manifest_generator.py."""
        from src.models.enums import ConfigType

        config_type = (
            cfg.config_type.value
            if cfg.config_type and hasattr(cfg.config_type, "value")
            else (cfg.config_type or "string")
        )
        value = (
            None
            if (cfg.config_type == ConfigType.SECRET or str(cfg.config_type) == "secret")
            else cfg.value
        )
        return cls(
            id=str(cfg.id),
            integration_id=str(cfg.integration_id) if cfg.integration_id else None,
            key=cfg.key,
            config_type=config_type,
            description=cfg.description,
            organization_id=str(cfg.organization_id) if cfg.organization_id else None,
            value=value,
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        if dest is not Destination.GIT_SYNC:
            raise NotImplementedError(f"Config has no install path; dest={dest}")
        return ImportFields(
            direct={
                "id": self.id,
                "key": self.key,
                "integration_id": self.integration_id,
                "organization_id": self.organization_id,
                "config_type": self.config_type,
                "value": self.value,
                "description": self.description,
            },
            indexer_content={},
            restamp={},
        )


class ManifestSolutionConfigSchema(EntityCodec, BaseModel):
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

    @classmethod
    def from_row(cls, cs) -> "ManifestSolutionConfigSchema":
        """Build from a SolutionConfigSchema ORM row, mirroring capture._config_entries."""
        return cls(
            id=str(cs.id),
            key=cs.key,
            type=cs.type,
            required=cs.required,
            description=cs.description,
            default=cs.default,
            position=cs.position,
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        """Column values for ORM upsert — all direct (no indexer split).

        INSTALL: key/type/required/description/default/position (not id — remapped
          by _remapped_bundle; not solution_id — stamped by caller).
        GIT_SYNC: raises NotImplementedError (install-only entity; no git-sync path).
        """
        if dest is Destination.GIT_SYNC:
            raise NotImplementedError(
                "ManifestSolutionConfigSchema has no git-sync path; it is install-only."
            )
        # INSTALL
        return ImportFields(
            direct={
                "key": self.key,
                "type": self.type,
                "required": self.required,
                "description": self.description,
                "default": self.default,
                "position": self.position,
            },
            indexer_content={},
            restamp={},
        )


class ManifestCustomClaim(EntityCodec, BaseModel):
    """Custom Claim entry in manifest."""
    id: str = Field(description="Custom Claim UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(description="Claim name, unique per org", **classify(FieldClass.CONTENT, match_key=True))
    description: str | None = Field(default=None, description="Human-readable description", **classify(FieldClass.CONTENT))
    organization_id: str = Field(description="Org UUID", **classify(FieldClass.ENVIRONMENT, match_key=True))
    type: Literal["list", "scalar"] = Field(default="list", description="list | scalar", **classify(FieldClass.CONTENT))
    query: ClaimQuery = Field(description="Source table query that resolves the claim", **classify(FieldClass.CONTENT))

    @classmethod
    def from_row(cls, claim) -> "ManifestCustomClaim":
        """Build from a CustomClaim ORM row, mirroring serialize_custom_claim exactly."""
        return cls(
            id=str(claim.id),
            name=claim.name,
            description=claim.description,
            organization_id=str(claim.organization_id),
            type=claim.type,  # type: ignore[arg-type]
            query=ClaimQuery.model_validate(claim.query),
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        """Column values for ORM upsert — all direct (no indexer split).

        GIT_SYNC: mirrors _resolve_custom_claim column set.
          query serialized as model_dump(mode="json") — the resolver reads
          mclaim.query.model_dump(mode="json").
        INSTALL: mirrors _upsert_claims values dict.
          organization_id ABSENT — stamped by deployer from solution.organization_id.
          query serialized as model_dump(mode="json") — deployer re-validates via
          ClaimQuery.model_validate(mclaim["query"]).model_dump().
        """
        query_json = self.query.model_dump(mode="json")
        if dest is Destination.GIT_SYNC:
            return ImportFields(
                direct={
                    "id": self.id,
                    "name": self.name,
                    "description": self.description,
                    "organization_id": self.organization_id,
                    "type": self.type,
                    "query": query_json,
                },
                indexer_content={},
                restamp={},
            )
        # INSTALL
        return ImportFields(
            direct={
                "id": self.id,
                "name": self.name,
                "description": self.description,
                "type": self.type,
                "query": query_json,
            },
            indexer_content={},
            restamp={},
        )


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


class ManifestPolicyRef(BaseModel):
    """A named-rule reference within a table or file policy list.

    Stored as ``{"$ref": "rule_name"}`` in manifest YAML and preserved verbatim
    in the JSONB column (never expanded/inlined at write time). Resolution happens
    at read time (websocket policy load) and at write-time validation (deploy,
    manifest import) to fail closed on unresolvable refs.

    Mirrors :class:`src.models.contracts.policies.PolicyRuleRef`.
    """
    ref: str = Field(alias="$ref", min_length=1, max_length=100)
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ManifestTable(EntityCodec, BaseModel):
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
    policies: list[ManifestPolicy | ManifestPolicyRef] | None = Field(
        default=None,
        description="Access policies (flat list). Entries may be inline policies or named-rule refs ({\"$ref\": name}). When null on import, the seed admin_bypass policy is written.",
        **classify(FieldClass.CONTENT),
    )

    model_config = {"populate_by_name": True}

    @classmethod
    def from_row(cls, table) -> "ManifestTable":
        """Build from a Table ORM row, mirroring serialize_table exactly.

        Unwraps the JSONB ``Table.access`` payload (shape: ``{"policies": [...]}``)
        into a flat list of ManifestPolicy. Tables with no access blob serialize
        with ``policies=None``; the importer reseeds those on the next round-trip.
        """
        access = table.access if isinstance(table.access, dict) else None
        raw_policies = access.get("policies") if access else None
        if raw_policies:
            entries: list[ManifestPolicy | ManifestPolicyRef] = []
            for p in raw_policies:
                if isinstance(p, dict) and ("$ref" in p or "ref" in p):
                    # Stored as a named-rule ref — preserve the $ref form.
                    ref_val = p.get("$ref") or p.get("ref")
                    entries.append(ManifestPolicyRef(**{"$ref": ref_val}))
                else:
                    entries.append(ManifestPolicy.model_validate(p))
            policies: list[ManifestPolicy | ManifestPolicyRef] | None = entries
        else:
            policies = None
        return cls(
            id=str(table.id),
            name=table.name,
            description=table.description,
            organization_id=str(table.organization_id) if table.organization_id else None,
            policies=policies,
            **{"schema": table.schema},  # type: ignore[arg-type]  # alias for table_schema
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        """Column values for ORM upsert — all direct (no indexer split).

        GIT_SYNC: mirrors _resolve_table column set.
          id/name/organization_id/description/table_schema→``schema`` column.
          policies→``access`` JSONB wrapping + TablePolicies validation +
          default admin_bypass seed STAY in the resolver (not here).
        INSTALL: mirrors _upsert_tables values dict.
          description and schema always present (full-replace — clearing is
          intentional on redeploy).  policies absent (deploy reads raw from dict).
        """
        if dest is Destination.GIT_SYNC:
            return ImportFields(
                direct={
                    "id": self.id,
                    "name": self.name,
                    "organization_id": self.organization_id,
                    "description": self.description,
                    "schema": self.table_schema,
                    "policies": self.policies,
                },
                indexer_content={},
                restamp={},
            )
        # INSTALL
        return ImportFields(
            direct={
                "name": self.name,
                "description": self.description,
                "schema": self.table_schema,
            },
            indexer_content={},
            restamp={},
        )


class ManifestFilePolicy(EntityCodec, BaseModel):
    """File policy entry in manifest.

    File policy rows are keyed by UUID and scoped by ``(organization_id,
    location, path)``. ``organization_id=None`` represents the global policy
    row; an org UUID represents an org-scoped override.
    """

    id: str = Field(description="File policy UUID", **classify(FieldClass.IDENTITY))
    organization_id: str | None = Field(
        description="Org UUID (null = global)",
        **classify(FieldClass.ENVIRONMENT, match_key=True),
    )
    location: str = Field(
        description="File storage location, e.g. workspace or shared",
        **classify(FieldClass.CONTENT, match_key=True),
    )
    path: str = Field(
        description="Path prefix relative to the location root",
        **classify(FieldClass.CONTENT, match_key=True),
    )
    policies: list[dict[str, Any]] = Field(
        default_factory=list,
        description="File access policy documents for this location/path",
        **classify(FieldClass.CONTENT),
    )

    @classmethod
    def from_row(cls, file_policy) -> "ManifestFilePolicy":
        raw_policies = file_policy.policies if isinstance(file_policy.policies, dict) else {}
        return cls(
            id=str(file_policy.id),
            organization_id=(
                str(file_policy.organization_id)
                if file_policy.organization_id
                else None
            ),
            location=file_policy.location,
            path=file_policy.path,
            policies=raw_policies.get("policies", []),
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        return ImportFields(
            direct={
                "id": self.id,
                "organization_id": self.organization_id,
                "location": self.location,
                "path": self.path,
                "policies": self.policies,
            },
            indexer_content={},
            restamp={},
        )


class ManifestEventSubscription(EntityCodec, BaseModel):
    """Event subscription within an event source."""
    id: str = Field(description="Subscription UUID", **classify(FieldClass.IDENTITY))
    target_type: str = Field(default="workflow", description="'workflow' or 'agent'", **classify(FieldClass.CONTENT))
    workflow_id: str | None = Field(default=None, description="Workflow UUID to trigger (when target_type='workflow')", **classify(FieldClass.REFERENCE))
    agent_id: str | None = Field(default=None, description="Agent UUID to run (when target_type='agent')", **classify(FieldClass.REFERENCE))
    event_type: str | None = Field(default=None, description="Filter by event type (e.g. 'ticket.created')", **classify(FieldClass.CONTENT))
    filter_expression: str | None = Field(default=None, description="JSONPath filter expression", **classify(FieldClass.CONTENT))
    input_mapping: dict | None = Field(default=None, description="Map event fields to workflow params", **classify(FieldClass.CONTENT))
    is_active: bool = Field(default=True, description="Enable/disable this subscription", **classify(FieldClass.ENVIRONMENT))

    @classmethod
    def from_row(cls, sub) -> "ManifestEventSubscription":
        """Build from an EventSubscription ORM row."""
        return cls(
            id=str(sub.id),
            target_type=sub.target_type or "workflow",
            workflow_id=str(sub.workflow_id) if sub.workflow_id else None,
            agent_id=str(sub.agent_id) if sub.agent_id else None,
            event_type=sub.event_type,
            filter_expression=sub.filter_expression,
            input_mapping=sub.input_mapping,
            is_active=sub.is_active,
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        raise NotImplementedError("EventSubscription rows are built by the parent resolver/deploy")


class ManifestEventSource(EntityCodec, BaseModel):
    """Event source entry in manifest."""
    id: str = Field(description="Event source UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="Event source display name", **classify(FieldClass.CONTENT))
    source_type: str = Field(description="webhook | schedule | topic", **classify(FieldClass.CONTENT))
    event_type: str | None = Field(default=None, description="Topic routing key (e.g. 'ticket.created'); topic sources only", **classify(FieldClass.CONTENT))
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

    @classmethod
    def from_row(
        cls,
        es,
        *,
        schedule=None,
        webhook=None,
        subscriptions=None,
    ) -> "ManifestEventSource":
        """Build from EventSource ORM row + optional child rows, mirroring serialize_event_source exactly."""
        cron_expression = schedule.cron_expression if schedule else None
        tz = schedule.timezone if schedule else None
        schedule_enabled = schedule.enabled if schedule else None
        overlap_policy = schedule.overlap_policy.value if schedule and schedule.overlap_policy else None

        adapter_name = webhook.adapter_name if webhook else None
        webhook_integration_id = str(webhook.integration_id) if webhook and webhook.integration_id else None
        webhook_config = webhook.config if webhook and webhook.config else None
        rate_limit_per_minute = webhook.rate_limit_per_minute if webhook else 60
        rate_limit_window_seconds = webhook.rate_limit_window_seconds if webhook else 60
        rate_limit_enabled = webhook.rate_limit_enabled if webhook else True

        return cls(
            id=str(es.id),
            name=es.name,
            source_type=es.source_type if isinstance(es.source_type, str) else es.source_type.value,
            event_type=es.event_type,
            organization_id=str(es.organization_id) if es.organization_id else None,
            is_active=es.is_active,
            cron_expression=cron_expression,
            timezone=tz,
            schedule_enabled=schedule_enabled,
            overlap_policy=overlap_policy,
            adapter_name=adapter_name,
            webhook_integration_id=webhook_integration_id,
            webhook_config=webhook_config,
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_window_seconds=rate_limit_window_seconds,
            rate_limit_enabled=rate_limit_enabled,
            subscriptions=[
                ManifestEventSubscription.from_row(s) for s in (subscriptions or [])
            ],
        )

    def _install_view(self, extras: dict) -> dict:
        """EventSource install view is the FULL git_sync dump (Nones kept), NOT the
        drop-none class-policy subset every other entity uses.

        This is a deliberate, documented exception: capture._event_entries emits the
        whole serialized source verbatim (adapter_name/webhook_integration_id/etc.
        present as None when absent), and deploy/_upsert_events reads those keys back.
        The generic drop-none install view (EntityCodec._install_view) would omit the
        null keys and structurally cannot emit a top-level ``key: null``, so
        EventSource declares its one structural fact — install ≡ git_sync — in one
        line rather than tagging every nullable field with a keep override.
        """
        return self.view(Destination.GIT_SYNC)

    def to_orm_values(self, dest: Destination) -> ImportFields:
        """Parent EventSource scalar fields only — child rows built by resolver/deploy."""
        direct: dict = {
            "name": self.name,
            "source_type": self.source_type,
            "event_type": self.event_type,
            "organization_id": self.organization_id,
            "is_active": self.is_active,
        }
        return ImportFields(direct=direct, indexer_content={}, restamp={})


class ManifestMCPConnectionTool(EntityCodec, BaseModel):
    """Tool catalog row inside an MCP connection.

    Populated from the vendor's ``tools/list`` and synced into the manifest
    so an importing environment knows the schema of every tool the connection
    is bound to (without re-calling the vendor at import time).
    """
    tool_name: str = Field(description="Tool name as published by the vendor", **classify(FieldClass.CONTENT, match_key=True))
    tool_schema: dict = Field(default_factory=dict, description="JSON schema for the tool", **classify(FieldClass.CONTENT))
    enabled: bool = Field(default=True, description="Whether the tool is enabled in this connection", **classify(FieldClass.CONTENT))
    disabled_reason: str | None = Field(default=None, description="Reason the tool is disabled (admin-set or auto-set)", **classify(FieldClass.CONTENT))

    @classmethod
    def from_row(cls, tool) -> "ManifestMCPConnectionTool":
        """Mirror serialize_mcp_connection_tool in manifest_generator.py."""
        return cls(
            tool_name=tool.tool_name,
            tool_schema=tool.tool_schema or {},
            enabled=tool.enabled,
            disabled_reason=tool.disabled_reason,
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        raise NotImplementedError(
            "ManifestMCPConnectionTool has no standalone orm path; "
            "tool reconciliation is handled by _resolve_mcp_connection"
        )


class ManifestMCPConnection(EntityCodec, BaseModel):
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

    @classmethod
    def from_row(cls, conn, *, tools=None) -> "ManifestMCPConnection":
        """Mirror serialize_mcp_connection in manifest_generator.py.

        ``encrypted_client_secret`` is intentionally omitted — secrets are
        gitignored, the same treatment Config secrets get today.
        """
        return cls(
            organization_id=str(conn.organization_id),
            client_id=conn.client_id,
            server_url_override=conn.server_url_override,
            available_in_chat=conn.available_in_chat,
            available_to_autonomous=conn.available_to_autonomous,
            service_oauth_token_id=(
                str(conn.service_oauth_token_id)
                if conn.service_oauth_token_id
                else None
            ),
            tools=[ManifestMCPConnectionTool.from_row(t) for t in (tools or [])],
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        raise NotImplementedError(
            "ManifestMCPConnection has no standalone orm path; "
            "connection reconciliation is handled by _resolve_mcp_connection"
        )


class ManifestMCPServer(EntityCodec, BaseModel):
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

    @classmethod
    def from_row(
        cls,
        server,
        *,
        connections_by_id=None,
        tools_by_connection=None,
    ) -> "ManifestMCPServer":
        """Mirror serialize_mcp_server in manifest_generator.py.

        Connections nested under the server keyed by connection UUID; each
        connection carries its own tool catalog inline.
        """
        connections = connections_by_id or {}
        tools_lookup = tools_by_connection or {}
        return cls(
            id=str(server.id),
            name=server.name,
            server_url=server.server_url,
            oauth_provider_id=(
                str(server.oauth_provider_id) if server.oauth_provider_id else None
            ),
            redirect_url=server.redirect_url,
            discovery_metadata=server.discovery_metadata,
            organization_id=(
                str(server.organization_id) if server.organization_id else None
            ),
            is_active=server.is_active,
            connections={
                cid: ManifestMCPConnection.from_row(conn, tools=tools_lookup.get(cid, []))
                for cid, conn in connections.items()
            },
        )

    def to_orm_values(self, dest: Destination) -> ImportFields:
        """Parent MCPServer scalar columns only — connections/tools built by resolver."""
        if dest is Destination.INSTALL:
            raise NotImplementedError(
                "ManifestMCPServer has no install path (MCP servers are git-sync only)"
            )
        # GIT_SYNC: scalar columns _resolve_mcp_server sets on the MCPServer ORM row.
        # UUID resolution (oauth_provider_id, organization_id) is done by the resolver
        # which holds DB session context; here we surface the string values so the
        # resolver can parse them with UUID().
        direct: dict = {
            "id": self.id,
            "name": self.name,
            "server_url": self.server_url,
            "oauth_provider_id": self.oauth_provider_id,
            "redirect_url": self.redirect_url,
            "discovery_metadata": self.discovery_metadata,
            "organization_id": self.organization_id,
            "is_active": self.is_active,
        }
        return ImportFields(direct=direct, indexer_content={}, restamp={})


class Manifest(BaseModel):
    """The complete workspace manifest."""
    organizations: list[ManifestOrganization] = Field(default_factory=list)
    roles: list[ManifestRole] = Field(default_factory=list)
    workflows: dict[str, ManifestWorkflow] = Field(default_factory=dict)
    integrations: dict[str, ManifestIntegration] = Field(default_factory=dict)
    configs: dict[str, ManifestConfig] = Field(default_factory=dict)
    claims: dict[str, ManifestCustomClaim] = Field(default_factory=dict)
    tables: dict[str, ManifestTable] = Field(default_factory=dict)
    file_policies: dict[str, ManifestFilePolicy] = Field(default_factory=dict)
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
        file_policies={
            k: v for k, v in manifest.file_policies.items() if k in entity_ids
        },
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

    # File policies: organization_id only
    for _key, file_policy in manifest.file_policies.items():
        policy_label = f"{file_policy.location}/{file_policy.path}".rstrip("/")
        if file_policy.organization_id and file_policy.organization_id not in org_ids:
            errors.append(
                f"File policy '{policy_label}' references unknown organization: "
                f"{file_policy.organization_id}"
            )

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
    for file_policy in manifest.file_policies.values():
        ids.add(file_policy.id)
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
