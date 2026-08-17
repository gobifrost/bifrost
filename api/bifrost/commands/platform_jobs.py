"""CLI commands for reading durable platform jobs.

* ``bifrost platform-jobs get <job-id>`` → ``GET /api/platform-jobs/{job_id}``

Operations that enqueue durable work — application publish, OAuth
connection provisioning, and similar — return a job ID. This command reads
the shared job status contract rather than a per-feature status surface.
Callers see their own jobs; platform administrators see all of them.
"""

from __future__ import annotations

import click

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

platform_jobs_group = entity_group(
    "platform-jobs", "Read durable platform job status."
)


@platform_jobs_group.command("get")
@click.argument("job_id")
@click.pass_context
@pass_resolver
@run_async
async def get_platform_job(
    ctx: click.Context,
    job_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get progress, result, or error for one platform job.

    ``JOB_ID`` is the UUID returned when the durable operation was queued.
    """
    response = await client.get(f"/api/platform-jobs/{job_id}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)
