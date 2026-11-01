"""Pure mapping from the shared engine result to its canonical parent envelope.

Physical runtimes supply measured metrics and elapsed time. This module neither
measures resources nor invents zero values for unavailable provider telemetry.
"""
from __future__ import annotations

from typing import Any


def execution_result_envelope(
    execution_id: str, worker_id: str, result: dict[str, Any], *,
    duration_ms: int, metrics: dict[str, Any],
) -> dict[str, Any]:
    """Preserve the mapping historically owned by simple_worker._execute_async."""
    status = result.get("status", "Failed")
    envelope: dict[str, Any] = {
        "execution_id": execution_id,
        "success": status in ("Success", "CompletedWithErrors"),
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
    if isinstance(result.get("service"), dict):
        envelope["service"] = result["service"]
    return envelope
