"""Pydantic types for Custom Claims — query-resolved facts about the caller."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from src.models.contracts.policies import Expr

ClaimType = Literal["list", "scalar"]


class ClaimQuery(BaseModel):
    """The lookup that produces a claim's value for the calling user."""

    table: str = Field(min_length=1, description="Source table name (org-scoped)")
    where: Expr | None = Field(default=None, description="Filter AST; same shape as policies")
    select: str = Field(min_length=1, description="Column or JSON path on the source table")


class CustomClaimBase(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="lower_snake; unique per org/global repo scope or per solution install",
    )
    description: str | None = None
    type: ClaimType = "list"
    query: ClaimQuery


class CustomClaimCreate(CustomClaimBase):
    """Create-shape; organization_id is taken from the caller's context."""


class CustomClaimUpdate(BaseModel):
    """Partial update; all fields optional."""

    description: str | None = None
    type: ClaimType | None = None
    query: ClaimQuery | None = None


class CustomClaim(CustomClaimBase):
    """Read-shape returned by REST."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID | None = None
    solution_id: UUID | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_solution_managed(self) -> bool:
        return self.solution_id is not None


class ClaimsList(BaseModel):
    claims: list[CustomClaim] = Field(default_factory=list)
