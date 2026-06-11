"""FastAPI database dependencies (API role only).

These Annotated aliases pull in fastapi via Depends. They live in their
own module — NOT in src.core.database — so the worker and scheduler
closures (which import src.core.database for sessions) never pay for
fastapi at import time. tests/unit/test_import_hygiene.py enforces this.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db, get_optional_db

# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db)]

# Type alias for optional database injection
OptionalDbSession = Annotated[AsyncSession | None, Depends(get_optional_db)]
