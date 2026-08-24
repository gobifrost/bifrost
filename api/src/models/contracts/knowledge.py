"""
Knowledge namespace and document contract models for Bifrost.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ==================== KNOWLEDGE NAMESPACE MODELS ====================


class KnowledgeNamespaceInfo(BaseModel):
    """Namespace info derived from knowledge_store document counts."""
    namespace: str
    document_count: int = 0
    global_count: int = 0
    org_count: int = 0


class KnowledgeNamespaceRolePublic(BaseModel):
    """Knowledge namespace role assignment output."""
    id: str
    namespace: str
    organization_id: str | None = None
    role_id: str
    assigned_by: str | None = None


class KnowledgeNamespaceRoleCreate(BaseModel):
    """Request model for assigning roles to a namespace."""
    namespace: str
    role_ids: list[str]
    organization_id: str | None = None


# ==================== KNOWLEDGE DOCUMENT MODELS ====================


class KnowledgeDocumentCreate(BaseModel):
    """Request model for creating a knowledge document."""
    content: str = Field(..., min_length=1, max_length=500000, description="Markdown content")
    key: str | None = Field(default=None, max_length=255, description="Optional key for upsert")
    metadata: dict = Field(default_factory=dict)


class KnowledgeDocumentUpdate(BaseModel):
    """Request model for updating a knowledge document."""
    content: str = Field(..., min_length=1, max_length=500000, description="Markdown content")
    metadata: dict | None = None


class KnowledgeDocumentPublic(BaseModel):
    """Knowledge document output for API responses."""

    id: str
    namespace: str
    key: str | None = None
    content: str
    metadata: dict = Field(default_factory=dict)
    organization_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeDocumentBulkScopeUpdate(BaseModel):
    """Request model for bulk-updating document scope."""
    document_ids: list[str] = Field(..., min_length=1, max_length=500)
    scope: str = Field(..., description="Target scope: 'global' or an org UUID")
    replace: bool = Field(default=False, description="Replace conflicting documents in target scope")


class KnowledgeDocumentSummary(BaseModel):
    """Lightweight document summary (no full content)."""

    id: str
    namespace: str
    key: str | None = None
    content_preview: str = Field(default="", description="First ~200 chars of content")
    metadata: dict = Field(default_factory=dict)
    organization_id: str | None = None
    created_at: datetime | None = None


class KnowledgeSearchRequest(BaseModel):
    """Canonical direct or Agent-bound knowledge-search request."""

    query: str = Field(..., min_length=1, description="Search query")
    namespace: list[str] | None = Field(
        default=None,
        description=(
            "Namespaces to search. Direct searches default to 'default'; "
            "Agent-bound searches default to every namespace granted to the Agent."
        ),
    )
    limit: int = Field(default=5, ge=1, le=100, description="Maximum results")
    min_score: float | None = Field(default=None, ge=0, le=1)
    metadata_filter: dict[str, Any] | None = None
    scope: str | None = Field(
        default=None,
        description="Direct-search scope: omitted, 'global', or an organization UUID",
    )
    agent_id: UUID | None = Field(
        default=None,
        description=(
            "Accessible Agent whose organization and knowledge grants define "
            "the search boundary"
        ),
    )
    fallback: bool = Field(
        default=True,
        description="Also search global knowledge when searching an organization",
    )


class KnowledgeSearchResult(BaseModel):
    """One canonical hybrid knowledge-search result."""

    id: str
    namespace: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None
    organization_id: str | None = None
    key: str | None = None
    created_at: datetime | None = None
