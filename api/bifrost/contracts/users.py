"""Minimal CLI-side mirror of role create/update DTOs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


def _normalize_legacy_role_capability_fields(data: Any) -> Any:
    """Accept pre-capability Role inputs while keeping capabilities canonical."""

    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    legacy_scopes = normalized.pop("scopes", None)
    legacy_permissions = normalized.get("permissions")
    capabilities = normalized.get("capabilities")

    if legacy_scopes is not None:
        from bifrost.authorization_legacy import translate_legacy_role_capabilities

        translated_scopes = translate_legacy_role_capabilities(legacy_scopes, None)
        if capabilities is not None and list(capabilities) != translated_scopes:
            raise ValueError("Use either capabilities or matching legacy scopes, not both")
        normalized["capabilities"] = translated_scopes
        capabilities = translated_scopes

    if legacy_permissions not in (None, {}, []):
        from bifrost.authorization_legacy import translate_legacy_role_capabilities

        translated_permissions = translate_legacy_role_capabilities(
            None,
            legacy_permissions,
        )
        normalized["capabilities"] = sorted(
            {*(normalized.get("capabilities") or []), *translated_permissions}
        )

    return normalized


class RoleCreate(BaseModel):
    """Input for creating a role (CLI mirror)."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None)
    capabilities: list[str] = Field(default_factory=list)
    scopes: list[str] | None = Field(
        default=None,
        description="Deprecated alias for capabilities; accepted for compatibility.",
    )
    permissions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        return _normalize_legacy_role_capability_fields(data)


class RoleUpdate(BaseModel):
    """Input for updating a role (CLI mirror)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    capabilities: list[str] | None = Field(default=None)
    scopes: list[str] | None = Field(
        default=None,
        description="Deprecated alias for capabilities; accepted for compatibility.",
    )
    permissions: dict[str, Any] | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        return _normalize_legacy_role_capability_fields(data)
