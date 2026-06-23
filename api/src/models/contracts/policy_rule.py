from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from src.models.contracts.policies import FilePolicyRule, Policy


class PolicyRuleUsagesFilePolicyItem(BaseModel):
    id: str
    location: str
    path: str
    organization_id: str | None


class PolicyRuleUsagesTableItem(BaseModel):
    id: str
    name: str
    organization_id: str | None


class PolicyRuleUsagesPublic(BaseModel):
    file_policies: list[PolicyRuleUsagesFilePolicyItem]
    tables: list[PolicyRuleUsagesTableItem]
    total: int

Domain = Literal["file", "table"]


def _validate_body(body: dict, domain: str) -> dict:
    """Validate a rule body against its domain by round-tripping through the inline model."""
    probe = {"name": "_probe", "actions": body.get("actions"), "when": body.get("when")}
    (FilePolicyRule if domain == "file" else Policy).model_validate(probe)  # raises on bad actions/when
    return body


class PolicyRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    domain: Domain
    description: str | None = None
    body: dict
    organization_id: UUID | None = None

    @model_validator(mode="after")
    def _check_body(self):
        _validate_body(self.body, self.domain)
        return self


class PolicyRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    body: dict | None = None
    # domain is immutable; body re-validated against the stored domain in the service.


class PolicyRulePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID | None
    name: str
    domain: Domain
    description: str | None
    body: dict
    is_builtin: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None
