"""Backward-compat shim for table-specific policy types.

The engine types live in `shared.policies.ast`. This module re-exports
them under tables-specific names AND defines a tables-narrowed `Policy`
class that restricts `actions` to the table action vocab. New code should
import from `shared.table_policies` (TablePolicies alias) or
`shared.policies.ast` (PolicyDocument).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from shared.policies.ast import (
    KNOWN_USER_FIELDS,
    Expr,
    PolicyDocument,
)

# Table-specific action vocab. File-policies has its own.
Action = Literal["read", "create", "update", "delete"]


class Policy(BaseModel):
    """Table-narrowed Policy: actions must be from the table action vocab.

    Same shape as `shared.policies.ast.Policy` but `actions` is restricted
    to the four table actions. Existing call sites continue to import this
    from `src.models.contracts.policies` and get the narrow type.
    """

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    actions: list[Action] = Field(min_length=1)
    when: Expr | None = None

    @field_validator("actions")
    @classmethod
    def _no_dup_actions(cls, v: list[Action]) -> list[Action]:
        if len(set(v)) != len(v):
            raise ValueError("actions must not contain duplicates")
        return v


class TablePolicies(BaseModel):
    """Tables-typed policy document.

    Mirrors `PolicyDocument` shape but uses the tables-narrowed `Policy`
    so action vocab validation survives at this boundary.
    """

    policies: list[Policy] = Field(default_factory=list)


class PolicyValidationError(BaseModel):
    """Single structured validation error for a policy document."""

    path: str
    message: str


class PolicyValidationResponse(BaseModel):
    """Outcome of a POST /api/tables/policies/validate call."""

    ok: bool
    errors: list[PolicyValidationError] = Field(default_factory=list)


__all__ = [
    "KNOWN_USER_FIELDS",
    "Action",
    "Expr",
    "Policy",
    "PolicyDocument",
    "TablePolicies",
    "PolicyValidationError",
    "PolicyValidationResponse",
]
