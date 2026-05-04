"""
Table and Document contract models for Bifrost App Builder.

Provides Pydantic models for API request/response handling.
"""

import warnings
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)

from src.models.contracts.policies import TablePolicies


# ==================== TABLE MODELS ====================


# Pydantic v2 warns when a field name shadows a BaseModel attribute.
# "schema" is a valid domain name here (table schema) and renaming would
# break the API contract, so suppress the warning for this module.
warnings.filterwarnings("ignore", message='Field name "schema"')


class TableBase(BaseModel):
    """Shared table fields."""

    name: str = Field(
        max_length=255,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="Table name (lowercase, underscores and hyphens allowed)",
    )
    description: str | None = Field(default=None, description="Optional table description")
    schema: dict[str, Any] | None = Field(
        default=None,
        description="Optional schema hints for validation/UI. Not enforced at DB level.",
    )


class TableCreate(TableBase):
    """Input for creating a table."""

    organization_id: UUID | None = Field(
        default=None,
        description="Organization ID. Null for global table.",
    )
    policies: TablePolicies | None = Field(
        default=None,
        description=(
            "Optional row-level access policies. See "
            "docs/superpowers/specs/2026-04-30-table-policies-design.md."
        ),
    )


class TableUpdate(BaseModel):
    """Input for updating a table."""

    name: str | None = Field(
        default=None,
        max_length=255,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="Table name (lowercase, underscores and hyphens allowed)",
    )
    description: str | None = None
    schema: dict[str, Any] | None = None
    policies: TablePolicies | None = Field(
        default=None,
        description=(
            "Optional row-level access policies. See "
            "docs/superpowers/specs/2026-04-30-table-policies-design.md."
        ),
    )


class TablePublic(TableBase):
    """Table output for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID | None
    policies: TablePolicies | None = Field(
        default=None,
        validation_alias=AliasChoices("policies", "access"),
    )
    created_at: datetime
    updated_at: datetime
    created_by: str | None

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class TableListResponse(BaseModel):
    """Response for listing tables."""

    tables: list[TablePublic]
    total: int


# ==================== DOCUMENT MODELS ====================


class DocumentCreate(BaseModel):
    """Input for creating a document."""

    id: str | None = Field(
        default=None,
        description="Optional document ID. Auto-generated (UUID) if omitted.",
    )
    data: dict[str, Any] = Field(..., description="Document data (any JSON-serializable dict)")
    upsert: bool = Field(
        default=False,
        description="If true and id is provided, update the existing document instead of raising a conflict.",
    )
    created_by: str | None = Field(
        default=None,
        description=(
            "Override attribution for created_by. Engine and platform-admin callers only; "
            "any other caller that sends this field receives 403. When omitted, defaults to "
            "the calling user."
        ),
    )
    updated_by: str | None = Field(
        default=None,
        description=(
            "Override attribution for updated_by (used on the update branch of an upsert). "
            "Engine and platform-admin callers only; any other caller that sends this field "
            "receives 403. When omitted, defaults to the calling user."
        ),
    )


class DocumentUpsert(BaseModel):
    """Input for upserting a document by id (atomic INSERT ... ON CONFLICT DO UPDATE)."""

    id: str = Field(..., description="Document ID. Required (the upsert conflict key).")
    data: dict[str, Any] = Field(..., description="Document data (any JSON-serializable dict)")
    created_by: str | None = Field(
        default=None,
        description=(
            "Override attribution for created_by (insert path). Engine and platform-admin "
            "callers only; any other caller that sends this field receives 403."
        ),
    )
    updated_by: str | None = Field(
        default=None,
        description=(
            "Override attribution for updated_by (insert and update paths). "
            "Engine and platform-admin callers only."
        ),
    )


class DocumentBatchItem(BaseModel):
    """A single item in a batch insert or upsert."""

    id: str | None = Field(
        default=None,
        description="Optional document ID. Auto-generated (UUID) if omitted.",
    )
    data: dict[str, Any] = Field(..., description="Document data (any JSON-serializable dict)")
    created_by: str | None = Field(
        default=None,
        description=(
            "Override attribution for created_by. Engine and platform-admin callers only; "
            "any item that sends this field from a non-privileged caller fails the whole batch with 403."
        ),
    )
    updated_by: str | None = Field(
        default=None,
        description=(
            "Override attribution for updated_by (upsert-update branch). "
            "Engine and platform-admin callers only."
        ),
    )


class DocumentBatchCreate(BaseModel):
    """Input for inserting or upserting multiple documents."""

    documents: list[DocumentBatchItem] = Field(..., description="Documents to insert or upsert")
    upsert: bool = Field(
        default=False,
        description="If true, upsert documents with an id instead of inserting.",
    )


class DocumentBatchCreateResponse(BaseModel):
    """Response for a batch insert or upsert."""

    inserted: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    documents: list["DocumentPublic"] = Field(
        default_factory=list,
        description=(
            "Inserted/updated documents in submission order. Lets SDK callers "
            "use auto-generated ids without a follow-up fetch."
        ),
    )


class DocumentBatchUpsertResponse(BaseModel):
    """Response for a batch upsert."""

    upserted: int
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DocumentBatchDeleteRequest(BaseModel):
    """Input for deleting multiple documents by ID."""

    ids: list[str] = Field(..., description="Document IDs to delete")


class DocumentBatchDeleteResponse(BaseModel):
    """Response for a batch delete."""

    deleted: int
    deleted_ids: list[str] = Field(
        default_factory=list,
        description="IDs of documents that were actually deleted (in submission order, skipping ids that didn't exist).",
    )


class DocumentUpdate(BaseModel):
    """Input for updating a document (partial update, merges with existing)."""

    data: dict[str, Any] = Field(..., description="Fields to update (merged with existing data)")
    updated_by: str | None = Field(
        default=None,
        description=(
            "Override attribution for updated_by. Engine and platform-admin callers only; "
            "any other caller that sends this field receives 403. When omitted, defaults to "
            "the calling user."
        ),
    )


class DocumentPublic(BaseModel):
    """Document output for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    table_id: UUID
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class QueryFilter(BaseModel):
    """
    JSON-native query filter with user-friendly operators.

    Supports:
    - eq: Equal (default if just a value is passed)
    - ne: Not equal
    - contains: Case-insensitive substring search
    - starts_with: Starts with (case-insensitive)
    - ends_with: Ends with (case-insensitive)
    - gt: Greater than
    - gte: Greater than or equal
    - lt: Less than
    - lte: Less than or equal
    - in_: Value in list
    - is_null: Check for null
    - has_key: Field exists in document

    Example:
        {
            "status": "active",                    # Implicit eq
            "amount": {"gt": 100, "lte": 1000},   # Range query
            "name": {"contains": "acme"},         # Case-insensitive search
            "category": {"in": ["a", "b", "c"]},  # In list
            "deleted_at": {"is_null": True}       # Null check
        }
    """

    eq: Any | None = None
    ne: Any | None = None
    contains: str | None = None
    starts_with: str | None = None
    ends_with: str | None = None
    gt: Any | None = None
    gte: Any | None = None
    lt: Any | None = None
    lte: Any | None = None
    in_: list[Any] | None = Field(default=None, alias="in")
    is_null: bool | None = None
    has_key: bool | None = None

    model_config = ConfigDict(populate_by_name=True)


class DocumentQuery(BaseModel):
    """Query parameters for document search."""

    where: dict[str, Any] | None = Field(
        default=None,
        description="""Filter conditions. Supports:
        - Simple equality: {"status": "active"}
        - Operators: {"amount": {"gt": 100, "lte": 1000}}
        - Contains: {"name": {"contains": "acme"}}
        - Starts/ends with: {"name": {"starts_with": "a"}}
        - IN: {"category": {"in": ["a", "b"]}}
        - NULL: {"deleted_at": {"is_null": true}}
        - Has field: {"field": {"has_key": true}}
        """,
    )
    order_by: str | None = Field(
        default=None,
        description="Field to order by (data field name)",
    )
    order_dir: Literal["asc", "desc"] = Field(
        default="asc",
        description="Sort direction",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum documents to return",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of documents to skip",
    )
    skip_count: bool = Field(
        default=False,
        description="Skip the total count query (returns total=-1). Use for faster paginated fetches after the first page.",
    )

    @field_validator("order_by")
    @classmethod
    def validate_order_by(cls, v: str | None) -> str | None:
        """Validate order_by field name."""
        if v is not None:
            # Allow nested paths like "data.amount" or simple fields like "created_at"
            if not v.replace(".", "").replace("_", "").isalnum():
                raise ValueError("order_by must be alphanumeric with dots and underscores")
        return v


class DocumentListResponse(BaseModel):
    """Response for document queries."""

    table_id: UUID
    documents: list[DocumentPublic]
    total: int
    limit: int
    offset: int


class DocumentCountResponse(BaseModel):
    """Response for document count."""

    count: int


# ==================== SDK REQUEST MODELS ====================


class SDKTableCreateRequest(BaseModel):
    """SDK request for creating a table."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    table_schema: dict[str, Any] | None = None
    description: str | None = None
    scope: str | None = Field(
        default=None,
        description="Scope: None=context org, 'global'=global, UUID=specific org",
    )
    app: str | None = Field(
        default=None,
        description="Application UUID to scope table to an app",
    )


class SDKTableListRequest(BaseModel):
    """SDK request for listing tables."""

    scope: str | None = None
    app: str | None = Field(
        default=None,
        description="Filter by application UUID",
    )



# Resolve forward reference (DocumentBatchCreateResponse → DocumentPublic).
DocumentBatchCreateResponse.model_rebuild()
