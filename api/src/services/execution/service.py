"""
Execution Service
Clean service layer for executing workflows, code, and data providers.
All execution runs in isolated subprocess via ExecutionPool.

Workflows are loaded by ID:
1. Look up workflow in database to get code and function name
2. Execute code from database (no filesystem access needed)
3. Find the @workflow decorated function by name
4. Execute in isolated subprocess
"""

from __future__ import annotations

import base64
import logging
import uuid
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.execution.module_loader import WorkflowMetadata, exec_from_db
from src.models import WorkflowExecutionResponse
from src.models.enums import ExecutionStatus

if TYPE_CHECKING:
    from src.sdk.context import ExecutionContext

logger = logging.getLogger(__name__)


class WorkflowNotFoundError(Exception):
    """Raised when a workflow cannot be found."""

    pass


class WorkflowLoadError(Exception):
    """Raised when a workflow fails to load."""

    pass


async def get_workflow_metadata_only(
    workflow_id: str,
) -> WorkflowMetadata:
    """
    Get workflow metadata by ID without loading the module.

    Uses Redis cache first, falls back to DB on miss, populates cache on miss.
    This is much faster than get_workflow_by_id() since it skips module loading.

    Args:
        workflow_id: Workflow UUID from database

    Returns:
        WorkflowMetadata with id, name, file_path, timeout, etc.

    Raises:
        WorkflowNotFoundError: If workflow doesn't exist in database
    """
    from src.core.redis_client import get_redis_client

    redis_client = get_redis_client()

    # Try Redis cache first
    cached = await redis_client.get_workflow_metadata_cache(workflow_id)
    if cached:
        logger.debug(f"Workflow metadata cache hit: {workflow_id}")
        metadata = WorkflowMetadata(
            name=cached["name"],
            timeout_seconds=cached.get("timeout_seconds", 1800),
            time_saved=cached.get("time_saved", 0),
            value=cached.get("value", 0.0),
            execution_mode=cached.get("execution_mode", "sync"),
        )
        metadata.id = cached["id"]
        metadata.source_file_path = cached["file_path"]
        return metadata

    # Cache miss - query DB
    logger.debug(f"Workflow metadata cache miss: {workflow_id}, loading from DB")

    from sqlalchemy import select
    from src.core.database import get_session_factory
    from src.models import Workflow as WorkflowORM

    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(WorkflowORM).where(
            WorkflowORM.id == workflow_id,
            WorkflowORM.is_active == True,  # noqa: E712
        )
        result = await db.execute(stmt)
        workflow_record = result.scalar_one_or_none()

    if not workflow_record:
        raise WorkflowNotFoundError(f"Workflow with ID '{workflow_id}' not found")

    # Build metadata from DB record
    metadata = WorkflowMetadata(
        name=workflow_record.name,
        timeout_seconds=workflow_record.timeout_seconds if workflow_record.timeout_seconds is not None else 1800,
        time_saved=workflow_record.time_saved or 0,
        value=float(workflow_record.value) if workflow_record.value else 0.0,
        execution_mode=workflow_record.execution_mode or "sync",
    )
    metadata.id = str(workflow_record.id)
    metadata.source_file_path = workflow_record.path

    # Populate cache for next time
    await redis_client.set_workflow_metadata_cache(
        workflow_id=str(workflow_record.id),
        name=workflow_record.name,
        file_path=workflow_record.path,
        timeout_seconds=workflow_record.timeout_seconds if workflow_record.timeout_seconds is not None else 1800,
        time_saved=workflow_record.time_saved or 0,
        value=float(workflow_record.value) if workflow_record.value else 0.0,
        execution_mode=workflow_record.execution_mode or "sync",
    )

    logger.debug(f"Loaded workflow metadata from DB: {workflow_id} -> {workflow_record.name}")
    return metadata


async def get_workflow_for_execution(
    workflow_id: str,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Get workflow metadata needed for subprocess execution.

    Returns metadata only (path, function_name, timeout, etc.) — no code.
    Workers load code via the virtual import hook: Redis cache → S3 _repo/ fallback.
    Content hash is pinned separately by the consumer for reproducibility.

    This is DB-only (no Redis caching) since metadata may be updated frequently
    and we want the latest version for each execution.

    Args:
        workflow_id: Workflow UUID from database
        db: Optional AsyncSession. If not provided, creates its own.

    Returns:
        Dict with keys:
        - name: Workflow display name
        - function_name: Python function name
        - path: Relative path (for __file__ injection and Redis/S3 loading)
        - timeout_seconds: Execution timeout
        - time_saved: ROI time saved value
        - value: ROI value
        - execution_mode: sync or async
        - organization_id: Org scope (or None for global)
        - solution_id: Solution install id if solution-managed (else None)
        - type: Executable type (workflow, tool, or data_provider)
        - cache_ttl_seconds: Data provider cache TTL

    Raises:
        WorkflowNotFoundError: If workflow doesn't exist in database
    """
    from sqlalchemy import select
    from src.core.database import get_session_factory
    from src.models import Workflow as WorkflowORM

    async def _fetch(session: AsyncSession) -> dict[str, Any]:
        from src.models.orm.solutions import Solution as SolutionORM

        stmt = (
            select(WorkflowORM, SolutionORM.global_repo_access)
            .outerjoin(SolutionORM, WorkflowORM.solution_id == SolutionORM.id)
            .where(
                WorkflowORM.id == workflow_id,
                WorkflowORM.is_active == True,  # noqa: E712
            )
        )
        result = await session.execute(stmt)
        row = result.one_or_none()

        if row is None:
            raise WorkflowNotFoundError(f"Workflow with ID '{workflow_id}' not found")

        workflow_record, global_repo_access = row
        logger.debug(f"Loaded workflow for execution: {workflow_id} -> {workflow_record.name}")

        return {
            "name": workflow_record.name,
            "function_name": workflow_record.function_name,
            "path": workflow_record.path,
            "timeout_seconds": workflow_record.timeout_seconds if workflow_record.timeout_seconds is not None else 1800,
            "time_saved": workflow_record.time_saved or 0,
            "value": float(workflow_record.value) if workflow_record.value else 0.0,
            "execution_mode": workflow_record.execution_mode or "async",
            "organization_id": str(workflow_record.organization_id) if workflow_record.organization_id else None,
            "solution_id": str(workflow_record.solution_id) if workflow_record.solution_id else None,
            "can_access_global_repo": bool(global_repo_access),
            "type": workflow_record.type or "workflow",
            "cache_ttl_seconds": workflow_record.cache_ttl_seconds or 0,
        }

    if db is not None:
        return await _fetch(db)
    else:
        session_factory = get_session_factory()
        async with session_factory() as session:
            return await _fetch(session)


async def get_workflow_by_id(
    workflow_id: str,
) -> tuple[Callable, WorkflowMetadata]:
    """
    Load a workflow by ID from the database.

    Args:
        workflow_id: Workflow UUID from database

    Returns:
        Tuple of (function, metadata)

    Raises:
        WorkflowNotFoundError: If workflow doesn't exist in database
        WorkflowLoadError: If workflow code can't be loaded
    """
    from sqlalchemy import select
    from src.core.database import get_session_factory
    from src.models import Workflow as WorkflowORM

    # Look up workflow in database
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(WorkflowORM).where(
            WorkflowORM.id == workflow_id,
            WorkflowORM.is_active == True,  # noqa: E712
        )
        result = await db.execute(stmt)
        workflow_record = result.scalar_one_or_none()

    if not workflow_record:
        raise WorkflowNotFoundError(f"Workflow with ID '{workflow_id}' not found")

    # Load code: Redis cache → S3 _repo/ fallback (same path as worker/modules)
    # file_index is a search index only, not a code source.
    code = None
    try:
        from src.core.module_cache import get_module
        cached = await get_module(workflow_record.path)
        if cached:
            code = cached["content"]
            logger.debug(f"Loaded workflow code from Redis cache for {workflow_record.path}")
    except Exception as e:
        logger.warning(f"Redis cache lookup failed for {workflow_record.path}: {e}")

    if not code:
        try:
            from src.services.repo_storage import RepoStorage
            repo = RepoStorage()
            content_bytes = await repo.read(workflow_record.path)
            code = content_bytes.decode("utf-8")
            logger.info(f"Loaded workflow code from S3 _repo/ for {workflow_record.path}")
        except Exception as e:
            logger.warning(f"S3 _repo/ fallback failed for {workflow_record.path}: {e}")

    if not code:
        raise WorkflowLoadError(
            f"Workflow '{workflow_record.name}' has no code in Redis or S3 _repo/. "
            "Ensure the workflow file has been saved."
        )

    try:
        module = exec_from_db(
            code=code,
            path=workflow_record.path,
            function_name=workflow_record.function_name,
        )
        logger.debug(f"Loaded workflow: {workflow_record.name}")
    except (SyntaxError, ImportError) as e:
        raise WorkflowLoadError(f"Failed to execute workflow code: {e}")

    # Find the decorated function by name
    # All decorators (@workflow, @tool, @data_provider) use unified _executable_metadata
    workflow_func = None
    workflow_metadata = None

    for name in dir(module):
        obj = getattr(module, name)
        # Only check callable objects (functions) for executable metadata
        # This avoids triggering __getattr__ on proxy objects like `context`
        if not callable(obj):
            continue

        # All decorators use _executable_metadata
        if hasattr(obj, "_executable_metadata"):
            meta = getattr(obj, "_executable_metadata", None)
            if meta and hasattr(meta, 'name') and meta.name == workflow_record.name:
                workflow_func = obj
                # For data providers, convert to WorkflowMetadata for consistent execution
                if hasattr(meta, 'type') and meta.type == 'data_provider':
                    workflow_metadata = WorkflowMetadata(
                        name=meta.name,
                        description=meta.description,
                        category=getattr(meta, 'category', 'General'),
                        parameters=getattr(meta, 'parameters', []),
                        timeout_seconds=getattr(meta, 'timeout_seconds', 300),
                        type='data_provider',
                    )
                    workflow_metadata.id = str(workflow_record.id)
                    workflow_metadata.source_file_path = workflow_record.path
                elif isinstance(meta, WorkflowMetadata):
                    workflow_metadata = meta
                else:
                    # Convert from old type if needed
                    from src.services.execution.module_loader import _convert_workflow_metadata
                    workflow_metadata = _convert_workflow_metadata(meta)
                break

    if not workflow_func or not workflow_metadata:
        raise WorkflowLoadError(
            f"No decorated function named '{workflow_record.name}' found in {workflow_record.path}"
        )

    # Enrich metadata from database record
    workflow_metadata.id = str(workflow_record.id)
    workflow_metadata.source_file_path = workflow_record.path

    logger.debug(f"Loaded workflow by ID: {workflow_id} -> {workflow_record.name}")
    return workflow_func, workflow_metadata


async def run_workflow(
    context: "ExecutionContext",
    workflow_id: str,
    input_data: dict[str, Any] | None = None,
    form_id: str | None = None,
    transient: bool = False,
    sync: bool = False,
) -> WorkflowExecutionResponse:
    """
    Execute a workflow by ID.

    All workflows are executed via the worker queue (RabbitMQ). The `sync` parameter
    controls whether we wait for the result or return immediately with PENDING status.

    Args:
        context: ExecutionContext with org scope and user info
        workflow_id: UUID of workflow to execute (from database)
        input_data: Input parameters for the workflow
        form_id: Optional form ID if triggered by form
        transient: If True, don't persist execution record
        sync: If True, wait for result via Redis BLPOP. If False, return PENDING immediately.

    Returns:
        WorkflowExecutionResponse with execution results (or PENDING status if sync=False)

    Raises:
        WorkflowNotFoundError: If workflow doesn't exist
        WorkflowLoadError: If workflow fails to load (syntax error, etc.)
    """
    parameters = input_data or {}

    # Validate workflow exists using metadata-only lookup (Redis-first, no module loading)
    try:
        workflow_metadata = await get_workflow_metadata_only(workflow_id)
        logger.debug(
            f"Validated workflow by ID: {workflow_id} -> {workflow_metadata.name}"
        )
    except WorkflowNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Failed to validate workflow {workflow_id}: {e}", exc_info=True)
        raise WorkflowNotFoundError(
            f"Failed to validate workflow '{workflow_id}': {str(e)}"
        )

    # Enqueue for execution via worker
    # sync is only True when explicitly passed by the caller (e.g. endpoints.py)
    return await _enqueue_workflow_async(
        context=context,
        workflow_id=workflow_id,
        workflow_name=workflow_metadata.name,
        parameters=parameters,
        form_id=form_id,
        sync=sync,
    )


async def run_code(
    context: "ExecutionContext",
    code: str,
    script_name: str = "inline_script",
    input_data: dict[str, Any] | None = None,
    transient: bool = False,
) -> WorkflowExecutionResponse:
    """
    Execute inline Python code.

    Args:
        context: ExecutionContext with org scope and user info
        code: Python code to execute
        script_name: Name for the script execution
        input_data: Input parameters for the script
        transient: If True, don't persist execution record

    Returns:
        WorkflowExecutionResponse with execution results
    """
    parameters = input_data or {}
    code_base64 = base64.b64encode(code.encode()).decode()

    # Scripts always run async
    return await _enqueue_code_async(
        context=context,
        script_name=script_name,
        code_base64=code_base64,
        parameters=parameters,
    )


async def _enqueue_workflow_async(
    context: "ExecutionContext",
    workflow_id: str,
    workflow_name: str,
    parameters: dict[str, Any],
    form_id: str | None = None,
    sync: bool = False,
) -> WorkflowExecutionResponse:
    """
    Enqueue workflow for execution via RabbitMQ.

    If sync=True, waits for result via Redis BLPOP.
    If sync=False, returns immediately with PENDING status.
    """
    from src.services.execution.async_executor import enqueue_workflow_execution
    from src.core.redis_client import get_redis_client

    execution_id = await enqueue_workflow_execution(
        context=context,
        workflow_id=workflow_id,
        parameters=parameters,
        form_id=form_id,
        execution_id=context.execution_id,  # Pass through for log streaming
        sync=sync,
    )

    if not sync:
        # Return immediately with pending status
        return WorkflowExecutionResponse(
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status=ExecutionStatus.PENDING,
        )

    # Wait for result via Redis BLPOP — use actual workflow timeout (+ 60s buffer)
    # 0 = no timeout: cap at 86400 (24h) to prevent infinite BLPOP hang
    redis_client = get_redis_client()
    workflow_meta = await get_workflow_metadata_only(workflow_id)
    if workflow_meta.timeout_seconds > 0:
        wait_timeout = workflow_meta.timeout_seconds + 60
    else:
        wait_timeout = 86400
    result = await redis_client.wait_for_result(execution_id, timeout_seconds=wait_timeout)

    if result is None:
        return WorkflowExecutionResponse(
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status=ExecutionStatus.TIMEOUT,
            error="Execution timed out waiting for result",
            error_type="TimeoutError",
        )

    # Map result to response
    status_str = result.get("status", "Failed")
    status = (
        ExecutionStatus(status_str)
        if status_str in [s.value for s in ExecutionStatus]
        else ExecutionStatus.FAILED
    )

    return WorkflowExecutionResponse(
        execution_id=execution_id,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        status=status,
        result=result.get("result"),
        error=result.get("error"),
        error_type=result.get("error_type"),
        duration_ms=result.get("duration_ms"),
    )


async def _enqueue_code_async(
    context: "ExecutionContext",
    script_name: str,
    code_base64: str,
    parameters: dict[str, Any],
) -> WorkflowExecutionResponse:
    """Enqueue inline code for async execution via RabbitMQ."""
    from src.services.execution.async_executor import enqueue_code_execution

    execution_id = await enqueue_code_execution(
        context=context,
        script_name=script_name,
        code_base64=code_base64,
        parameters=parameters,
    )

    return WorkflowExecutionResponse(
        execution_id=execution_id,
        workflow_name=script_name,
        status=ExecutionStatus.PENDING,
    )


async def execute_tool(
    workflow_id: str,
    workflow_name: str,
    parameters: dict[str, Any],
    user_id: str,
    user_email: str,
    user_name: str,
    org_id: str | None = None,
    org_name: str | None = None,
    is_platform_admin: bool = False,
    is_agent: bool = False,
    execution_id: str | None = None,
) -> WorkflowExecutionResponse:
    """
    Execute a workflow as a tool (for AI agent tool calls).

    Uses sync execution via RabbitMQ with Redis BLPOP for result.

    Args:
        workflow_id: Workflow UUID
        workflow_name: Workflow name for display
        parameters: Tool call arguments
        user_id: User ID executing the tool
        user_email: User email
        user_name: User display name
        org_id: Organization ID (optional)
        org_name: Organization name (optional)
        is_platform_admin: Whether user is platform admin
        execution_id: Optional pre-generated execution ID (for streaming)

    Returns:
        WorkflowExecutionResponse with execution results
    """
    from src.sdk.context import ExecutionContext, Organization

    # Build organization if provided.
    #
    # Only org_id is load-bearing: _enqueue_workflow_async passes org_id (not
    # this object) to the queue, and the worker rehydrates the org — including
    # is_provider — from org_id via OrganizationRepository.get_with_cache in the
    # workflow_execution consumer. This object is never serialized, so omitting
    # is_provider here is intentional, not a scope-bypass gap.
    org = None
    if org_id:
        org = Organization(
            id=org_id,
            name=org_name or "",
            is_active=True,
        )

    # Use provided execution_id or generate a new one
    if not execution_id:
        execution_id = str(uuid.uuid4())

    from src.config import get_settings

    context = ExecutionContext(
        user_id=user_id,
        email=user_email,
        name=user_name,
        scope=org_id or "GLOBAL",
        organization=org,
        is_platform_admin=is_platform_admin,
        is_function_key=False,
        is_agent=is_agent,
        execution_id=execution_id,
        workflow_name=workflow_name,  # Workflow name for context
        public_url=get_settings().public_url,
    )

    # Execute synchronously via queue
    return await _enqueue_workflow_async(
        context=context,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        parameters=parameters,
        sync=True,  # Wait for result
    )
