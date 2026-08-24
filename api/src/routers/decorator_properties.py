"""
Decorator Properties Router

API endpoints for reading and writing decorator properties in Python source files.
Used by the Monaco editor for property editing and by the file storage service
for automatic ID injection.

Platform admin resource - no org scoping.
"""

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context
from src.core.database import get_db
from src.core.log_safety import log_safe
from src.models import (
    DecoratorInfo,
    DecoratorPropertiesResponse,
    UpdatePropertiesRequest,
    UpdatePropertiesResponse,
)
from src.services.decorator_property_service import DecoratorPropertyService
from src.services.file_storage import FileStorageService
from src.services.audit import emit_audit
from src.services.authorization import AuthorizationContext, CurrentAuthorizationContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decorator-properties", tags=["Decorator Properties"])


def _require_platform_repository(
    authorization: AuthorizationContext,
    *,
    write: bool,
) -> None:
    authorization.require("repository.readwrite" if write else "repository.read")
    authorization.require_resource_boundary(None)


@router.get(
    "",
    response_model=DecoratorPropertiesResponse,
    summary="Read decorator properties from a file",
    description="Read all workflow/data_provider/tool decorator properties from a Python file",
)
async def get_decorator_properties(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    path: str = Query(..., description="File path relative to workspace root"),
    db: AsyncSession = Depends(get_db),
) -> DecoratorPropertiesResponse:
    """
    Read all decorator properties from a Python file.

    Args:
        path: File path relative to workspace root

    Returns:
        DecoratorPropertiesResponse with list of decorators and their properties
    """
    _require_platform_repository(authorization, write=False)
    if not path.endswith(".py"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Python files are supported",
        )

    try:
        storage = FileStorageService(db)
        content, _ = await storage.read_file(path)
        content_str = content.decode("utf-8", errors="replace")

        service = DecoratorPropertyService()
        decorators = service.read_decorators(content_str)

        return DecoratorPropertiesResponse(
            path=path,
            decorators=[
                DecoratorInfo(
                    decorator_type=d.decorator_type,
                    function_name=d.function_name,
                    line_number=d.line_number,
                    properties=d.properties,
                    has_parentheses=d.has_parentheses,
                )
                for d in decorators
            ],
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )
    except Exception as e:
        logger.error(
            f"Error reading decorator properties from {log_safe(path)}: {log_safe(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read decorator properties",
        )


@router.put(
    "",
    response_model=UpdatePropertiesResponse,
    summary="Update decorator properties",
    description="Update properties on a decorator in a Python file",
)
async def update_decorator_properties(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    request: UpdatePropertiesRequest,
    db: AsyncSession = Depends(get_db),
) -> UpdatePropertiesResponse:
    """
    Update properties on a decorator.

    Uses ETag for optimistic concurrency control - if the file has been
    modified since the ETag was generated, the request will fail with 409.

    Args:
        request: UpdatePropertiesRequest with path, function_name, properties, and optional expected_etag

    Returns:
        UpdatePropertiesResponse with modification status and new ETag
    """
    _require_platform_repository(authorization, write=True)
    if not request.path.endswith(".py"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Python files are supported",
        )

    try:
        storage = FileStorageService(db)

        # Read current content
        try:
            content, _ = await storage.read_file(request.path)
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {request.path}",
            )

        # Check ETag for conflict detection
        if request.expected_etag:
            current_etag = hashlib.md5(content).hexdigest()
            if current_etag != request.expected_etag:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "reason": "content_changed",
                        "message": "File was modified since you last read it",
                        "current_etag": current_etag,
                    },
                )

        content_str = content.decode("utf-8", errors="replace")

        # Apply property changes
        service = DecoratorPropertyService()
        result = service.write_properties(
            content_str,
            request.function_name,
            request.properties,
        )

        if not result.modified:
            # No changes needed - return current ETag
            current_etag = hashlib.md5(content).hexdigest()
            return UpdatePropertiesResponse(
                modified=False,
                changes=result.changes,
                new_etag=current_etag,
            )

        # Write modified content
        modified_content = result.new_content.encode("utf-8")
        write_result = await storage.write_file(
            request.path,
            modified_content,
            updated_by=authorization.effective_actor.email,
        )

        await emit_audit(
            db,
            "repository.decorator_properties.update",
            resource_type="repository_file",
            details={
                "path": request.path,
                "function_name": request.function_name,
                "changes": result.changes,
            },
        )

        new_etag = hashlib.md5(write_result.final_content).hexdigest()
        logger.info(
            f"Updated decorator properties in {log_safe(request.path)} "
            f"for {log_safe(request.function_name)}: {result.changes}"
        )

        return UpdatePropertiesResponse(
            modified=True,
            changes=result.changes,
            new_etag=new_etag,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error updating decorator properties in {log_safe(request.path)}: {log_safe(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update decorator properties",
        )
