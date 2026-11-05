"""Entry for one ``agent.run`` dispatch inside a child forked from the agent template.

The template holds no connections, so the child opens its own database pool,
runs the ordinary consumer once and closes the pool. Errors propagate: the
fork protocol turns them into the child's reported outcome.

The template does not load the API application, so thin-wrapper system tools
reach REST over the network: the child points the MCP HTTP bridge at the
worker's API URL.
"""

from __future__ import annotations

import os
from uuid import UUID

from src.core.database import close_db, init_db
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.services.mcp_server.tools._http_bridge import _BRIDGE_URL_ENV


async def run_agent_message(message: dict, *, handler: str = "agent.run", worker_id: str | None = None) -> None:
    """Run one agent-family dispatch with a database pool owned by this child."""
    api_url = os.environ.get("BIFROST_API_URL")
    if not api_url:
        raise RuntimeError(
            "BIFROST_API_URL is not set: an agent child calls the API's REST "
            "routes over HTTP and has no in-process application"
        )
    os.environ[_BRIDGE_URL_ENV] = api_url
    try:
        await init_db()
        if handler == "agent.run":
            await AgentRunConsumer().process_message(message)
        elif handler in ("agent.summarize", "agent.summarize_backfill"):
            from src.jobs.summarize_worker import handle_summarize_message

            if worker_id is None:
                raise ValueError("A summary child requires its physical worker identity")
            await handle_summarize_message(message, worker_id=UUID(worker_id))
        else:
            raise ValueError(f"Unknown agent handler: {handler}")
    finally:
        await close_db()
