"""
Workflow Validation Service

Validates workflow files for syntax errors, decorator issues, and Pydantic validation.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.models import WorkflowMetadata as WorkflowMetadataModel

logger = logging.getLogger(__name__)


def _extract_relative_path(source_file_path: str | None) -> str | None:
    """
    Return the relative file path for display.

    Args:
        source_file_path: File path (should be relative from database)

    Returns:
        The relative path as-is, or None if not provided
    """
    if not source_file_path:
        return None

    # Paths are now stored as relative paths in the database
    # e.g., "features/ticketing/workflows/create_ticket.py"
    # Just return as-is - no prefixes needed
    return source_file_path


def _convert_workflow_metadata_to_model(
    workflow_metadata: Any,
) -> WorkflowMetadataModel:
    """
    Convert a discovery WorkflowMetadata dataclass to a Pydantic model.

    Args:
        workflow_metadata: Workflow metadata from discovery (dataclass)

    Returns:
        WorkflowMetadata Pydantic model for API response
    """
    # Convert parameters from dataclass to dict with proper field mapping
    parameters = []
    if workflow_metadata.parameters:
        for p in workflow_metadata.parameters:
            param_dict = {
                "name": p.name,
                "type": p.type,
                "required": p.required,
            }
            # Add optional fields only if they're not None
            # Note: data_provider, help_text, validation, options are form concerns,
            # not workflow concerns. Workflow parameters come from function signatures.
            if p.label is not None:
                param_dict["label"] = p.label
            if p.default_value is not None:
                param_dict["default_value"] = p.default_value
            parameters.append(param_dict)

    # Extract the relative path for display
    relative_path = _extract_relative_path(workflow_metadata.source_file_path)

    # Generate a placeholder ID for validation
    # (workflows get real IDs when saved to the database)
    workflow_id = f"pending-{workflow_metadata.name}"

    return WorkflowMetadataModel(
        id=workflow_id,
        name=workflow_metadata.name,
        description=workflow_metadata.description,
        category=workflow_metadata.category,
        tags=workflow_metadata.tags or [],
        parameters=parameters,
        execution_mode=workflow_metadata.execution_mode,
        # NOT `or 1800` — 0 means "no timeout" and `or` would clobber it.
        timeout_seconds=(
            workflow_metadata.timeout_seconds
            if workflow_metadata.timeout_seconds is not None
            else 1800
        ),
        retry_policy=None,
        schedule=None,
        endpoint_enabled=False,
        disable_global_key=False,
        public_endpoint=False,
        time_saved=workflow_metadata.time_saved or 0,
        value=workflow_metadata.value or 0.0,
        source_file_path=workflow_metadata.source_file_path,
        relative_file_path=relative_path,
        created_at=datetime.now(timezone.utc),
    )


async def validate_workflow_file(path: str, content: str | None = None):
    """
    Validate a workflow file for syntax errors, decorator issues, and Pydantic validation.

    Args:
        path: Relative workspace path to the workflow file
        content: File content (required - we no longer read from filesystem)

    Returns:
        WorkflowValidationResponse with validation results

    Note:
        Content is now required. The filesystem is no longer used for workflow
        validation - all code is stored in the database. If content is not provided,
        the function will attempt to load it from the database.
    """
    import tempfile
    import re
    from pydantic import ValidationError
    from src.models import WorkflowValidationResponse, ValidationIssue
    from src.services.execution.module_loader import import_module, WorkflowMetadata
    from src.services.execution.type_inference import VALID_PARAM_TYPES

    issues = []
    valid = True
    metadata = None

    # If content not provided, try to load from database
    if content is None:
        from src.core.database import get_db_context
        from src.services.file_storage import FileStorageService

        try:
            async with get_db_context() as db:
                service = FileStorageService(db)
                content_bytes, _ = await service.read_file(path)
                content = content_bytes.decode("utf-8")
        except FileNotFoundError:
            issues.append(ValidationIssue(
                line=None,
                message=f"File not found in database: {path}",
                severity="error"
            ))
            return WorkflowValidationResponse(valid=False, issues=issues, metadata=None)
        except Exception as e:
            issues.append(ValidationIssue(
                line=None,
                message=f"Failed to read file from database: {str(e)}",
                severity="error"
            ))
            return WorkflowValidationResponse(valid=False, issues=issues, metadata=None)

    # Create a temporary file with the content for validation
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    try:
        temp_file.write(content)
        temp_file.close()
        file_to_validate = Path(temp_file.name)
        file_content = content
    except Exception as e:
        issues.append(ValidationIssue(
            line=None,
            message=f"Failed to write temporary file: {str(e)}",
            severity="error"
        ))
        return WorkflowValidationResponse(valid=False, issues=issues, metadata=None)

    try:
        # Step 1: Check for Python syntax errors
        try:
            compile(file_content, str(file_to_validate), 'exec')
        except SyntaxError as e:
            issues.append(ValidationIssue(
                line=e.lineno,
                message=f"Syntax error: {e.msg}",
                severity="error"
            ))
            valid = False
            return WorkflowValidationResponse(valid=valid, issues=issues, metadata=None)

        # Step 2: Check for import errors by attempting to load the module
        try:
            module = import_module(file_to_validate)
        except Exception as e:
            # Import error - could be missing dependencies or runtime errors
            issues.append(ValidationIssue(
                line=None,
                message=f"Import error: {str(e)}",
                severity="error"
            ))
            valid = False
            return WorkflowValidationResponse(valid=valid, issues=issues, metadata=None)

        # Step 3: Check if @workflow/@tool decorator was found by scanning module
        # All decorators use unified _executable_metadata attribute
        discovered_workflows: list[WorkflowMetadata] = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if callable(attr) and hasattr(attr, '_executable_metadata'):
                metadata_obj = attr._executable_metadata
                # Only include workflows and tools, not data providers
                if isinstance(metadata_obj, WorkflowMetadata):
                    discovered_workflows.append(metadata_obj)
                elif hasattr(metadata_obj, 'type') and metadata_obj.type in ('workflow', 'tool'):
                    # Convert to WorkflowMetadata if needed
                    from src.services.execution.module_loader import _convert_workflow_metadata
                    discovered_workflows.append(_convert_workflow_metadata(metadata_obj))

        if not discovered_workflows:
            issues.append(ValidationIssue(
                line=None,
                message="No @workflow decorator found. Functions must use @workflow(...) to be discoverable.",
                severity="error"
            ))
            valid = False
            return WorkflowValidationResponse(valid=valid, issues=issues, metadata=None)

        # Use the first matching workflow for validation
        # (most files will have just one workflow, but we support multiple)
        workflow_metadata = discovered_workflows[0]

        # Step 4: Validate workflow name pattern
        name_pattern = r"^[a-z0-9_]+$"
        if not re.match(name_pattern, workflow_metadata.name):
            issues.append(ValidationIssue(
                line=None,
                message=f"Invalid workflow name '{workflow_metadata.name}'. Name must be lowercase snake_case (only letters, numbers, underscores).",
                severity="error"
            ))
            valid = False

        # Step 5: Validate required fields
        if not workflow_metadata.description or len(workflow_metadata.description.strip()) == 0:
            issues.append(ValidationIssue(
                line=None,
                message="Workflow description is required and cannot be empty.",
                severity="error"
            ))
            valid = False

        # Step 6: Validate execution mode
        if workflow_metadata.execution_mode not in ["sync", "async"]:
            issues.append(ValidationIssue(
                line=None,
                message=f"Invalid execution mode '{workflow_metadata.execution_mode}'. Must be 'sync' or 'async'.",
                severity="error"
            ))
            valid = False

        # Step 7: Validate timeout
        if workflow_metadata.timeout_seconds is not None:
            if workflow_metadata.timeout_seconds < 0 or workflow_metadata.timeout_seconds > 86400:
                issues.append(ValidationIssue(
                    line=None,
                    message=f"Invalid timeout {workflow_metadata.timeout_seconds}s. Must be between 0 and 86400 seconds.",
                    severity="error"
                ))
                valid = False

        # Step 8: Validate parameter types
        if workflow_metadata.parameters:
            for param in workflow_metadata.parameters:
                if param.type not in VALID_PARAM_TYPES:
                    issues.append(ValidationIssue(
                        line=None,
                        message=f"Invalid parameter type '{param.type}' for parameter '{param.name}'. Must be one of: {', '.join(VALID_PARAM_TYPES)}",
                        severity="error"
                    ))
                    valid = False

        # Step 9: Validate Pydantic model conversion (this is what discovery endpoint does)
        try:
            metadata = _convert_workflow_metadata_to_model(workflow_metadata)
        except ValidationError as e:
            for error in e.errors():
                field = ".".join(str(loc) for loc in error["loc"])
                issues.append(ValidationIssue(
                    line=None,
                    message=f"Validation error in field '{field}': {error['msg']}",
                    severity="error"
                ))
            valid = False

        # Step 10: Warnings for best practices
        if workflow_metadata.category == "General":
            issues.append(ValidationIssue(
                line=None,
                message="Consider specifying a category other than 'General' for better organization.",
                severity="warning"
            ))

        if not workflow_metadata.tags or len(workflow_metadata.tags) == 0:
            issues.append(ValidationIssue(
                line=None,
                message="Consider adding tags to make your workflow more discoverable.",
                severity="warning"
            ))

    finally:
        # Clean up temporary file if created
        if content is not None:
            try:
                os.unlink(temp_file.name)
            except OSError as e:
                # Temp file may already be gone or permissions changed — non-fatal
                logger.debug(f"could not remove temp validation file {temp_file.name}: {e}")

    return WorkflowValidationResponse(valid=valid, issues=issues, metadata=metadata)
