"""
Forms Router

CRUD operations for workflow forms.
Support for org-specific and global forms.
Form execution for org users with access control.

Forms are virtual entities stored only in the database.
They are serialized to JSON on-the-fly for git sync operations.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.auth import Context, CurrentActiveUser, CurrentSuperuser
from src.core.database import DbSession
from src.core.log_safety import log_safe
from src.core.org_filter import resolve_org_filter
from src.models.enums import FormAccessLevel
from src.repositories.forms import FormRepository
from src.models import Execution as ExecutionORM
from src.models import (
    Form as FormORM,
    FormField as FormFieldORM,
    FormRole as FormRoleORM,
    UserRole as UserRoleORM,
)
from src.models import Role as RoleORM
from src.models import Workflow as WorkflowORM
from src.models import FormCreate, FormUpdate, FormPublic
from src.models.contracts.forms import FormField, FormSchema
from src.models import WorkflowExecutionResponse
from src.models import FileUploadRequest, FileUploadResponse, UploadedFileMetadata
from src.models import FormExecuteRequest, FormStartupResponse
from src.models.enums import ExecutionStatus

# Import cache invalidation
try:
    from src.core.cache import invalidate_form

    CACHE_INVALIDATION_AVAILABLE = True
except ImportError:
    CACHE_INVALIDATION_AVAILABLE = False
    invalidate_form = None  # type: ignore

# Import workflow role sync
from src.services.workflow_role_service import sync_form_roles_to_workflows

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forms", tags=["Forms"])


def _form_schema_to_fields(form_schema: dict, form_id: UUID) -> list[FormFieldORM]:
    """
    Convert FormSchema dict to list of FormField ORM objects.

    Args:
        form_schema: FormSchema dict with 'fields' key
        form_id: Parent form UUID

    Returns:
        List of FormField ORM objects
    """
    from src.models import FormSchema

    # Validate structure
    schema = FormSchema.model_validate(form_schema)

    fields = []
    for position, field in enumerate(schema.fields):
        # Convert data_provider_inputs Pydantic models to plain dicts for JSON storage
        dp_inputs = None
        if field.data_provider_inputs:
            dp_inputs = {
                key: config.model_dump(mode="json")
                for key, config in field.data_provider_inputs.items()
            }

        field_orm = FormFieldORM(
            form_id=form_id,
            name=field.name,
            label=field.label,
            type=field.type.value,
            required=field.required,
            position=position,
            placeholder=field.placeholder,
            help_text=field.help_text,
            default_value=field.default_value,
            options=field.options,
            data_provider_id=field.data_provider_id,
            data_provider_inputs=dp_inputs,
            visibility_expression=field.visibility_expression,
            validation=field.validation,
            allowed_types=field.allowed_types,
            multiple=field.multiple,
            max_size_mb=field.max_size_mb,
            content=field.content,
            allow_as_query_param=field.allow_as_query_param,
            auto_fill=field.auto_fill,
        )
        fields.append(field_orm)

    return fields


async def _validate_form_references(
    db: AsyncSession,
    workflow_id: str | None,
    launch_workflow_id: str | None,
    form_schema: dict | None,
) -> None:
    """
    Validate that all referenced workflows and data providers exist and are active.

    Args:
        db: Database session
        workflow_id: Optional workflow ID to validate (must be type='workflow')
        launch_workflow_id: Optional launch workflow ID to validate (must be type='workflow')
        form_schema: Optional form schema with fields that may reference data providers

    Raises:
        HTTPException: 422 if any reference is invalid
    """
    errors: list[str] = []

    def _is_valid_uuid(val: str) -> bool:
        try:
            UUID(str(val))
            return True
        except (ValueError, AttributeError):
            return False

    # Validate workflow_id
    if workflow_id:
        if not _is_valid_uuid(workflow_id):
            errors.append(f"workflow_id '{workflow_id}' is not a valid UUID")
        else:
            result = await db.execute(
                select(WorkflowORM).where(
                    WorkflowORM.id == workflow_id,
                    WorkflowORM.is_active == True,  # noqa: E712
                )
            )
            workflow = result.scalar_one_or_none()
            if workflow is None:
                errors.append(
                    f"workflow_id '{workflow_id}' does not reference an active workflow"
                )
            elif workflow.type not in ("workflow", "tool"):
                errors.append(
                    f"workflow_id '{workflow_id}' references a {workflow.type}, not a workflow or tool"
                )

    # Validate launch_workflow_id
    if launch_workflow_id:
        if not _is_valid_uuid(launch_workflow_id):
            errors.append(
                f"launch_workflow_id '{launch_workflow_id}' is not a valid UUID"
            )
        else:
            result = await db.execute(
                select(WorkflowORM).where(
                    WorkflowORM.id == launch_workflow_id,
                    WorkflowORM.is_active == True,  # noqa: E712
                )
            )
            launch_workflow = result.scalar_one_or_none()
            if launch_workflow is None:
                errors.append(
                    f"launch_workflow_id '{launch_workflow_id}' does not reference an active workflow"
                )
            elif launch_workflow.type not in ("workflow", "tool"):
                errors.append(
                    f"launch_workflow_id '{launch_workflow_id}' references a {launch_workflow.type}, not a workflow or tool"
                )

    # Validate data_provider_id references in form fields
    if form_schema and "fields" in form_schema:
        for field in form_schema["fields"]:
            dp_id = field.get("data_provider_id")
            if dp_id:
                if not _is_valid_uuid(dp_id):
                    errors.append(
                        f"Field '{field.get('name', 'unknown')}' has invalid data_provider_id "
                        f"'{dp_id}' - not a valid UUID"
                    )
                else:
                    result = await db.execute(
                        select(WorkflowORM).where(
                            WorkflowORM.id == dp_id,
                            WorkflowORM.is_active == True,  # noqa: E712
                        )
                    )
                    data_provider = result.scalar_one_or_none()
                    if data_provider is None:
                        errors.append(
                            f"Field '{field.get('name', 'unknown')}' has invalid data_provider_id "
                            f"'{dp_id}' - no active data provider found"
                        )
                    elif data_provider.type != "data_provider":
                        errors.append(
                            f"Field '{field.get('name', 'unknown')}' has data_provider_id "
                            f"'{dp_id}' that references a {data_provider.type}, not a data_provider"
                        )

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": errors, "message": "Invalid form references"},
        )


@router.get(
    "",
    response_model=list[FormPublic],
    summary="List forms",
    description="List all forms visible to the user based on their permissions",
)
async def list_forms(
    ctx: Context,
    db: DbSession,
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org + global.",
    ),
) -> list[FormPublic]:
    """List all forms visible to the user.

    - Platform admins see all forms (or filter by scope if provided)
    - Org users see: their org's forms + global forms (org_id IS NULL)
    - Access is further filtered by access_level (authenticated, role_based)

    Uses the FormRepository which handles:
    - Cascade scoping (org + global for org users)
    - Role-based access control (for non-superusers)
    """
    # Resolve organization filter based on user permissions
    try:
        filter_type, filter_org = resolve_org_filter(ctx.user, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    # Create repository with appropriate scope and user context
    # For superusers: is_superuser=True bypasses role checks
    # For org users: user_id enables role-based access filtering
    repo = FormRepository(
        session=db,
        org_id=filter_org,
        user_id=ctx.user.user_id if not ctx.user.is_superuser else None,
        is_superuser=ctx.user.is_superuser,
    )

    # Platform admins bypass access level filtering - they see all forms within org scope
    if ctx.user.is_superuser:
        # Use list_all_in_scope which skips role checks (appropriate for superusers)
        # Pass filter_type to control org scoping behavior
        forms = await repo.list_all_in_scope(filter_type=filter_type, active_only=False)
        result = [FormPublic.model_validate(f) for f in forms]
    else:
        # For org users: repository handles cascade scoping + role-based access
        # list_forms() applies both cascade scope and role checks automatically
        forms = await repo.list_forms(active_only=True)
        result = [FormPublic.model_validate(f) for f in forms]

    # Compute dependency counts for each form
    for form_public in result:
        count = 0
        if form_public.workflow_id and len(form_public.workflow_id) == 36:
            count += 1
        if form_public.launch_workflow_id and len(form_public.launch_workflow_id) == 36:
            count += 1
        if form_public.form_schema:
            schema = form_public.form_schema
            fields = (
                schema.fields
                if isinstance(schema, FormSchema)
                else (schema or {}).get("fields", [])
            )
            for field in fields:
                dp_id = (
                    field.data_provider_id
                    if isinstance(field, FormField)
                    else (field or {}).get("data_provider_id")
                )
                if dp_id:
                    count += 1
        form_public.dependency_count = count

    return result


async def _replace_form_roles(
    db: AsyncSession,
    form_id: UUID,
    role_ids: list[UUID],
    assigned_by: str,
) -> None:
    """Bulk-replace role assignments on a form.

    Validates every role exists, then deletes existing FormRole rows for the
    form and inserts the new set. Empty list clears all assignments.
    """
    if role_ids:
        existing = await db.execute(select(RoleORM.id).where(RoleORM.id.in_(role_ids)))
        found = set(existing.scalars().all())
        missing = [str(rid) for rid in role_ids if rid not in found]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role(s) not found: {', '.join(missing)}",
            )

    await db.execute(delete(FormRoleORM).where(FormRoleORM.form_id == form_id))
    now = datetime.now(timezone.utc)
    for role_id in role_ids:
        db.add(
            FormRoleORM(
                form_id=form_id,
                role_id=role_id,
                assigned_by=assigned_by,
                assigned_at=now,
            )
        )
    await db.flush()


async def _load_form_role_ids(db: AsyncSession, form_id: UUID) -> list[UUID]:
    """Return the role IDs currently assigned to a form."""
    result = await db.execute(
        select(FormRoleORM.role_id).where(FormRoleORM.form_id == form_id)
    )
    return list(result.scalars().all())


@router.post(
    "",
    response_model=FormPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new form",
    description="Create a new form (Platform admin only)",
)
async def create_form(
    request: FormCreate,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> FormPublic:
    """
    Create a new form.

    Forms are stored in the database only. They are serialized
    to JSON on-the-fly for git sync operations.
    """
    # Prepare form_schema for validation
    form_schema_data: dict = request.form_schema  # type: ignore[assignment]
    if hasattr(form_schema_data, "model_dump"):
        form_schema_data = form_schema_data.model_dump()  # type: ignore[union-attr]

    # Validate all references before creating the form
    await _validate_form_references(
        db=db,
        workflow_id=request.workflow_id,
        launch_workflow_id=request.launch_workflow_id,
        form_schema=form_schema_data,
    )

    now = datetime.now(timezone.utc)

    # Create form record
    form = FormORM(
        name=request.name,
        description=request.description,
        workflow_id=request.workflow_id,
        launch_workflow_id=request.launch_workflow_id,
        default_launch_params=request.default_launch_params,
        allowed_query_params=request.allowed_query_params,
        access_level=request.access_level,
        organization_id=request.organization_id,
        is_active=True,
        created_by=ctx.user.email,
        created_at=now,
        updated_at=now,
    )

    db.add(form)
    await db.flush()  # Get the form ID

    # Convert form_schema to FormField records (form_schema_data already prepared above)
    field_records = _form_schema_to_fields(form_schema_data, form.id)
    for field in field_records:
        db.add(field)

    await db.flush()

    # Apply role assignments before reloading so the response reflects them
    if request.role_ids:
        await _replace_form_roles(db, form.id, request.role_ids, ctx.user.email)

    # Reload form with fields eager-loaded
    result = await db.execute(
        select(FormORM)
        .options(selectinload(FormORM.fields))
        .where(FormORM.id == form.id)
    )
    form = result.scalar_one()

    # Sync form roles to referenced workflows (additive)
    await sync_form_roles_to_workflows(
        db, form, form.fields, assigned_by=ctx.user.email
    )

    logger.info(f"Created form {form.id}: {log_safe(form.name)}")

    # Invalidate cache after successful create
    if CACHE_INVALIDATION_AVAILABLE and invalidate_form:
        org_id = str(form.organization_id) if form.organization_id else None
        await invalidate_form(org_id, str(form.id))

    form.role_ids = await _load_form_role_ids(db, form.id)  # type: ignore[attr-defined]
    return FormPublic.model_validate(form)


@router.get(
    "/{form_id}",
    response_model=FormPublic,
    summary="Get form by ID",
    description="Get a specific form by ID. User must have access to the form.",
)
async def get_form(
    form_id: UUID,
    ctx: Context,
    db: DbSession,
) -> FormPublic:
    """Get a specific form by ID."""
    # Use FormRepository for consistent query logic
    # Note: We don't filter by org here - access control is done after fetch
    repo = FormRepository(
        session=db,
        org_id=None,  # No org filtering for initial fetch
        user_id=ctx.user.user_id if not ctx.user.is_superuser else None,
        is_superuser=ctx.user.is_superuser,
    )
    form = await repo.get_form(form_id)

    if not form:
        logger.warning(f"Form {log_safe(form_id)} not found in database")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found",
        )

    async def _to_public(orm_form: FormORM) -> FormPublic:
        orm_form.role_ids = await _load_form_role_ids(db, orm_form.id)  # type: ignore[attr-defined]
        return FormPublic.model_validate(orm_form)

    # Check access - admins and embed users can see all forms
    if ctx.user.is_superuser or ctx.user.embed:
        return await _to_public(form)

    # Non-admins can only see active forms
    if not form.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found",
        )

    # Check org access - user can access their org's forms OR global forms (org_id is NULL)
    if form.organization_id is not None and form.organization_id != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to form",
        )

    # Check access level
    access_level = form.access_level or "role_based"
    if access_level == "authenticated":
        return await _to_public(form)

    # Role-based: check if user has a role assigned to this form
    role_query = select(UserRoleORM.role_id).where(
        UserRoleORM.user_id == ctx.user.user_id
    )
    role_result = await db.execute(role_query)
    user_role_ids = list(role_result.scalars().all())

    if user_role_ids:
        form_role_query = select(FormRoleORM).where(
            FormRoleORM.form_id == form_id,
            FormRoleORM.role_id.in_(user_role_ids),
        )
        form_role_result = await db.execute(form_role_query)
        if form_role_result.scalar_one_or_none() is not None:
            return await _to_public(form)

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied to form",
    )


@router.patch(
    "/{form_id}",
    response_model=FormPublic,
    summary="Update a form",
    description="Update an existing form (Platform admin only)",
)
async def update_form(
    form_id: UUID,
    request: FormUpdate,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> FormPublic:
    """
    Update a form.

    Forms are stored in the database only. They are serialized
    to JSON on-the-fly for git sync operations.
    """
    result = await db.execute(
        select(FormORM)
        .options(selectinload(FormORM.fields))
        .where(FormORM.id == form_id)
    )
    form = result.scalar_one_or_none()

    if not form:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found",
        )

    # Validate references being updated
    form_schema_for_validation = None
    if request.form_schema is not None:
        form_schema_for_validation = request.form_schema
        if hasattr(form_schema_for_validation, "model_dump"):
            form_schema_for_validation = form_schema_for_validation.model_dump()

    await _validate_form_references(
        db=db,
        workflow_id=request.workflow_id,
        launch_workflow_id=request.launch_workflow_id,
        form_schema=form_schema_for_validation,
    )

    if request.name is not None:
        form.name = request.name
    if request.description is not None:
        form.description = request.description
    if request.workflow_id is not None:
        form.workflow_id = request.workflow_id
    if request.launch_workflow_id is not None:
        form.launch_workflow_id = request.launch_workflow_id
    if request.default_launch_params is not None:
        form.default_launch_params = request.default_launch_params
    if request.allowed_query_params is not None:
        form.allowed_query_params = request.allowed_query_params
    if request.form_schema is not None:
        # Delete all existing fields using bulk delete
        await db.execute(delete(FormFieldORM).where(FormFieldORM.form_id == form_id))
        # Expire the relationship to reflect the deletion
        db.expire(form, ["fields"])

        # Convert new form_schema to FormField records
        form_schema_data: dict = request.form_schema  # type: ignore[assignment]
        if hasattr(form_schema_data, "model_dump"):
            form_schema_data = form_schema_data.model_dump()  # type: ignore[union-attr]

        field_records = _form_schema_to_fields(form_schema_data, form_id)
        for field in field_records:
            db.add(field)

    if request.is_active is not None:
        form.is_active = request.is_active
    if request.access_level is not None:
        form.access_level = request.access_level
    # Use model_fields_set to distinguish "not provided" from "explicitly set to null"
    if "organization_id" in request.model_fields_set:
        form.organization_id = request.organization_id

    # Role assignment edits. ``role_ids`` (when explicitly provided) bulk-replaces
    # the assignment set; ``clear_roles`` is the legacy single-purpose flag and
    # wipes assignments when ``role_ids`` was not supplied. If both are provided,
    # ``role_ids`` wins because it carries the more specific intent.
    if request.role_ids is not None:
        await _replace_form_roles(db, form_id, request.role_ids, ctx.user.email)
        logger.info(
            f"Replaced role assignments for form '{log_safe(form.name)}' "
            f"({len(request.role_ids)} role(s))"
        )
    elif request.clear_roles:
        await db.execute(delete(FormRoleORM).where(FormRoleORM.form_id == form_id))
        # Also set to role_based access level (effectively no access)
        form.access_level = FormAccessLevel.ROLE_BASED
        logger.info(f"Cleared all role assignments for form '{log_safe(form.name)}'")

    form.updated_at = datetime.now(timezone.utc)

    await db.flush()

    # Reload form with fields eager-loaded
    result = await db.execute(
        select(FormORM)
        .options(selectinload(FormORM.fields))
        .where(FormORM.id == form_id)
    )
    form = result.scalar_one()

    # Sync form roles to referenced workflows (additive)
    await sync_form_roles_to_workflows(
        db, form, form.fields, assigned_by=ctx.user.email
    )

    logger.info(f"Updated form {log_safe(form_id)}")

    # Invalidate cache after successful update
    if CACHE_INVALIDATION_AVAILABLE and invalidate_form:
        org_id = str(form.organization_id) if form.organization_id else None
        await invalidate_form(org_id, str(form_id))

    form.role_ids = await _load_form_role_ids(db, form_id)  # type: ignore[attr-defined]
    return FormPublic.model_validate(form)


# Keep PUT for backwards compatibility
@router.put(
    "/{form_id}",
    response_model=FormPublic,
    summary="Update a form",
    description="Update an existing form (Platform admin only)",
    include_in_schema=False,  # Hide from OpenAPI, use PATCH instead
)
async def update_form_put(
    form_id: UUID,
    request: FormUpdate,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> FormPublic:
    """Update a form (PUT - for backwards compatibility)."""
    return await update_form(form_id, request, ctx, user, db)


@router.delete(
    "/{form_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a form",
    description="Delete a form. Use ?purge=true to permanently remove it from the database (Platform admin only)",
)
async def delete_form(
    form_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    purge: bool = Query(
        False,
        description="Permanently remove the form from the database instead of soft-deleting",
    ),
) -> None:
    """
    Delete a form.

    By default, sets is_active=False (soft delete).
    With purge=true, permanently removes the form and its related records from the database.
    """
    result = await db.execute(select(FormORM).where(FormORM.id == form_id))
    form = result.scalar_one_or_none()

    if not form:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found",
        )

    if purge:
        if form.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot purge an active form. Deactivate it first.",
            )
        # Unlink executions that reference this form (nullable FK)
        await db.execute(
            update(ExecutionORM)
            .where(ExecutionORM.form_id == form_id)
            .values(form_id=None)
        )
        # Delete form roles (no DB-level ondelete, must delete explicitly)
        await db.execute(delete(FormRoleORM).where(FormRoleORM.form_id == form_id))
        # Delete the form (form_fields and embed_secrets cascade via DB-level ondelete=CASCADE)
        await db.execute(delete(FormORM).where(FormORM.id == form_id))
        logger.info(f"Purged form {log_safe(form_id)}")

    if not purge:
        form.is_active = False
        form.updated_at = datetime.now(timezone.utc)
        await db.flush()
        logger.info(f"Soft deleted form {log_safe(form_id)}")

    # Invalidate cache
    if CACHE_INVALIDATION_AVAILABLE and invalidate_form:
        org_id = str(form.organization_id) if form.organization_id else None
        await invalidate_form(org_id, str(form_id))


# =============================================================================
# Form Execution
# =============================================================================


async def _check_form_access(
    db: DbSession,
    form: FormORM,
    user_id: UUID,
    user_org_id: UUID | None,
    is_superuser: bool,
) -> bool:
    """
    Check if user has access to execute a form.

    Delegates to ``FormRepository.get(id=...)``, which is the single
    org+role+access_level gate shared with the UI listing path and every
    other org-scoped entity (workflows, agents, tables, etc.). Returning
    None from the repo means "form doesn't exist OR caller doesn't have
    access" — for an existing form (the caller already loaded it via raw
    select), None definitively means "access denied".
    """
    from src.repositories.forms import FormRepository

    repo = FormRepository(
        db,
        org_id=user_org_id,
        user_id=user_id,
        is_superuser=is_superuser,
    )
    accessible = await repo.get(id=form.id)
    return accessible is not None


@router.post(
    "/{form_id}/execute",
    response_model=WorkflowExecutionResponse,
    summary="Execute a form",
    description="Execute the workflow linked to a form. Requires appropriate access based on form's access_level.",
)
async def execute_form(
    form_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
    request: FormExecuteRequest = Body(default=None),
) -> WorkflowExecutionResponse:
    """
    Execute the workflow linked to a form.

    This endpoint allows org users to execute workflows through forms they have access to.
    Access control is based on the form's access_level:
    - 'authenticated': Any logged-in user can execute
    - 'role_based': User must be assigned to a role that has this form

    The request body can include:
    - form_data: Form field values to pass to the workflow
    - startup_data: Results from /startup call (launch workflow) available via context.startup
    """
    from src.sdk.context import ExecutionContext as SharedContext, Organization
    from src.services.execution.service import (
        run_workflow,
        WorkflowNotFoundError,
        WorkflowLoadError,
    )

    # Default request if None (backward compatibility with empty body)
    if request is None:
        request = FormExecuteRequest()

    # Get the form
    result = await db.execute(select(FormORM).where(FormORM.id == form_id))
    form = result.scalar_one_or_none()

    if not form or not form.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found",
        )

    # Check access — embed users are pre-authorized via HMAC
    if not ctx.user.embed:
        has_access = await _check_form_access(
            db, form, ctx.user.user_id, ctx.org_id, ctx.user.is_superuser
        )
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this form",
            )

    # Form must have a workflow_id
    if not form.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Form has no workflow configured",
        )

    # Merge: defaults < verified HMAC params < user form input
    verified_params = ctx.user.verified_params or {}
    merged_params = {
        **(form.default_launch_params or {}),
        **verified_params,
        **request.form_data,
    }

    # Scheduled execution: normalize delay_seconds -> scheduled_at and insert a
    # SCHEDULED row directly. The deferred_execution_promoter job picks it up
    # when it matures — we never call run_workflow here.
    scheduled_at: datetime | None = request.scheduled_at
    if request.delay_seconds is not None:
        scheduled_at = datetime.now(timezone.utc) + timedelta(
            seconds=request.delay_seconds
        )

    if scheduled_at is not None:
        from src.routers.workflows import _insert_scheduled_execution

        workflow_result = await db.execute(
            select(WorkflowORM).where(WorkflowORM.id == UUID(form.workflow_id))
        )
        workflow = workflow_result.scalar_one_or_none()
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow not found: {form.workflow_id}",
            )

        exec_id = await _insert_scheduled_execution(
            db=db,
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            parameters=merged_params,
            scheduled_at=scheduled_at,
            organization_id=ctx.org_id,
            executed_by=ctx.user.user_id,
            executed_by_name=ctx.user.name or ctx.user.email or "Unknown",
            form_id=form.id,
            api_key_id=None,
            is_platform_admin=ctx.user.is_superuser,
        )
        logger.info(
            f"Form {log_safe(form_id)} scheduled by user {ctx.user.email}, "
            f"execution_id={exec_id}, scheduled_at={scheduled_at.isoformat()}"
        )
        return WorkflowExecutionResponse(
            execution_id=str(exec_id),
            workflow_id=str(workflow.id),
            workflow_name=workflow.name,
            status=ExecutionStatus.SCHEDULED,
            scheduled_at=scheduled_at,
        )

    # Create organization object if org_id is set
    org = None
    if ctx.org_id:
        org = Organization(id=str(ctx.org_id), name="", is_active=True)

    # Create shared context for execution
    # startup_data from the request is passed to context.startup
    shared_ctx = SharedContext(
        user_id=str(ctx.user.user_id),
        name=ctx.user.name,
        email=ctx.user.email,
        scope=str(ctx.org_id) if ctx.org_id else "GLOBAL",
        organization=org,
        is_platform_admin=ctx.user.is_superuser,
        is_function_key=False,
        execution_id=str(uuid4()),
        startup=request.startup_data,
    )

    try:
        # Execute workflow by ID
        response = await run_workflow(
            context=shared_ctx,
            workflow_id=form.workflow_id,
            input_data=merged_params,
            form_id=str(form.id),
        )

        logger.info(
            f"Form {log_safe(form_id)} executed by user {ctx.user.email}, execution_id={response.execution_id}"
        )

        # Register execution in Redis for embed session scoping
        if ctx.user.embed and ctx.user.jti:
            from src.core.cache.keys import embed_execution_key, TTL_EMBED_EXECUTION
            from src.core.cache.redis_client import get_redis

            async with get_redis() as r:
                await r.setex(
                    embed_execution_key(ctx.user.jti, response.execution_id),
                    TTL_EMBED_EXECUTION,
                    "1",
                )

        return response

    except WorkflowNotFoundError as e:
        logger.error(f"Workflow not found for form {log_safe(form_id)}: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {form.workflow_id}",
        )
    except WorkflowLoadError as e:
        logger.error(f"Workflow load error for form {log_safe(form_id)}: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load workflow: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error executing form {log_safe(form_id)}: {log_safe(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute form",
        )


# =============================================================================
# Launch Workflow (Startup)
# =============================================================================


@router.post(
    "/{form_id}/startup",
    response_model=FormStartupResponse,
    summary="Execute launch workflow",
    description="Execute the launch workflow to populate form context before main execution.",
)
async def execute_startup_workflow(
    form_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
    input_data: dict = Body(default={}),
) -> FormStartupResponse:
    """
    Execute the launch workflow to populate form context.

    The launch workflow runs BEFORE the form is displayed to the user.
    Its results are returned to the client, which stores them and passes
    them back during /execute. The main workflow can then access these
    results via context.startup.

    Use cases:
    - Pre-fetch dynamic options based on user's org
    - Load user-specific defaults
    - Validate form access based on external systems

    Returns:
        FormStartupResponse with the launch workflow's result
    """
    from src.sdk.context import ExecutionContext as SharedContext, Organization
    from src.services.execution.service import (
        run_workflow,
        WorkflowNotFoundError,
        WorkflowLoadError,
    )

    # Get the form
    result = await db.execute(select(FormORM).where(FormORM.id == form_id))
    form = result.scalar_one_or_none()

    if not form or not form.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found",
        )

    # Check access — embed users are pre-authorized via HMAC
    if not ctx.user.embed:
        has_access = await _check_form_access(
            db, form, ctx.user.user_id, ctx.org_id, ctx.user.is_superuser
        )
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this form",
            )

    # If no launch workflow, return empty result
    if not form.launch_workflow_id:
        return FormStartupResponse(result=None)

    # Merge: defaults < verified HMAC params < user input
    verified_params = ctx.user.verified_params or {}
    merged_params = {
        **(form.default_launch_params or {}),
        **verified_params,
        **input_data,
    }

    # Create organization object if org_id is set
    org = None
    if ctx.org_id:
        org = Organization(id=str(ctx.org_id), name="", is_active=True)

    # Create shared context for execution
    shared_ctx = SharedContext(
        user_id=str(ctx.user.user_id),
        name=ctx.user.name,
        email=ctx.user.email,
        scope=str(ctx.org_id) if ctx.org_id else "GLOBAL",
        organization=org,
        is_platform_admin=ctx.user.is_superuser,
        is_function_key=False,
        execution_id=str(uuid4()),
    )

    try:
        # Execute launch workflow by ID
        response = await run_workflow(
            context=shared_ctx,
            workflow_id=form.launch_workflow_id,
            input_data=merged_params,
            form_id=str(form.id),
        )

        logger.info(
            f"Launch workflow executed for form {log_safe(form_id)} by user {ctx.user.email}"
        )

        return FormStartupResponse(result=response.result)

    except WorkflowNotFoundError as e:
        logger.error(
            f"Launch workflow not found for form {log_safe(form_id)}: {log_safe(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Launch workflow not found: {form.launch_workflow_id}",
        )
    except WorkflowLoadError as e:
        logger.error(
            f"Launch workflow load error for form {log_safe(form_id)}: {log_safe(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load launch workflow: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error executing launch workflow for form {log_safe(form_id)}: {log_safe(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute launch workflow",
        )


# =============================================================================
# File Upload
# =============================================================================


def _sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for safe storage.

    Removes path separators, null bytes, and other potentially dangerous characters.
    Preserves the file extension.
    """
    # Remove path separators and null bytes
    sanitized = re.sub(r"[/\\:\x00]", "", filename)
    # Remove leading/trailing whitespace and dots (to prevent hidden files)
    sanitized = sanitized.strip(". ")
    # If nothing left, use a default name
    if not sanitized:
        sanitized = "unnamed_file"
    return sanitized


def _check_mime_type_allowed(content_type: str, allowed_types: list[str]) -> bool:
    """
    Check if a MIME type matches the allowed types.

    Supports:
    - Exact match: "application/pdf"
    - Wildcard: "image/*"
    - Extension: ".pdf" (matched against content_type)
    """
    for allowed in allowed_types:
        if allowed.endswith("/*"):
            # Wildcard match (e.g., "image/*")
            prefix = allowed[:-1]  # "image/"
            if content_type.startswith(prefix):
                return True
        elif allowed.startswith("."):
            # Extension-based - map common extensions to MIME types
            ext_to_mime = {
                ".pdf": "application/pdf",
                ".doc": "application/msword",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xls": "application/vnd.ms-excel",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".csv": "text/csv",
                ".txt": "text/plain",
                ".json": "application/json",
                ".xml": "application/xml",
                ".zip": "application/zip",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".svg": "image/svg+xml",
                ".webp": "image/webp",
            }
            if allowed.lower() in ext_to_mime:
                if content_type == ext_to_mime[allowed.lower()]:
                    return True
        else:
            # Exact MIME type match
            if content_type == allowed:
                return True
    return False


@router.post(
    "/{form_id}/upload",
    response_model=FileUploadResponse,
    summary="Generate presigned URL for file upload",
    description="Generate a presigned S3 URL for direct file upload. The file will be stored in the uploads folder.",
)
async def generate_upload_url(
    form_id: UUID,
    request: FileUploadRequest,
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
) -> FileUploadResponse:
    """
    Generate a presigned S3 URL for direct file upload.

    Path: uploads/{form_id}/{uuid}/{sanitized_filename}
    - Organized by form for easy association
    - UUID prevents collisions within form
    - File exists before execution, workflow receives path in file_field metadata

    Returns:
        FileUploadResponse with presigned URL and file metadata
    """
    from src.services.file_storage import FileStorageService

    # Verify form exists and user has access
    result = await db.execute(
        select(FormORM)
        .options(selectinload(FormORM.fields))
        .where(FormORM.id == form_id)
    )
    form = result.scalar_one_or_none()

    if not form or not form.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found",
        )

    # Check access — embed users are pre-authorized via HMAC
    if not ctx.user.embed:
        has_access = await _check_form_access(
            db, form, ctx.user.user_id, ctx.org_id, ctx.user.is_superuser
        )
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this form",
            )

    # Server-side validation of file constraints if field_name provided
    if request.field_name:
        field = next((f for f in form.fields if f.name == request.field_name), None)
        if field:
            # Validate file type
            if field.allowed_types:
                if not _check_mime_type_allowed(
                    request.content_type, field.allowed_types
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"File type '{request.content_type}' not allowed. Allowed: {', '.join(field.allowed_types)}",
                    )

            # Validate file size
            if field.max_size_mb:
                max_bytes = field.max_size_mb * 1024 * 1024
                if request.file_size > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"File size {request.file_size} bytes exceeds maximum {field.max_size_mb}MB",
                    )

    # Generate the upload's relative path. The full S3 key is built by the
    # unified files resolver: `uploads/{scope}/{relative_path}`. Scope is the
    # caller's effective org (matches what the workflow's SDK will resolve to
    # when it reads the file with `location="uploads"` and no explicit scope).
    from shared.file_paths import resolve_s3_key

    file_uuid = str(uuid4())
    sanitized_name = _sanitize_filename(request.file_name)
    relative_path = f"{form_id}/{file_uuid}/{sanitized_name}"
    upload_scope = str(ctx.org_id) if ctx.org_id else "global"
    s3_key = resolve_s3_key("uploads", upload_scope, relative_path)

    # Generate presigned URL against the full S3 key, but surface the
    # location-relative path to callers so workflows can use
    # `files.read(blob_uri, location="uploads")` without prefix-stripping.
    storage = FileStorageService(db)
    try:
        upload_url = await storage.generate_presigned_upload_url(
            path=s3_key,
            content_type=request.content_type,
            expires_in=600,  # 10 minutes
        )
    except Exception as e:
        logger.error(
            f"Failed to generate presigned URL for form {log_safe(form_id)}: {log_safe(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate upload URL",
        )

    # Calculate expiration time
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat() + "Z"

    return FileUploadResponse(
        upload_url=upload_url,
        upload_headers=storage.presigned_upload_headers(request.content_type),
        blob_uri=relative_path,
        expires_at=expires_at,
        file_metadata=UploadedFileMetadata(
            name=request.file_name,
            container="uploads",
            path=relative_path,
            content_type=request.content_type,
            size=request.file_size,
        ),
    )
