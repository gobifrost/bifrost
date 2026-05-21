"""
Form, FormField, and FormRole ORM models.

Represents forms, form fields, and form role associations.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SQLAlchemyEnum, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import FormAccessLevel
from src.models.orm.base import Base



class FormField(Base):
    """Form field database table."""

    __tablename__ = "form_fields"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    form_id: Mapped[UUID] = mapped_column(
        ForeignKey("forms.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), default=None)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # Optional field properties
    placeholder: Mapped[str | None] = mapped_column(String(500), default=None)
    help_text: Mapped[str | None] = mapped_column(Text, default=None)
    default_value: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # For select/radio fields
    options: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # For data provider integration
    # NOTE: FK now points to workflows table where type='data_provider'
    # (migrated from data_providers table in 20260103_000000)
    data_provider_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL", onupdate="CASCADE"), default=None
    )
    data_provider_inputs: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # Advanced features
    visibility_expression: Mapped[str | None] = mapped_column(Text, default=None)
    validation: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # For file fields
    allowed_types: Mapped[list | None] = mapped_column(ARRAY(Text), default=None)
    multiple: Mapped[bool | None] = mapped_column(Boolean, default=None)
    max_size_mb: Mapped[int | None] = mapped_column(Integer, default=None)

    # For markdown/html fields
    content: Mapped[str | None] = mapped_column(Text, default=None)

    # Allow field value to be populated from URL query parameters
    allow_as_query_param: Mapped[bool | None] = mapped_column(Boolean, default=None)

    # Auto-fill sibling fields from data provider metadata
    auto_fill: Mapped[dict | None] = mapped_column(JSONB, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    form: Mapped["Form"] = relationship(back_populates="fields")


class Form(Base):
    """Form database table."""

    __tablename__ = "forms"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    workflow_id: Mapped[str | None] = mapped_column(String(255), default=None)
    launch_workflow_id: Mapped[str | None] = mapped_column(String(255), default=None)
    default_launch_params: Mapped[dict | None] = mapped_column(JSONB, default=None)
    allowed_query_params: Mapped[list | None] = mapped_column(JSONB, default=None)
    access_level: Mapped[FormAccessLevel] = mapped_column(
        SQLAlchemyEnum(
            FormAccessLevel,
            name="form_access_level",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=FormAccessLevel.ROLE_BASED,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Metadata for file sync
    module_path: Mapped[str | None] = mapped_column(String(500), default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Cross-environment portability references
    # These fields store the path/function identity of the linked workflow,
    # allowing forms to find their workflow in different environments where
    # workflow UUIDs may differ but path+function_name remain stable.
    workflow_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    workflow_function_name: Mapped[str | None] = mapped_column(String(255), default=None)

    # Relationships
    organization: Mapped["Organization | None"] = relationship(back_populates="forms")
    executions: Mapped[list["Execution"]] = relationship(back_populates="form")
    fields: Mapped[list["FormField"]] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="FormField.position",
    )
    embed_secrets: Mapped[list["FormEmbedSecret"]] = relationship(
        "FormEmbedSecret", back_populates="form", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__: tuple = ()


class FormRole(Base):
    """Form-Role association table."""

    __tablename__ = "form_roles"

    form_id: Mapped[UUID] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    assigned_by: Mapped[str] = mapped_column(String(255))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
