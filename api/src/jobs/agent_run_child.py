"""Entry for one ``agent.run`` dispatch inside a child forked from the agent template.

The template holds no connections, so the child opens its own database pool,
runs the ordinary consumer once and closes the pool. Errors propagate: the
fork protocol turns them into the child's reported outcome.
"""

from __future__ import annotations

from src.core.database import close_db, init_db
from src.jobs.consumers.agent_run import AgentRunConsumer


async def run_agent_message(message: dict) -> None:
    """Run one claimed agent-run dispatch with a database pool owned by this child."""
    try:
        await init_db()
        await AgentRunConsumer().process_message(message)
    finally:
        await close_db()
