"""FastAPI database dependencies (API role only).

These Annotated aliases pull in fastapi via Depends. They live in their
own module — NOT in src.core.database — so the worker and scheduler
closures (which import src.core.database for sessions) never pay for
fastapi at import time. tests/unit/test_import_hygiene.py enforces this.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db, get_optional_db, get_session_factory


async def get_read_snapshot_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-local session with a repeatable-read snapshot.

    Use for read-only routes that assemble one logical report from multiple
    SELECT statements. The isolation level is set before the first statement on
    this independent session, so concurrent commits cannot make the response
    internally inconsistent.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db)]

# Type alias for optional database injection
OptionalDbSession = Annotated[AsyncSession | None, Depends(get_optional_db)]

# Type alias for read-only report routes that need one consistent snapshot.
ReadSnapshotDbSession = Annotated[AsyncSession, Depends(get_read_snapshot_db)]
