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

from src.core.database import close_db, init_db
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.services.mcp_server.tools._http_bridge import _BRIDGE_URL_ENV


async def run_agent_message(message: dict) -> None:
    """Run one claimed agent-run dispatch with a database pool owned by this child."""
    api_url = os.environ.get("BIFROST_API_URL")
    if not api_url:
        raise RuntimeError(
            "BIFROST_API_URL is not set: an agent child calls the API's REST "
            "routes over HTTP and has no in-process application"
        )
    os.environ[_BRIDGE_URL_ENV] = api_url
    try:
        await init_db()
        await AgentRunConsumer().process_message(message)
    finally:
        await close_db()
