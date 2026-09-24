"""Shared execution core for isolated worker processes.

The forked worker adapter supplies an execution context to ``run_execution``,
which loads and runs the workflow or script and returns its result with
resource metrics. Logs are written to Redis Stream by the execution engine.

The worker imports minimal dependencies to keep memory footprint low.

IMPORTANT: The virtual import hook is installed at module load time
(before any workspace imports can occur) to enable loading Python
modules from Redis cache instead of the filesystem.
"""

from __future__ import annotations

import logging
import resource
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Install virtual import hook IMMEDIATELY at module load time.
# This must happen before any workspace imports (e.g., from shared import ...)
# The hook intercepts imports and loads modules from Redis cache.
from src.services.execution.virtual_import import install_virtual_import_hook
from src.services.execution.workspace_modules import (
    clear_workspace_modules as _clear_workspace_modules,
)

install_virtual_import_hook()

logger = logging.getLogger(__name__)


def _set_process_engine_credentials(context_data: dict[str, Any]) -> bool:
    """Install the handed-down engine token for this one-shot process."""
    engine_token = context_data.get("engine_token")
    if not engine_token:
        return False

    import os

    api_url = os.getenv("BIFROST_API_URL", "http://api:8000")
    # The SDK's process backend takes precedence over keyring or JSON
    # persistence and supports refresh-on-401 by updating this tuple. Avoiding
    # persistence also prevents headless keyring probes from importing optional
    # DBus/desktop modules through the virtual importer on every execution.
    os.environ["BIFROST_API_URL"] = api_url
    os.environ["BIFROST_ACCESS_TOKEN"] = engine_token
    os.environ["BIFROST_REFRESH_TOKEN"] = engine_token
    return True


@dataclass
class ResourceMetrics:
    """Resource usage metrics captured during execution."""
    # Memory metrics (bytes)
    peak_memory_bytes: int  # Maximum RSS during execution
    # CPU metrics (seconds)
    cpu_user_seconds: float  # User-mode CPU time
    cpu_system_seconds: float  # Kernel-mode CPU time
    cpu_total_seconds: float  # Total CPU time (user + system)


def _get_resource_usage() -> tuple[int, float, float]:
    """Get current resource usage from the OS.

    Returns:
        Tuple of (max_rss_bytes, user_cpu_seconds, system_cpu_seconds)
    """
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is in KB on Linux, bytes on macOS
    # Normalize to bytes
    if sys.platform == 'darwin':
        max_rss_bytes = usage.ru_maxrss  # Already in bytes on macOS
    else:
        max_rss_bytes = usage.ru_maxrss * 1024  # KB to bytes on Linux

    return max_rss_bytes, usage.ru_utime, usage.ru_stime


def _capture_metrics(start_rss: int, start_utime: float, start_stime: float) -> ResourceMetrics:
    """Capture resource metrics since execution started.

    Args:
        start_rss: RSS at start (for reference, we use peak which is cumulative)
        start_utime: User CPU time at start
        start_stime: System CPU time at start

    Returns:
        ResourceMetrics with delta values
    """
    end_rss, end_utime, end_stime = _get_resource_usage()

    cpu_user = end_utime - start_utime
    cpu_system = end_stime - start_stime

    return ResourceMetrics(
        peak_memory_bytes=end_rss,  # Peak is cumulative from process start
        cpu_user_seconds=round(cpu_user, 4),
        cpu_system_seconds=round(cpu_system, 4),
        cpu_total_seconds=round(cpu_user + cpu_system, 4),
    )


async def run_execution(execution_id: str, context_data: dict[str, Any]) -> dict[str, Any]:
    """
    Run the actual execution.

    This is the core execution logic, isolated in the worker process.
    """
    from src.sdk.context import Caller, Organization
    from src.services.execution.engine import ExecutionRequest, execute
    from src.models.enums import ExecutionStatus
    from bifrost.credentials import is_token_expired

    service_cfg = context_data.get("service")
    if service_cfg:
        return await run_service(service_cfg, context_data)

    # Engine credentials: prefer the pre-minted token handed down from the
    # consumer via context_data["engine_token"].  This keeps SECRET_KEY out of
    # the child process entirely.
    # Fall back to authenticate_engine() only for callers (e.g. direct engine
    # tests) that bypass the consumer and do not supply a pre-minted token.
    if not _set_process_engine_credentials(context_data) and is_token_expired(
        buffer_seconds=3600
    ):
        from src.core.security import authenticate_engine
        authenticate_engine()

    start_time = datetime.now(timezone.utc)

    # Capture starting resource usage
    start_rss, start_utime, start_stime = _get_resource_usage()

    # Activate the per-execution Solution import root BEFORE any workspace code
    # loads. With no solution_id this is a no-op (plain _repo/ behavior). The
    # finally below always clears it so a forked worker reused for the next
    # execution never inherits a stale root. See module_cache_sync.
    from src.core.module_cache_sync import clear_solution_context, set_solution_context

    _exec_solution_id = context_data.get("solution_id")
    if _exec_solution_id:
        set_solution_context(
            _exec_solution_id,
            global_repo_access=bool(context_data.get("solution_global_repo_access", False)),
        )

    try:
        # The shared execution boundary owns cache freshness so direct callers
        # and alternate worker paths cannot inherit stale workspace imports.
        # This must run after Solution context activation because resolution is
        # scoped to the active install.
        _clear_workspace_modules()

        # Reconstruct Organization
        org = None
        org_data = context_data.get("organization")
        if org_data:
            org = Organization(
                id=org_data["id"],
                name=org_data["name"],
                is_active=org_data.get("is_active", True),
                is_provider=org_data.get("is_provider", False),
            )

        # Reconstruct Caller
        caller_data = context_data["caller"]
        caller = Caller(
            user_id=caller_data["user_id"],
            email=caller_data["email"],
            name=caller_data["name"]
        )

        # Load executable function if not a script
        # All types (workflow, tool, data_provider) use the same unified loader
        workflow_func = None
        metadata = None
        is_script = bool(context_data.get("code"))

        if not is_script:
            from src.services.execution.module_loader import load_workflow_from_db

            name = context_data["name"]
            function_name = context_data.get("function_name")
            file_path = context_data.get("file_path")
            load_error: str | None = None
            loaded_code: str | None = None

            # Load code from Redis→S3 _repo/ cache (same path as module imports).
            # Consumer provides metadata only; worker is self-sufficient for code loading.
            if function_name and file_path:
                try:
                    from src.core.module_cache_sync import get_module_sync

                    cached = get_module_sync(file_path)
                    if cached:
                        loaded_code = cached["content"]
                        workflow_func, metadata, load_error = load_workflow_from_db(
                            code=loaded_code,
                            path=file_path,
                            function_name=function_name,
                        )
                        logger.info(
                            f"Loaded workflow '{name}' from cache (path={file_path})"
                        )
                    else:
                        logger.error(
                            f"Workflow code not found in cache or S3: "
                            f"function_name={function_name}, file_path={file_path}"
                        )
                except Exception as e:
                    logger.error(f"Failed to load workflow from cache: {e}")
                    load_error = f"Cache load failed: {e}"
            else:
                # Missing required fields for execution
                logger.error(
                    f"Missing required fields for workflow execution: "
                    f"function_name={function_name}, "
                    f"file_path={file_path}"
                )

            # Validate content hash if pinned at dispatch time
            content_hash = context_data.get("content_hash")
            if content_hash and loaded_code:
                import hashlib

                actual_hash = hashlib.sha256(
                    loaded_code.encode("utf-8")
                ).hexdigest()
                if actual_hash != content_hash:
                    logger.warning(
                        f"Content hash mismatch for {file_path}: "
                        f"expected={content_hash[:12]}... actual={actual_hash[:12]}... "
                        f"Code may have changed between dispatch and execution."
                    )

            if workflow_func is None:
                metrics = _capture_metrics(start_rss, start_utime, start_stime)
                # Use the actual error if available, otherwise fall back to generic message
                error_msg = load_error or f"Executable '{name}' not found"
                error_type = "WorkflowLoadError" if load_error else "ExecutableNotFound"
                return {
                    "status": ExecutionStatus.FAILED.value,
                    "error_message": error_msg,
                    "error_type": error_type,
                    "duration_ms": int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
                    "result": None,
                    "logs": [],
                    "variables": None,
                    "metrics": {
                        "peak_memory_bytes": metrics.peak_memory_bytes,
                        "cpu_user_seconds": metrics.cpu_user_seconds,
                        "cpu_system_seconds": metrics.cpu_system_seconds,
                        "cpu_total_seconds": metrics.cpu_total_seconds,
                    },
                }

        # Reconstruct EventContext for event-triggered executions
        event_ctx = None
        event_dict = context_data.get("event")
        if event_dict:
            from bifrost._execution_context import EventContext
            event_ctx = EventContext(**event_dict)

        # Build execution request
        request = ExecutionRequest(
            execution_id=execution_id,
            caller=caller,
            organization=org,
            func=workflow_func,
            code=context_data.get("code"),
            name=context_data.get("name"),
            tags=context_data.get("tags", []),
            timeout_seconds=context_data.get("timeout_seconds", 1800),
            workflow_deadline=(
                datetime.fromisoformat(context_data["workflow_deadline"])
                if context_data.get("workflow_deadline") else None
            ),
            cache_ttl_seconds=context_data.get("cache_ttl_seconds", 300),
            parameters=context_data.get("parameters", {}),
            startup=context_data.get("startup"),  # Launch workflow results
            form_inputs=context_data.get("form_inputs", {}),
            embed=context_data.get("embed", {}),
            roi=context_data.get("roi"),  # ROI initialization
            transient=context_data.get("transient", False),
            no_cache=context_data.get("no_cache", False),
            is_platform_admin=context_data.get("is_platform_admin", False),
            broadcaster=None,  # Logs go to Redis Stream directly
            event=event_ctx,
            solution_id=context_data.get("solution_id"),  # install scope for SDK
            artifact_workspace_id=context_data.get("artifact_workspace_id"),
        )

        # Execute
        exec_result = await execute(request)

        # Capture resource metrics after execution
        metrics = _capture_metrics(start_rss, start_utime, start_stime)

        # Convert result to dict for serialization
        return {
            "status": exec_result.status.value,
            "result": exec_result.result,
            "duration_ms": exec_result.duration_ms,
            "logs": exec_result.logs,
            "variables": exec_result.variables,
            "integration_calls": exec_result.integration_calls,
            "roi": exec_result.roi,
            "error_message": exec_result.error_message,
            "error_type": exec_result.error_type,
            "cached": exec_result.cached,
            "cache_expires_at": exec_result.cache_expires_at,
            "execution_context": exec_result.execution_context,
            "metrics": {
                "peak_memory_bytes": metrics.peak_memory_bytes,
                "cpu_user_seconds": metrics.cpu_user_seconds,
                "cpu_system_seconds": metrics.cpu_system_seconds,
                "cpu_total_seconds": metrics.cpu_total_seconds,
            },
        }

    except Exception as e:
        import traceback
        logger.exception(f"Worker execution failed: {e}")
        e.__traceback__ = None  # Free frame references to prevent memory leaks

        # Still capture metrics even on failure
        metrics = _capture_metrics(start_rss, start_utime, start_stime)

        return {
            "status": ExecutionStatus.FAILED.value,
            "error_message": str(e),
            "error_type": type(e).__name__,
            "duration_ms": int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
            "result": None,
            "logs": [],
            "variables": None,
            "traceback": traceback.format_exc(),
            "metrics": {
                "peak_memory_bytes": metrics.peak_memory_bytes,
                "cpu_user_seconds": metrics.cpu_user_seconds,
                "cpu_system_seconds": metrics.cpu_system_seconds,
                "cpu_total_seconds": metrics.cpu_total_seconds,
            },
        }

    finally:
        # Always clear the solution import root before any final cleanup in this
        # execution process. Current pool children are one-shot.
        clear_solution_context()


async def run_service(
    service_cfg: dict[str, Any], context_data: dict[str, Any]
) -> dict[str, Any]:
    """Run one supervised @service attempt in this child process.

    Mirrors ``run_execution`` loading (Solution scope, module cache, content
    hash) but invokes the engine in service mode: the coroutine runs until it
    returns, raises, or a stop is requested. The returned envelope carries
    the service identity so the parent routes it to attempt completion
    instead of the one-shot execution callback.
    """
    from datetime import datetime, timezone

    from src.sdk.context import Caller, Organization
    from src.services.execution.engine import (
        ExecutionRequest,
        ServiceRunConfig,
        execute,
    )
    from src.models.enums import ExecutionStatus

    start_time = datetime.now(timezone.utc)
    attempt_id = str(service_cfg.get("attempt_id", context_data.get("execution_id")))

    from src.core.module_cache_sync import clear_solution_context, set_solution_context

    _exec_solution_id = context_data.get("solution_id")
    if _exec_solution_id:
        set_solution_context(
            _exec_solution_id,
            global_repo_access=bool(context_data.get("solution_global_repo_access", False)),
        )

    try:
        _clear_workspace_modules()

        org = None
        org_data = context_data.get("organization")
        if org_data:
            org = Organization(
                id=org_data["id"],
                name=org_data["name"],
                is_active=org_data.get("is_active", True),
                is_provider=org_data.get("is_provider", False),
            )

        caller_data = context_data["caller"]
        caller = Caller(
            user_id=caller_data["user_id"],
            email=caller_data["email"],
            name=caller_data["name"],
        )

        # Renewable service credential handed down at dispatch (no SECRET_KEY
        # in the child). The engine supervisor refreshes it from the parent's
        # Redis handoff for the life of the attempt.
        from bifrost._service_runtime import install_service_credentials

        install_service_credentials(service_cfg.get("token", ""))

        # Load the @service function (same DB-first loader as workflows).
        service_func = None
        load_error: str | None = None
        loaded_code: str | None = None
        function_name = context_data.get("function_name")
        file_path = context_data.get("file_path")
        if function_name and file_path:
            try:
                from src.core.module_cache_sync import get_module_sync
                from src.services.execution.module_loader import load_workflow_from_db

                cached = get_module_sync(file_path)
                if cached:
                    loaded_code = cached["content"]
                    service_func, _, load_error = load_workflow_from_db(
                        code=loaded_code,
                        path=file_path,
                        function_name=function_name,
                    )
                else:
                    load_error = (
                        f"Service code not found in cache or S3: "
                        f"function_name={function_name}, file_path={file_path}"
                    )
            except Exception as e:
                load_error = f"Cache load failed: {e}"
        else:
            load_error = (
                f"Missing required fields for service execution: "
                f"function_name={function_name}, file_path={file_path}"
            )

        content_hash = context_data.get("content_hash")
        if content_hash and loaded_code:
            import hashlib

            actual_hash = hashlib.sha256(loaded_code.encode("utf-8")).hexdigest()
            if actual_hash != content_hash:
                logger.warning(
                    "Content hash mismatch for %s: expected=%s... actual=%s...",
                    file_path,
                    content_hash[:12],
                    actual_hash[:12],
                )

        if service_func is None:
            return _service_result(
                service_cfg, start_time, ExecutionStatus.FAILED.value,
                result=None,
                error_message=load_error or "Service function not found",
                error_type="WorkflowLoadError" if load_error else "ExecutableNotFound",
            )

        request = ExecutionRequest(
            execution_id=attempt_id,
            caller=caller,
            organization=org,
            func=service_func,
            name=context_data.get("name"),
            tags=["service"],
            timeout_seconds=0,
            parameters={},
            is_platform_admin=False,
            broadcaster=None,
            solution_id=context_data.get("solution_id"),
            artifact_workspace_id=context_data.get("artifact_workspace_id"),
            service=ServiceRunConfig(
                service_id=str(service_cfg.get("service_id")),
                attempt_id=attempt_id,
                lease_token=str(service_cfg.get("lease_token", "")),
                revision=service_cfg.get("revision"),
                graceful_shutdown_seconds=int(
                    service_cfg.get("graceful_shutdown_seconds", 30)
                ),
                startup_grace_seconds=int(
                    service_cfg.get("startup_grace_seconds", 60)
                ),
                token=service_cfg.get("token", ""),
                token_expires_at=service_cfg.get("token_expires_at", ""),
            ),
        )

        exec_result = await execute(request)
        return _service_result(
            service_cfg, start_time, exec_result.status.value,
            result=exec_result.result,
            error_message=exec_result.error_message,
            error_type=exec_result.error_type,
        )
    except Exception as e:
        logger.exception("Service execution failed: %s", e)
        e.__traceback__ = None
        return _service_result(
            service_cfg, start_time, ExecutionStatus.FAILED.value,
            result=None,
            error_message=str(e),
            error_type=type(e).__name__,
        )
    finally:
        clear_solution_context()


def _service_result(
    service_cfg: dict[str, Any],
    start_time: Any,
    status: str,
    result: Any,
    error_message: str | None,
    error_type: str | None,
) -> dict[str, Any]:
    """Internal result shape (mirrors run_execution) plus service identity.

    ``_execute_async`` maps this onto the transport envelope and passes the
    ``service`` block through so the pool routes it to attempt completion.
    """
    from datetime import datetime, timezone

    return {
        "status": status,
        "result": result,
        "duration_ms": int(
            (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        ),
        "logs": [],
        "variables": None,
        "error_message": error_message,
        "error_type": error_type,
        "service": {
            "service_id": str(service_cfg.get("service_id")),
            "attempt_id": str(service_cfg.get("attempt_id")),
            "lease_token": str(service_cfg.get("lease_token", "")),
        },
    }
