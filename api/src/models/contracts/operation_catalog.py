"""Transport-neutral operation catalog contracts.

The catalog describes one Bifrost product operation independently from the
REST, CLI, MCP, or native Builder binding used to invoke it.  Runtime surface
inventory is intentionally separate: a binding in this model is the canonical
target, while the generated inventory records whether today's implementation
already matches it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OperationAsyncPolicy(StrEnum):
    """Where durable or compute-heavy work for an operation executes."""

    SYNCHRONOUS = "synchronous"
    EXECUTION_WORKER = "execution_worker"
    PLATFORM_JOB = "platform_job"


class OperationTargetKind(StrEnum):
    """Authorization/resource boundary selected by an operation."""

    COLLECTION = "collection"
    RESOURCE = "resource"
    SOLUTION = "solution"
    WORKSPACE = "workspace"
    PLATFORM = "platform"


class RestOperationBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: str
    path: str
    request_model: str | None = None
    response_model: str | None = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DELETE", "GET", "PATCH", "POST", "PUT"}:
            raise ValueError(f"unsupported REST method: {value}")
        return normalized


class CliOperationBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: tuple[str, ...]

    @field_validator("path")
    @classmethod
    def require_resource_and_verb(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) < 2 or any(not part.strip() for part in value):
            raise ValueError("CLI bindings require a resource and verb")
        return value


class McpOperationBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str

    @field_validator("name")
    @classmethod
    def require_bifrost_namespace(cls, value: str) -> str:
        if not value.startswith("bifrost_"):
            raise ValueError("Bifrost MCP tools must use the bifrost_ namespace")
        return value


class ManifestOperationBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity: str
    behavior: str = "reconcile"


class OperationDefinition(BaseModel):
    """Canonical identity, authorization, side effects, and surface bindings."""

    model_config = ConfigDict(frozen=True)

    operation_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$")
    summary: str
    target_kind: OperationTargetKind
    rest: RestOperationBinding
    cli: CliOperationBinding | None = None
    mcp: McpOperationBinding | None = None
    native_builder: bool = False
    manifest: ManifestOperationBinding | None = None
    action_scopes: tuple[str, ...] = ()
    authorization_resolver: str
    audit_event: str | None = None
    side_effects: tuple[str, ...] = ()
    async_policy: OperationAsyncPolicy = OperationAsyncPolicy.SYNCHRONOUS
    exclusions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_surface_dispositions(self) -> "OperationDefinition":
        optional_surfaces = {
            "cli": self.cli,
            "mcp": self.mcp,
            "native_builder": self.native_builder or None,
            "manifest": self.manifest,
        }
        missing = sorted(
            surface
            for surface, binding in optional_surfaces.items()
            if binding is None and not self.exclusions.get(surface, "").strip()
        )
        if missing:
            raise ValueError(
                "missing binding or exclusion reason for: " + ", ".join(missing)
            )
        return self


class OperationSurfaceStatus(StrEnum):
    """Observed parity of one implementation surface against the catalog."""

    EXACT = "exact_parity"
    MISSING = "missing_surface"
    DIVERGENT = "divergent_behavior"
    TRANSPORT_ONLY = "transport_only"
    INTENTIONALLY_UNSUPPORTED = "intentionally_unsupported"


__all__ = [
    "CliOperationBinding",
    "ManifestOperationBinding",
    "McpOperationBinding",
    "OperationAsyncPolicy",
    "OperationDefinition",
    "OperationSurfaceStatus",
    "OperationTargetKind",
    "RestOperationBinding",
]
