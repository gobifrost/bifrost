"""Small shared request and response models used outside a single API domain."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SolutionDevContextRequest(BaseModel):
    """The selected portable app identity for a local Solution preview."""

    app_id: UUID


class SolutionDevContextResponse(BaseModel):
    """Short-lived, app-bound context accepted only as a supplemental header."""

    token: str
    expires_at: float
    solution_id: UUID
    organization_id: UUID | None
    app_id: UUID
