"""Shared CLI polling helpers for durable PlatformJob operations."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import click
import httpx

DEFAULT_PLATFORM_JOB_TIMEOUT_SECONDS = 20 * 60


async def poll_platform_job(
    client: Any,
    job_id: str,
    *,
    label: str,
    failure_label: str | None = None,
    interval: float = 2.0,
    timeout_seconds: float = DEFAULT_PLATFORM_JOB_TIMEOUT_SECONDS,
    timeout_operation: str | None = None,
    allow_requires_action: bool = False,
) -> dict[str, Any]:
    """Poll ``/api/platform-jobs/{job_id}`` until the job reaches a terminal state.

    The helper deliberately performs short status requests only; callers enqueue
    the durable operation first, then use this to follow progress without
    holding the original HTTP request open.
    """

    started = time.monotonic()
    last_progress: tuple[str | None, int, int | None] | None = None
    operation = timeout_operation or label.lower()
    failed_name = failure_label or label

    while True:
        try:
            response = await client.get(f"/api/platform-jobs/{job_id}")
        except httpx.TimeoutException as exc:
            raise click.ClickException(
                f"Timed out reading {operation} job {job_id}. "
                "The durable operation may still be running; retry the command "
                "to follow the existing job."
            ) from exc

        response.raise_for_status()
        body = response.json()
        status_value = body.get("status")
        if status_value == "succeeded":
            return body
        if status_value in ("failed", "cancelled"):
            error = body.get("error") or {}
            raise click.ClickException(
                f"{failed_name} failed (job {job_id}): "
                f"{error.get('message') or status_value}"
            )
        if status_value == "requires_action":
            if allow_requires_action:
                return body
            result = body.get("result")
            action = (
                result.get("requires_action")
                if isinstance(result, dict)
                else None
            )
            follow_up = f" Required action: {action}." if isinstance(action, str) else " User action is required."
            raise click.ClickException(
                f"{failed_name} requires action (job {job_id}).{follow_up}"
            )

        progress_body = body.get("progress") or {}
        phase = progress_body.get("phase")
        current = int(progress_body.get("current") or 0)
        total = progress_body.get("total")
        progress = (phase, current, total)
        if progress != last_progress:
            count = f" ({current}/{total})" if total is not None else ""
            click.echo(
                f"{label} {phase or status_value or 'in progress'}{count}", err=True
            )
            last_progress = progress

        if time.monotonic() - started >= timeout_seconds:
            raise click.ClickException(
                f"{label} polling timed out after {int(timeout_seconds)}s "
                f"(job {job_id}). The durable operation may still be running; "
                "check its status before retrying."
            )

        await asyncio.sleep(interval)
