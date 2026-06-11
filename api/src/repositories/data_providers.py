"""
Data Provider Repository

Database operations for data provider registry.

NOTE: Data providers are now stored in the workflows table with type='data_provider'.
This repository queries the workflows table with type filter for backward compatibility.
New code should use WorkflowRepository.get_data_providers() instead.
"""

from typing import Sequence

from sqlalchemy import func, select

from src.models import Workflow
from src.repositories.org_scoped import OrgScopedRepository


class DataProviderRepository(OrgScopedRepository[Workflow]):
    """Repository for data provider registry operations.

    NOTE: This repository now queries the workflows table with type='data_provider'
    filter. Data providers were consolidated into the workflows table in migration
    20260103_000000.

    For new code, prefer using WorkflowRepository.get_data_providers() directly.

    Data providers are SDK-only resources (no direct user access), so they don't
    have role-based access control. Callers should use is_superuser=True.
    """

    model = Workflow
    role_table = None  # Explicit: no RBAC - SDK-only resource

    async def get_by_name(self, name: str) -> Workflow | None:
        """Get data provider by name with priority: org-specific > global.

        This uses prioritized lookup to avoid MultipleResultsFound when
        the same name exists in both org scope and global scope.

        The type='data_provider' and is_active=True filters are always applied.
        """
        return await self.get(name=name, type="data_provider", is_active=True)

    async def get_all_active(self) -> Sequence[Workflow]:
        """Get all active data providers with cascade scoping."""
        query = (
            select(Workflow)
            .where(Workflow.type == "data_provider")
            .where(Workflow.is_active.is_(True))
            .order_by(Workflow.name)
        )
        query = self._apply_cascade_scope(query)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def count_active(self) -> int:
        """Count all active data providers with cascade scoping."""
        query = (
            select(func.count(Workflow.id))
            .where(Workflow.type == "data_provider")
            .where(Workflow.is_active.is_(True))
        )

        # Apply cascade scoping manually (count queries can't use
        # _apply_cascade_scope).
        if self.org_id is not None:
            query = query.where(
                (Workflow.organization_id == self.org_id)
                | (Workflow.organization_id.is_(None))
            )
        else:
            query = query.where(Workflow.organization_id.is_(None))

        result = await self.session.execute(query)
        return result.scalar() or 0

    async def search(
        self,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Workflow]:
        """Search data providers with filters and cascade scoping."""
        stmt = (
            select(Workflow)
            .where(Workflow.type == "data_provider")
            .where(Workflow.is_active.is_(True))
        )

        # Apply cascade scoping
        stmt = self._apply_cascade_scope(stmt)

        if query:
            stmt = stmt.where(
                Workflow.name.ilike(f"%{query}%") |
                Workflow.description.ilike(f"%{query}%")
            )

        stmt = stmt.order_by(Workflow.name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()
