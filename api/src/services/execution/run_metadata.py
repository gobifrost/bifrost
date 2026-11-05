"""Helpers for agent-emitted metadata on runs.

Provides a thin async helper that lets a tool author (or future SDK surface)
attach a small dict of string-valued metadata to an ``AgentRun`` row. The
caps protect the row from a misbehaving agent dumping arbitrary blobs into
the JSONB column: at most ``MAX_KEYS`` entries, with each key truncated to
``MAX_KEY_LEN`` characters and each value to ``MAX_VALUE_LEN``.

The DB column is named ``metadata`` but the Python attribute is
``run_metadata`` (``DeclarativeBase.metadata`` is reserved by SQLAlchemy);
see :class:`src.models.orm.agent_runs.AgentRun`.
"""
from typing import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.agent_runs import AgentRun
from src.services.ai_behavior_service import AGENT_EXECUTION_MODE_METADATA_KEY


class TooManyMetadataKeys(ValueError):
    """Raised when an agent attempts to set more than ``MAX_KEYS`` metadata entries."""


MAX_KEYS = 16
MAX_KEY_LEN = 64
MAX_VALUE_LEN = 256


async def set_run_metadata(
    run_id: UUID,
    metadata: Mapping[str, str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Replace agent-authored run metadata while retaining platform-owned fields.

    Validates the key-count cap and truncates over-long keys/values. Commits
    the change in its own session so the helper can be called from a tool
    context that does not own the request session. The execution mode is
    selected by the platform before the run begins, so an agent may neither
    replace nor remove it.
    """
    async with session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update(of=AgentRun)
            )
        ).scalar_one()
        persisted_mode = (run.run_metadata or {}).get(
            AGENT_EXECUTION_MODE_METADATA_KEY
        )
        has_persisted_mode = persisted_mode in {"delegated", "automatic"}
        cleaned = {
            key: str(value)[:MAX_VALUE_LEN]
            for raw_key, value in metadata.items()
            if (key := str(raw_key)[:MAX_KEY_LEN]) != AGENT_EXECUTION_MODE_METADATA_KEY
        }
        max_agent_keys = MAX_KEYS - int(has_persisted_mode)
        if len(cleaned) > max_agent_keys:
            raise TooManyMetadataKeys(
                f"Maximum {max_agent_keys} agent metadata keys (got {len(cleaned)})"
            )
        # Python attribute; DB column is ``metadata``.
        if has_persisted_mode:
            cleaned[AGENT_EXECUTION_MODE_METADATA_KEY] = persisted_mode
        run.run_metadata = cleaned
        await db.commit()
