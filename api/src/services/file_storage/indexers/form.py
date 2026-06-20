"""
Form indexer for parsing and indexing .form.yaml files.

Handles form metadata extraction, ID alignment, and field synchronization.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

import yaml
from pydantic import ValidationError

from src.models import Form, FormField as FormFieldORM, Workflow
from src.models.contracts.forms import FormField

logger = logging.getLogger(__name__)


class FormIndexer:
    """
    Indexes .form.yaml files and synchronizes with the database.

    Handles ID alignment, workflow name resolution, and field synchronization.
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize the form indexer.

        Args:
            db: Database session for querying and updating form records
        """
        self.db = db

    async def resolve_workflow_name_to_id(self, workflow_name: str) -> str | None:
        """
        Resolve a workflow name to its UUID.

        Used for legacy form files that use linked_workflow (name) instead of workflow_id (UUID).

        Args:
            workflow_name: The workflow name to resolve

        Returns:
            The workflow UUID as a string, or None if not found
        """
        result = await self.db.execute(
            select(Workflow.id).where(
                Workflow.name == workflow_name,
                Workflow.is_active == True,  # noqa: E712
            )
        )
        row = result.scalar_one_or_none()
        return str(row) if row else None

    async def index_form(
        self,
        path: str,
        content: bytes,
    ) -> bool:
        """
        Parse and index form from .form.yaml file.

        If the YAML contains an 'id' field, uses that ID (for dual-write from API).
        Otherwise generates a new ID and writes it back to the file.

        Updates form definition (name, description, workflow_id, form_schema, etc.)
        but preserves environment-specific fields (organization_id, access_level).

        Uses ON CONFLICT on primary key (id) to update existing forms.

        Args:
            path: File path
            content: File content bytes

        Returns:
            True if content was modified (ID alignment), False otherwise
        """
        content_modified = False

        try:
            form_data = yaml.safe_load(content.decode("utf-8"))
        except yaml.YAMLError:
            logger.warning(f"Invalid YAML in form file: {path}")
            return False

        name = form_data.get("name")
        if not name:
            logger.warning(f"Form file missing name: {path}")
            return False

        # Use ID from JSON if present (for API-created forms), otherwise generate and inject
        form_id_str = form_data.get("id")
        if form_id_str:
            try:
                form_id = UUID(form_id_str)
            except ValueError:
                logger.warning(f"Invalid form ID in {path}: {form_id_str}")
                form_id = uuid4()
                form_data["id"] = str(form_id)
                content_modified = True
        else:
            # Generate new ID and inject it into the file
            form_id = uuid4()
            form_data["id"] = str(form_id)
            content_modified = True
            logger.info(f"Injecting ID {form_id} into form file: {path}")

        # Forms are now "fully virtual" - their path is computed from their ID
        # (e.g., forms/{uuid}.form.yaml), so we don't need a separate file_path column.
        # We just use the ID from the YAML content directly.

        now = datetime.now(timezone.utc)

        # Get workflow_id - prefer explicit workflow_id, fall back to 'workflow' (UUID alias),
        # then linked_workflow (name lookup)
        workflow_id = form_data.get("workflow_id") or form_data.get("workflow")
        if not workflow_id:
            linked_workflow = form_data.get("linked_workflow")
            if linked_workflow:
                # Legacy format - resolve workflow name to UUID
                workflow_id = await self.resolve_workflow_name_to_id(linked_workflow)
                if workflow_id:
                    logger.info(f"Resolved legacy linked_workflow '{linked_workflow}' to workflow_id '{workflow_id}'")
                else:
                    logger.warning(f"Could not resolve linked_workflow '{linked_workflow}' to workflow ID for form {path}")

        # Same fallback for launch_workflow_id
        launch_workflow_id = form_data.get("launch_workflow_id")
        if not launch_workflow_id:
            launch_workflow_value = form_data.get("launch_workflow")
            if launch_workflow_value:
                # Check if it looks like a UUID (direct reference) vs a name (needs lookup)
                try:
                    from uuid import UUID as _UUID
                    _UUID(str(launch_workflow_value))
                    launch_workflow_id = str(launch_workflow_value)
                except ValueError:
                    launch_workflow_id = await self.resolve_workflow_name_to_id(launch_workflow_value)

        # Upsert form - updates definition but NOT organization_id or access_level
        # These env-specific fields are only set via the API, not from file sync
        stmt = insert(Form).values(
            id=form_id,
            name=name,
            description=form_data.get("description"),
            workflow_id=workflow_id,
            launch_workflow_id=launch_workflow_id,
            default_launch_params=form_data.get("default_launch_params"),
            allowed_query_params=form_data.get("allowed_query_params"),
            is_active=form_data.get("is_active", True),
            last_seen_at=now,
            created_by="file_sync",
        ).on_conflict_do_update(
            index_elements=[Form.id],
            set_={
                # Update definition fields from file
                "name": name,
                "description": form_data.get("description"),
                "workflow_id": workflow_id,
                "launch_workflow_id": launch_workflow_id,
                "default_launch_params": form_data.get("default_launch_params"),
                "allowed_query_params": form_data.get("allowed_query_params"),
                "is_active": form_data.get("is_active", True),
                "last_seen_at": now,
                "updated_at": now,
                # NOTE: organization_id and access_level are NOT updated
                # These are preserved from the database (env-specific)
            },
        )
        await self.db.execute(stmt)

        # Sync form_schema (fields) if present
        # Support both form_schema.fields (canonical) and flat fields (workspace shorthand)
        form_schema = form_data.get("form_schema")
        if form_schema and isinstance(form_schema, dict):
            fields_data = form_schema.get("fields", [])
        elif "fields" in form_data and isinstance(form_data["fields"], list):
            # Flat fields format — normalize 'default' to 'default_value'
            fields_data = []
            for f in form_data["fields"]:
                if isinstance(f, dict):
                    fd = dict(f)
                    if "default" in fd and "default_value" not in fd:
                        fd["default_value"] = fd.pop("default")
                    fields_data.append(fd)
        else:
            fields_data = None

        if fields_data is not None and isinstance(fields_data, list):
            # Delete existing fields
            await self.db.execute(
                delete(FormFieldORM).where(FormFieldORM.form_id == form_id)
            )

            # Create new fields from schema using Pydantic validation
            for position, field_dict in enumerate(fields_data):
                if not isinstance(field_dict, dict) or not field_dict.get("name"):
                    continue

                try:
                    form_field = FormField.model_validate(field_dict)
                except ValidationError as e:
                    logger.warning(f"Invalid field in {path}: {e}")
                    continue

                field_orm = FormFieldORM(
                    form_id=form_id,
                    name=form_field.name,
                    label=form_field.label,
                    type=form_field.type,
                    required=form_field.required,
                    position=position,
                    placeholder=form_field.placeholder,
                    help_text=form_field.help_text,
                    default_value=form_field.default_value,
                    options=form_field.options,
                    data_provider_id=form_field.data_provider_id,
                    data_provider_inputs=(
                        {k: v.model_dump() for k, v in form_field.data_provider_inputs.items()}
                        if form_field.data_provider_inputs else None
                    ),
                    visibility_expression=form_field.visibility_expression,
                    validation=form_field.validation,
                    allowed_types=form_field.allowed_types,
                    multiple=form_field.multiple,
                    max_size_mb=form_field.max_size_mb,
                    content=form_field.content,
                    allow_as_query_param=form_field.allow_as_query_param,
                    auto_fill=form_field.auto_fill,
                )
                self.db.add(field_orm)

        logger.debug(f"Indexed form: {name} from {path}")
        return content_modified

    async def delete_form_for_file(self, path: str) -> int:
        """
        Delete the form associated with a file.

        Called when a file is deleted to clean up form records from the database.
        For virtual forms, the ID is extracted from the path (forms/{uuid}.form.yaml).

        Args:
            path: File path that was deleted (e.g., "forms/{uuid}.form.yaml")

        Returns:
            Number of forms deleted
        """
        # Extract form ID from path: {uuid}.form.yaml -> uuid (any directory)
        import re
        match = re.search(r"([a-f0-9-]+)\.form\.yaml$", path, re.IGNORECASE)
        if not match:
            logger.warning(f"Cannot extract form ID from path: {path}")
            return 0

        try:
            form_id = UUID(match.group(1))
        except ValueError:
            logger.warning(f"Invalid UUID in form path: {path}")
            return 0

        # Delete the form by ID (cascade will delete form_fields)
        stmt = delete(Form).where(Form.id == form_id)
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(f"Deleted form {form_id} from database for deleted file: {path}")

        return count
