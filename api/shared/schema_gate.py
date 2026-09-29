"""Startup gate that holds long-running processes until migrations have landed.

Migrations run once, in the init step. Workers and the scheduler start
independently, so during a rolling deploy they can come up against a database
that is still on an older revision. ``wait_for_schema`` blocks until the
database is at or ahead of every Alembic head shipped with this image.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0
MAX_WAIT_SECONDS = 15 * 60.0

_API_DIR = Path(__file__).resolve().parent.parent


class SchemaGateTimeout(RuntimeError):
    """The database did not reach the expected schema revision in time."""


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(_API_DIR / "alembic.ini")))


def expected_heads() -> frozenset[str]:
    """Alembic head revisions shipped with this image."""
    return frozenset(_script_directory().get_heads())


def _known_ancestors(script: ScriptDirectory) -> Callable[[str], frozenset[str] | None]:
    def ancestors(revision: str) -> frozenset[str] | None:
        try:
            return frozenset(
                r.revision for r in script.iterate_revisions(revision, "base")
            )
        except CommandError:
            return None

    return ancestors


async def current_revisions(engine: AsyncEngine) -> frozenset[str]:
    """Revisions recorded in ``alembic_version`` (empty if the table is missing)."""
    async with engine.connect() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        except ProgrammingError:
            return frozenset()
        return frozenset(row[0] for row in result)


def is_at_or_ahead(
    current: Collection[str],
    heads: Collection[str],
    ancestors_of: Callable[[str], frozenset[str] | None],
) -> bool:
    """True if the database has applied every head, or is ahead of this image.

    A current revision this image does not know means a newer version already
    migrated the database; that counts as ahead.
    """
    covered: set[str] = set()
    for revision in current:
        ancestors = ancestors_of(revision)
        if ancestors is None:
            return True
        covered |= ancestors
    return set(heads) <= covered


async def wait_for_schema(
    engine: AsyncEngine,
    *,
    heads: Collection[str] | None = None,
    read_current: Callable[[], Awaitable[Collection[str]]] | None = None,
    ancestors_of: Callable[[str], frozenset[str] | None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    max_wait: float = MAX_WAIT_SECONDS,
) -> None:
    """Return once the database is at or ahead of this image's schema.

    Raises ``SchemaGateTimeout`` after ``max_wait`` seconds so the process exits
    non-zero instead of consuming work behind the schema.
    """
    if heads is None or ancestors_of is None:
        script = _script_directory()
        heads = frozenset(script.get_heads()) if heads is None else heads
        ancestors_of = _known_ancestors(script) if ancestors_of is None else ancestors_of
    started = monotonic()
    while True:
        current = await (read_current() if read_current else current_revisions(engine))
        if is_at_or_ahead(current, heads, ancestors_of):
            return
        waited = monotonic() - started
        if waited >= max_wait:
            raise SchemaGateTimeout(
                f"Database schema still behind after {waited:.0f}s "
                f"(current: {sorted(current) or 'none'}, expected: {sorted(heads)})"
            )
        logger.warning(
            "Database schema is behind this image; waiting for migrations "
            "(current: %s, expected: %s)",
            sorted(current) or "none",
            sorted(heads),
        )
        await sleep(poll_interval)
