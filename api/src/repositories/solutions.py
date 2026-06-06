"""
Solution Repository

Data access for Solution *install* entities. A Solution is an identity-style
entity (like Organization): it belongs to a scope but is never resolved by name
with cascade, so it does NOT go through OrgScopedRepository. See
api/src/repositories/README.md.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Solution
from src.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class SolutionRepository(BaseRepository[Solution]):  # type: ignore[type-var]
    """Repository for Solution install entities."""

    model = Solution

    def __init__(self, session: AsyncSession):
        super().__init__(session)
