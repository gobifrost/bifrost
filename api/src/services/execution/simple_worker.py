"""
Execution helpers for forked worker processes.

This module provides the helpers that forked children (spawned by
TemplateProcess via os.fork) use to run an execution:

- install_requirements(): called by a temporary setup helper to pip-install
  user requirements before the template starts. All forked children inherit
  the resulting filesystem.
- _execute_sync() / _execute_async(): run a single execution using context
  assembled by the parent consumer and delivered over a private pipe.
- _get_process_rss() / _get_pss_bytes() / _capture_resource_metrics():
  per-process memory/resource reporting used by the pool for recycling
  bloated children.

Execution callers live in template_process.py. Requirements installation is
invoked by requirements_setup_helper.py before any template starts. There is
no multiprocessing.spawn code path — forked children are created by
template_process.fork() and communicate via pipe-backed send/recv queues.
"""

from __future__ import annotations

import asyncio
import logging
import os
import resource
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from src.services.execution.requirements_setup_result import FailedPackage, RequirementsInstallResult

logger = logging.getLogger(__name__)


def _parse_requirement_lines(content: str) -> list[str]:
    """Return real package specs (non-comment, non-blank, non-option lines)."""
    packages, _options = _parse_requirements(content)
    return packages


def _parse_requirements(content: str) -> tuple[list[str], list[str]]:
    """Split requirements file content into package specs and pip option tokens.

    Lines starting with ``-`` (e.g. ``-i``, ``--index-url``,
    ``--extra-index-url``, ``-r other.txt``) are pip options, not packages.
    Their whitespace-split tokens are collected so the per-package fallback can
    reapply the same index/source configuration to each individual install.
    Comment and blank lines are ignored.
    """
    packages: list[str] = []
    options: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            options.extend(line.split())
            continue
        packages.append(line)
    return packages, options


def _pip_install(args: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", *args, "--quiet"],
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout
    )


def install_requirements() -> RequirementsInstallResult:
    """
    Install packages from requirements.txt resiliently.

    Called once at pool startup (and on recycle_all) to ensure user-installed
    packages persist across container restarts. Reads requirements via
    get_requirements_sync() (Redis → S3 fallback).

    Strategy: attempt a single batch ``pip install -r`` for speed. If the batch
    fails (e.g. one package can't build), fall back to installing each
    requirement individually so a single bad package no longer strips the whole
    runtime. Returns a structured result; the async caller surfaces failures.

    This function never raises — failures are captured in the returned result.
    """
    import tempfile

    from src.core.requirements_cache import get_requirements_sync

    result = RequirementsInstallResult()

    content = get_requirements_sync()
    if not content:
        logger.info("[pool] No requirements.txt found")
        return result

    packages, options = _parse_requirements(content)
    result.attempted = list(packages)
    if not packages:
        return result

    # Fast path: one batch install.
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            temp_path = f.name

        logger.info(f"[pool] Installing {len(packages)} packages from requirements.txt")
        batch = _pip_install(["-r", temp_path])
        if batch.returncode == 0:
            logger.info(f"[pool] Installed {len(packages)} packages from requirements.txt")
            result.installed = list(packages)
            return result

        logger.warning(
            "[pool] Batch pip install failed; falling back to per-package install. "
            f"pip output: {batch.stderr or batch.stdout}"
        )
    except subprocess.TimeoutExpired:
        logger.warning("[pool] Batch pip install timed out; trying per-package install")
    except Exception as e:  # noqa: BLE001 - batch failure must not abort the fallback path
        logger.warning(f"[pool] Batch install error ({e}); trying per-package install")
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError as e:
                logger.debug(f"[pool] could not remove temp requirements file {temp_path}: {e}")

    # Fallback: install each requirement on its own so one bad package only
    # fails itself.
    for pkg in packages:
        try:
            single = _pip_install([*options, pkg])
            if single.returncode == 0:
                result.installed.append(pkg)
            else:
                err = (single.stderr or single.stdout or "").strip()
                result.failed.append(FailedPackage(package=pkg, error=err[:2000]))
                logger.warning(f"[pool] Failed to install '{pkg}': {err[:500]}")
        except subprocess.TimeoutExpired:
            result.failed.append(FailedPackage(package=pkg, error="pip install timed out (300s)"))
            logger.warning(f"[pool] Timed out installing '{pkg}'")
        except Exception as e:  # noqa: BLE001 - per-package install must never abort the loop
            result.failed.append(FailedPackage(package=pkg, error=str(e)))
            logger.warning(f"[pool] Error installing '{pkg}': {e}")

    return result


def _execute_sync(
    execution_id: str,
    worker_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Synchronous wrapper that runs async execution.

    Creates a new event loop for this execution via asyncio.run().
    This ensures clean isolation between executions.

    Args:
        execution_id: Unique execution identifier
        worker_id: Worker identifier (for logging/tracking)
        context: Execution context assembled by the parent consumer

    Returns:
        Result dict with success, result, error, duration_ms, etc.
    """
    try:
        result = asyncio.run(_execute_async(execution_id, worker_id, context))
        return result
    except Exception as e:
        logger.exception(f"Execution {execution_id} failed: {e}")
        return {
            "execution_id": execution_id,
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "duration_ms": 0,
            "worker_id": worker_id,
        }


async def _execute_async(
    execution_id: str,
    worker_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a workflow from parent-supplied context and return its result.

    This is the core async execution logic. It:
    1. Applies the execution's Solution import scope
    2. Builds an ExecutionRequest
    3. Calls the existing execute() engine
    4. Formats and returns the result

    Args:
        execution_id: Unique execution identifier
        worker_id: Worker identifier (for logging/tracking)
        context: Execution context assembled by the parent consumer

    Returns:
        Result dict with execution outcome
    """
    start_time = datetime.now(timezone.utc)

    # Baseline PSS is captured before execution so both success and engine
    # failure paths can report the memory growth attributable to this
    # execution. Failed executions are often the highest-memory samples, so
    # they must not go unrecorded.
    baseline_pss = _get_pss_bytes()

    # Run the execution using the shared core, which owns Solution context and
    # workspace-module freshness.
    try:
        from src.services.execution.worker import run_execution

        result = await run_execution(execution_id, context)

        # Calculate duration
        duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

        # Determine success from result
        status = result.get("status", "Failed")
        success = status in ("Success", "CompletedWithErrors")

        # Capture resource metrics (use worker's metrics if available, else local)
        metrics = result.get("metrics") or _capture_resource_metrics()

        # Overwrite peak_memory_bytes with PSS delta — the memory uniquely
        # attributable to this execution, excluding shared parent pages.
        end_pss = _get_pss_bytes()
        if baseline_pss > 0 and end_pss > 0:
            metrics["peak_memory_bytes"] = max(0, end_pss - baseline_pss)

        return {
            "execution_id": execution_id,
            "success": success,
            "status": status,
            "result": result.get("result"),
            "error": result.get("error_message"),
            "error_type": result.get("error_type"),
            "duration_ms": result.get("duration_ms", duration_ms),
            "logs": result.get("logs", []),
            "variables": result.get("variables"),
            "integration_calls": result.get("integration_calls", []),
            "roi": result.get("roi"),
            "metrics": metrics,
            "cached": result.get("cached", False),
            "cache_expires_at": result.get("cache_expires_at"),
            "execution_context": result.get("execution_context"),
            "worker_id": worker_id,
        }

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        logger.exception(f"Execution {execution_id} failed in engine: {e}")
        return {
            "execution_id": execution_id,
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "duration_ms": duration_ms,
            "worker_id": worker_id,
            # A failure snapshot, not a peak: PSS growth from before the
            # execution started to the failure point. Absent (None) values
            # mean "unknown", never zero usage.
            "metrics": _capture_failure_metrics(baseline_pss),
        }


def _get_pss_bytes() -> int:
    """Get current PSS (Proportional Set Size) in bytes.

    PSS divides shared pages proportionally among all processes sharing them
    and counts private pages fully — giving the true unique memory footprint.
    Reads from /proc/self/smaps_rollup on Linux/Docker.
    Falls back to 0 if unavailable.
    """
    try:
        with open("/proc/self/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1]) * 1024  # kB to bytes
    except (OSError, ValueError) as e:
        # /proc not available (macOS) or unexpected line format — caller treats 0 as unknown
        logger.debug(f"could not read smaps_rollup PSS: {e}")
    return 0


def _get_process_rss() -> int:
    """Get current process RSS in bytes (not peak).

    Reads VmRSS from /proc/self/status on Linux/Docker.
    Falls back to 0 if unavailable (e.g., macOS dev).
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024  # kB to bytes
    except (OSError, ValueError) as e:
        # /proc not available (macOS) or unexpected line format — caller treats 0 as unknown
        logger.debug(f"could not read /proc/self/status VmRSS: {e}")
    return 0


def _capture_failure_metrics(baseline_pss: int) -> dict[str, Any]:
    """Capture a best-effort resource snapshot for a failed execution.

    Returns PSS growth since ``baseline_pss`` when both readings are
    available, plus cumulative CPU time. Uses ``None`` (not ``0``) for
    unknown memory so downstream aggregation can distinguish "no sample"
    from "no usage".
    """
    usage = resource.getrusage(resource.RUSAGE_SELF)
    end_pss = _get_pss_bytes()
    if baseline_pss > 0 and end_pss > 0:
        peak_memory_bytes: int | None = max(0, end_pss - baseline_pss)
    else:
        peak_memory_bytes = None
    return {
        "peak_memory_bytes": peak_memory_bytes,
        "cpu_user_seconds": round(usage.ru_utime, 4),
        "cpu_system_seconds": round(usage.ru_stime, 4),
        "cpu_total_seconds": round(usage.ru_utime + usage.ru_stime, 4),
    }


def _capture_resource_metrics() -> dict[str, Any]:
    """
    Capture resource usage for diagnostics.

    Returns:
        Dict with peak memory, CPU time, etc.
    """
    usage = resource.getrusage(resource.RUSAGE_SELF)

    # ru_maxrss is in KB on Linux, bytes on macOS
    if sys.platform == 'darwin':
        peak_memory_bytes = usage.ru_maxrss  # Already in bytes on macOS
    else:
        peak_memory_bytes = usage.ru_maxrss * 1024  # KB to bytes on Linux

    return {
        "peak_memory_bytes": peak_memory_bytes,
        "cpu_user_seconds": round(usage.ru_utime, 4),
        "cpu_system_seconds": round(usage.ru_stime, 4),
        "cpu_total_seconds": round(usage.ru_utime + usage.ru_stime, 4),
    }
