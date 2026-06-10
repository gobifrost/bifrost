"""
Worker process entry point for isolated execution.

This module runs in a separate process and:
1. Reads execution context from Redis
2. Runs the workflow/script
3. Writes logs to Redis Stream (already handled by engine)
4. Writes result to Redis (including resource metrics)
5. Exits cleanly (or gets killed on timeout)

The worker imports minimal dependencies to keep memory footprint low.

IMPORTANT: The virtual import hook is installed at module load time
(before any workspace imports can occur) to enable loading Python
modules from Redis cache instead of the filesystem.
"""

from __future__ import annotations

import asyncio
import json
import logging
import resource
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Install virtual import hook IMMEDIATELY at module load time.
# This must happen before any workspace imports (e.g., from shared import ...)
# The hook intercepts imports and loads modules from Redis cache.
from src.services.execution.virtual_import import install_virtual_import_hook

install_virtual_import_hook()

logger = logging.getLogger(__name__)


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


def _setup_signal_handlers():
    """Set up signal handlers for graceful shutdown."""
    def handle_sigterm(signum, frame):
        logger.info("Worker received SIGTERM, initiating graceful shutdown")
        # Raise SystemExit to trigger cleanup
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)


async def _read_execution_context(redis_client, execution_id: str) -> dict[str, Any] | None:
    """Read execution context from Redis."""
    key = f"bifrost:exec:{execution_id}:context"
    data = await redis_client.get(key)
    if data:
        return json.loads(data)
    return None


async def _write_execution_result(redis_client, execution_id: str, result: dict[str, Any]):
    """Write execution result to Redis."""
    key = f"bifrost:exec:{execution_id}:result"
    # Set with 1 hour TTL (parent should read quickly, but safety margin)
    await redis_client.setex(key, 3600, json.dumps(result, default=str))


async def _run_execution(execution_id: str, context_data: dict[str, Any]) -> dict[str, Any]:
    """
    Run the actual execution.

    This is the core execution logic, isolated in the worker process.
    """
    from src.sdk.context import Caller, Organization
    from src.services.execution.engine import ExecutionRequest, execute
    from src.models.enums import ExecutionStatus
    from bifrost.credentials import is_token_expired, save_credentials

    # Engine credentials: prefer the pre-minted token handed down from the
    # consumer via context_data["engine_token"].  This keeps SECRET_KEY out of
    # the child process entirely.
    # Fall back to authenticate_engine() only for callers (e.g. direct engine
    # tests) that bypass the consumer and do not supply a pre-minted token.
    engine_token = context_data.get("engine_token")
    if engine_token:
        import os
        api_url = os.getenv("BIFROST_API_URL", "http://api:8000")
        expires_at = context_data.get("engine_token_expires_at", "")
        save_credentials(
            api_url=api_url,
            access_token=engine_token,
            refresh_token=engine_token,  # Not used but required by schema
            expires_at=expires_at,
        )
    elif is_token_expired(buffer_seconds=3600):
        from src.core.security import authenticate_engine
        authenticate_engine()

    start_time = datetime.now(timezone.utc)

    # Capture starting resource usage
    start_rss, start_utime, start_stime = _get_resource_usage()

    try:
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
            cache_ttl_seconds=context_data.get("cache_ttl_seconds", 300),
            parameters=context_data.get("parameters", {}),
            startup=context_data.get("startup"),  # Launch workflow results
            roi=context_data.get("roi"),  # ROI initialization
            transient=context_data.get("transient", False),
            no_cache=context_data.get("no_cache", False),
            is_platform_admin=context_data.get("is_platform_admin", False),
            broadcaster=None,  # Logs go to Redis Stream directly
            event=event_ctx,
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


async def worker_main(execution_id: str):
    """
    Main entry point for worker process.

    Called by the pool manager when spawning a new worker.
    """
    import redis.asyncio as redis
    from src.config import get_settings

    settings = get_settings()

    # Set up signal handlers
    _setup_signal_handlers()

    logger.info(f"Worker starting for execution: {execution_id}")

    # Note: No workspace directory needed - modules are loaded from Redis via virtual imports
    # The virtual import hook is installed below before any workspace imports

    # Connect to Redis
    redis_client = redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=5.0,
    )

    try:
        # Read context from Redis
        context_data = await _read_execution_context(redis_client, execution_id)
        if not context_data:
            logger.error(f"No context found for execution: {execution_id}")
            await _write_execution_result(redis_client, execution_id, {
                "status": "Failed",
                "error_message": "Execution context not found in Redis",
                "error_type": "ContextNotFound",
                "duration_ms": 0,
                "metrics": None,
            })
            return

        # Run the execution
        result = await _run_execution(execution_id, context_data)

        # Write result to Redis
        await _write_execution_result(redis_client, execution_id, result)

        # Log metrics
        metrics = result.get("metrics")
        if metrics:
            logger.info(
                f"Worker completed execution: {execution_id}, "
                f"status: {result.get('status')}, "
                f"memory: {metrics['peak_memory_bytes'] / 1024 / 1024:.1f}MB, "
                f"cpu: {metrics['cpu_total_seconds']:.3f}s"
            )
        else:
            logger.info(f"Worker completed execution: {execution_id}, status: {result.get('status')}")

    except Exception as e:
        logger.exception(f"Worker failed for execution {execution_id}: {e}")
        try:
            await _write_execution_result(redis_client, execution_id, {
                "status": "Failed",
                "error_message": str(e),
                "error_type": type(e).__name__,
                "duration_ms": 0,
                "metrics": None,
            })
        except Exception:
            pass  # Best effort
    finally:
        await redis_client.aclose()


def run_in_worker(execution_id: str):
    """
    Synchronous entry point for multiprocessing.

    This is called when the process is spawned.
    """
    # Configure logging for worker process
    logging.basicConfig(
        level=logging.INFO,
        format=f"[Worker:{execution_id[:8]}] %(levelname)s - %(message)s"
    )

    # Run the async worker
    asyncio.run(worker_main(execution_id))
