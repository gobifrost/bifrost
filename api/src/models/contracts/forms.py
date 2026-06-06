"""
Form contract models for Bifrost.
"""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from src.models.enums import FormAccessLevel, FormFieldType
from src.models.contracts.base import DataProviderInputMode
from src.models.contracts.refs import WorkflowRef

if TYPE_CHECKING:
    pass


# ==================== FORM MODELS ====================


class FormFieldValidation(BaseModel):
    """Form field validation rules"""
    pattern: str | None = None
    min: float | None = None
    max: float | None = None
    message: str | None = None


class DataProviderInputConfig(BaseModel):
    """Configuration for a single data provider input parameter (T006)"""
    mode: DataProviderInputMode = Field(
        ..., description="How to resolve the value: 'static' (fixed value), 'fieldRef' (from another form field), or 'expression' (JavaScript)")
    value: str | None = Field(
        default=None, description="Fixed value to pass (required when mode='static')")
    field_name: str | None = Field(
        default=None, description="Name of another form field to get value from (required when mode='fieldRef')")
    expression: str | None = Field(
        default=None, description="JavaScript expression like 'context.field.org_id' (required when mode='expression')")

    @model_validator(mode='after')
    def validate_mode_data(self):
        """Ensure exactly one field is set based on mode"""
        if self.mode == DataProviderInputMode.STATIC:
            if not self.value:
                raise ValueError("value required for static mode")
            if self.field_name or self.expression:
                raise ValueError("only value should be set for static mode")
        elif self.mode == DataProviderInputMode.FIELD_REF:
            if not self.field_name:
                raise ValueError("field_name required for fieldRef mode")
            if self.value or self.expression:
                raise ValueError("only field_name should be set for fieldRef mode")
        elif self.mode == DataProviderInputMode.EXPRESSION:
            if not self.expression:
                raise ValueError("expression required for expression mode")
            if self.value or self.field_name:
                raise ValueError("only expression should be set for expression mode")
        return self


class FormField(BaseModel):
    """Form field definition"""
    name: str = Field(..., description="Parameter name for workflow")
    label: str | None = Field(
        default=None, description="Display label (optional for markdown/html types)")
    type: FormFieldType
    required: bool = Field(default=False)
    validation: dict[str, Any] | None = None
    data_provider_id: Annotated[UUID | None, WorkflowRef()] = Field(
        default=None, description="Data provider ID for dynamic options")
    data_provider_inputs: dict[str, DataProviderInputConfig] | None = Field(
        default=None, description="Input configurations for data provider parameters")
    default_value: Any | None = None
    placeholder: str | None = None
    help_text: str | None = None

    # NEW MVP fields (T012)
    visibility_expression: str | None = Field(
        default=None, description="JavaScript expression for conditional visibility (e.g., context.field.show === true)")
    options: list[dict[str, str]] | None = Field(
        default=None, description="Options for radio/select fields")
    allowed_types: list[str] | None = Field(
        default=None, description="Allowed MIME types for file uploads")
    multiple: bool | None = Field(
        default=None, description="Allow multiple file uploads")
    max_size_mb: int | None = Field(
        default=None, description="Maximum file size in MB")
    content: str | None = Field(
        default=None, description="Static content for markdown/HTML components")
    allow_as_query_param: bool | None = Field(
        default=None, description="Whether this field's value can be populated from URL query parameters")
    auto_fill: dict[str, str] | None = Field(
        default=None,
        description="Map of sibling field names to metadata paths. When this field's data provider "
                    "returns results, auto-populate sibling fields from the first result's metadata. "
                    "Example: {\"employee_count\": \"recommended_employee_count\"}")

    @model_validator(mode='after')
    def validate_field_requirements(self):
        """Validate field-specific requirements"""
        # data_provider_inputs requires data_provider_id
        # If data_provider_id is NULL but data_provider_inputs exists, clear the inputs
        # This handles the edge case where the data provider was deleted (FK SET NULL)
        if self.data_provider_inputs and not self.data_provider_id:
            object.__setattr__(self, 'data_provider_inputs', None)

        # label is required for non-display fields (markdown/html use content instead)
        display_only_types = {FormFieldType.MARKDOWN, FormFieldType.HTML}
        if self.type not in display_only_types and not self.label:
            raise ValueError(f"label is required for {self.type.value} fields")

        # content is required for markdown/html fields
        if self.type in display_only_types and not self.content:
            raise ValueError(f"content is required for {self.type.value} fields")

        # multi_select default_value is a comma-separated list of option values;
        # trim whitespace around each entry and drop empty entries so that
        # "a, b , ,c" normalizes to "a,b,c"
        if self.type == FormFieldType.MULTI_SELECT and isinstance(self.default_value, str):
            parts = [p.strip() for p in self.default_value.split(",")]
            parts = [p for p in parts if p]
            object.__setattr__(self, 'default_value', ",".join(parts) if parts else None)

        return self


class FormSchema(BaseModel):
    """Form schema with field definitions"""
    fields: list[FormField] = Field(..., max_length=50,
                                    description="Max 50 fields per form")

    @field_validator('fields')
    @classmethod
    def validate_unique_names(cls, v):
        """Ensure field names are unique"""
        names = [field.name for field in v]
        if len(names) != len(set(names)):
            raise ValueError("Field names must be unique")
        return v

    @model_validator(mode='after')
    def validate_auto_fill_targets(self):
        """Ensure auto_fill target field names reference fields that exist in this form."""
        field_names = {field.name for field in self.fields}
        for field in self.fields:
            if not field.auto_fill:
                continue
            invalid = set(field.auto_fill.keys()) - field_names
            if invalid:
                raise ValueError(
                    f"Field '{field.name}' auto_fill references non-existent "
                    f"target field(s): {', '.join(sorted(invalid))}"
                )
        return self


class Form(BaseModel):
    """Form entity (response model)"""
    id: str
    org_id: str = Field(..., description="Organization ID or 'GLOBAL'")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    workflow_id: str | None = Field(default=None, description="Workflow ID (UUID) to execute when form is submitted")
    form_schema: FormSchema
    is_active: bool = Field(default=True)
    is_global: bool = Field(default=False)
    access_level: FormAccessLevel | None = Field(
        default=None, description="Access control level. Defaults to 'role_based' if not set.")
    created_by: str
    created_at: datetime
    updated_at: datetime

    # Optional launch params
    allowed_query_params: list[str] | None = Field(
        default=None, description="List of allowed query parameter names to inject into form context")
    default_launch_params: dict[str, Any] | None = Field(
        default=None, description="Default parameter values for workflow execution")


class CreateFormRequest(BaseModel):
    """Request model for creating a form"""
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    workflow_id: str = Field(..., description="Workflow ID (UUID) to execute when form is submitted")
    form_schema: FormSchema
    is_global: bool = Field(default=False)
    access_level: FormAccessLevel = Field(
        default=FormAccessLevel.ROLE_BASED, description="Access control level")

    # Optional launch params
    allowed_query_params: list[str] | None = Field(
        default=None, description="List of allowed query parameter names to inject into form context")
    default_launch_params: dict[str, Any] | None = Field(
        default=None, description="Default parameter values for workflow execution")


class UpdateFormRequest(BaseModel):
    """Request model for updating a form"""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    workflow_id: str | None = Field(default=None, description="Workflow ID (UUID) to execute when form is submitted")
    form_schema: FormSchema | None = None
    is_active: bool | None = None
    access_level: FormAccessLevel | None = None

    # Optional launch params
    allowed_query_params: list[str] | None = Field(
        default=None, description="List of allowed query parameter names to inject into form context")
    default_launch_params: dict[str, Any] | None = Field(
        default=None, description="Default parameter values for workflow execution")


class FormExecuteRequest(BaseModel):
    """Request model for executing a form"""
    form_data: dict[str, Any] = Field(default_factory=dict, description="Form field values")
    startup_data: dict[str, Any] | None = Field(default=None, description="Results from /startup call (launch workflow)")
    scheduled_at: datetime | None = Field(
        default=None,
        description=(
            "Run at this tz-aware timestamp (ISO-8601). Must be strictly in the "
            "future and within 1 year of now. Mutually exclusive with delay_seconds."
        ),
    )
    delay_seconds: int | None = Field(
        default=None,
        ge=1,
        le=31_536_000,
        description=(
            "Run this many seconds from now (≤ 1 year). "
            "Mutually exclusive with scheduled_at."
        ),
    )

    @model_validator(mode="after")
    def validate_scheduling(self) -> "FormExecuteRequest":
        if self.scheduled_at is not None and self.delay_seconds is not None:
            raise ValueError(
                "'scheduled_at' and 'delay_seconds' are mutually exclusive"
            )

        if self.scheduled_at is not None:
            if self.scheduled_at.tzinfo is None:
                raise ValueError("'scheduled_at' must be timezone-aware")
            now = datetime.now(timezone.utc)
            if self.scheduled_at <= now:
                raise ValueError("'scheduled_at' must be in the future")
            if self.scheduled_at > now + timedelta(days=365):
                raise ValueError("'scheduled_at' must be within 1 year")

        return self


class FormStartupResponse(BaseModel):
    """Response model for form startup/launch workflow execution"""
    result: dict[str, Any] | list[Any] | str | None = Field(default=None, description="Workflow execution result")


# CRUD Pattern Models for Form
class FormCreate(BaseModel):
    """Input for creating a form."""
    name: str
    description: str | None = None
    workflow_id: str | None = None
    launch_workflow_id: str | None = None
    default_launch_params: dict | None = None
    allowed_query_params: list[str] | None = None
    form_schema: dict | FormSchema
    access_level: FormAccessLevel | None = FormAccessLevel.ROLE_BASED
    organization_id: UUID | None = Field(
        default=None, description="Organization ID (null = global resource)"
    )
    role_ids: list[UUID] = Field(
        default_factory=list,
        description="Role IDs for role_based access (ignored if access_level is 'authenticated')",
    )

class FormUpdate(BaseModel):
    """Input for updating a form."""
    name: str | None = None
    description: str | None = None
    workflow_id: str | None = None
    launch_workflow_id: str | None = None
    default_launch_params: dict | None = None
    allowed_query_params: list[str] | None = None
    form_schema: dict | FormSchema | None = None
    is_active: bool | None = None
    access_level: FormAccessLevel | None = None
    organization_id: UUID | None = Field(
        default=None, description="Organization ID (null = global resource)"
    )
    clear_roles: bool = False
    role_ids: list[UUID] | None = Field(
        default=None,
        description="Role IDs for role_based access (replaces existing roles when provided)",
    )


class FormPublic(BaseModel):
    """Form output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    workflow_id: Annotated[str | None, WorkflowRef()] = None
    launch_workflow_id: Annotated[str | None, WorkflowRef()] = None
    default_launch_params: dict | None = None
    allowed_query_params: list[str] | None = None
    form_schema: dict | FormSchema | None = None
    access_level: FormAccessLevel | None = None
    organization_id: UUID | None = None
    role_ids: list[UUID] = Field(
        default_factory=list,
        description="Role IDs assigned to this form (for role_based access)",
    )
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    dependency_count: int = Field(default=0, description="Number of workflow dependencies this form uses")
    is_solution_managed: bool = Field(default=False, description="True if managed by a deployed Solution (read-only on platform)")
    solution_id: UUID | None = Field(default=None, description="UUID of the owning Solution install (null if not solution-managed)")

    @model_validator(mode="before")
    @classmethod
    def compute_form_schema(cls, data):
        """Compute form_schema from fields relationship if available."""
        if isinstance(data, dict):
            return data  # Already a dict, use as-is

        # It's an ORM object
        if hasattr(data, 'fields') and data.fields:
            # Build FormField objects from ORM fields relationship
            # This ensures @field_serializer decorators are used during model_dump()
            form_fields = []
            for field in sorted(data.fields, key=lambda f: f.position):
                form_field = FormField(
                    name=field.name,
                    type=field.type,
                    required=field.required,
                    label=field.label,
                    placeholder=field.placeholder,
                    help_text=field.help_text,
                    default_value=field.default_value,
                    options=field.options,
                    data_provider_id=field.data_provider_id,
                    data_provider_inputs=field.data_provider_inputs,
                    visibility_expression=field.visibility_expression,
                    validation=field.validation,
                    allowed_types=field.allowed_types,
                    multiple=field.multiple,
                    max_size_mb=field.max_size_mb,
                    content=field.content,
                    allow_as_query_param=field.allow_as_query_param,
                    auto_fill=field.auto_fill,
                )
                form_fields.append(form_field)

            # Create FormSchema with FormField objects
            form_schema = FormSchema(fields=form_fields)

            # Create a new dict with form_schema computed
            data_dict = {
                "id": data.id,
                "name": data.name,
                "description": data.description,
                "workflow_id": data.workflow_id,
                "launch_workflow_id": data.launch_workflow_id,
                "default_launch_params": data.default_launch_params,
                "allowed_query_params": data.allowed_query_params,
                "form_schema": form_schema,
                "access_level": data.access_level,
                "organization_id": data.organization_id,
                "is_active": data.is_active,
                "created_at": data.created_at,
                "updated_at": data.updated_at,
                "is_solution_managed": getattr(data, "solution_id", None) is not None,
                "solution_id": getattr(data, "solution_id", None),
            }
            # Forward an attached role_ids list when callers populate it
            # (via a separate FormRole query). Falls back to default_factory
            # when absent so reads that don't bother stay backwards-compatible.
            attached_role_ids = getattr(data, "role_ids", None)
            if attached_role_ids is not None:
                data_dict["role_ids"] = list(attached_role_ids)
            return data_dict

        # ORM object with no fields relationship loaded: map the handful of
        # attributes pydantic needs, deriving the solution-managed flag (the ORM
        # attr is solution_id; the DTO field is is_solution_managed).
        if not isinstance(data, dict):
            return {
                "id": data.id,
                "name": data.name,
                "description": data.description,
                "workflow_id": data.workflow_id,
                "launch_workflow_id": data.launch_workflow_id,
                "default_launch_params": data.default_launch_params,
                "allowed_query_params": data.allowed_query_params,
                "access_level": data.access_level,
                "organization_id": data.organization_id,
                "is_active": data.is_active,
                "created_at": data.created_at,
                "updated_at": data.updated_at,
                "is_solution_managed": getattr(data, "solution_id", None) is not None,
                "solution_id": getattr(data, "solution_id", None),
                **({"role_ids": list(data.role_ids)} if getattr(data, "role_ids", None) is not None else {}),
            }

        return data

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None
