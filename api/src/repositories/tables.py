"""Table repository."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select

from shared.policies.probe import make_seed_admin_bypass
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType
from src.models.contracts.tables import TableCreate, TableUpdate
from src.models.orm.tables import Table
from src.repositories.org_scoped import OrgScopedRepository

logger = logging.getLogger(__name__)


class TableRepository(OrgScopedRepository[Table]):
    """Repository for table operations."""

    model = Table
    role_table = None

    async def list_tables(
        self,
        filter_type: OrgFilterType = OrgFilterType.ORG_PLUS_GLOBAL,
        include_orphaned: bool = False,
    ) -> list[Table]:
        """List tables with specified filter type.

        Orphaned tables (former-install data; orphaned_at stamped) are hidden by
        default and only surfaced when ``include_orphaned`` is True.
        """
        query = select(self.model)

        if filter_type == OrgFilterType.ALL:
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            query = query.where(self.model.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            query = query.where(self.model.organization_id == self.org_id)
        else:
            query = self._apply_cascade_scope(query)

        if not include_orphaned:
            query = query.where(self.model.orphaned_at.is_(None))

        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Table | None:
        """Get by name with cascade scoping: org-specific > global."""
        return await self.get(name=name)

    async def get_by_name_strict(
        self, name: str, *, repo_namespace_only: bool = False
    ) -> Table | None:
        """Get table by name strictly in current org scope.

        ``repo_namespace_only`` restricts the lookup to the _repo namespace
        (``solution_id IS NULL``). The schema (see migration
        20260606_table_name_solution_scope) intentionally lets a _repo table and
        a solution-managed table coexist with the same name in the same org via
        separate partial unique indexes, so the _repo create-time duplicate
        check must only see the _repo namespace.
        """
        query = select(self.model).where(
            self.model.name == name,
            self.model.organization_id == self.org_id,
        )
        if repo_namespace_only:
            query = query.where(self.model.solution_id.is_(None))
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_table(
        self,
        data: TableCreate,
        created_by: str,
    ) -> Table:
        """Create a new table."""
        # create_table only ever creates _repo tables (solution_id stays NULL),
        # so the duplicate-name guard must only look at the _repo namespace —
        # a solution-managed row of the same name is allowed to coexist.
        existing = await self.get_by_name_strict(data.name, repo_namespace_only=True)
        if existing:
            raise ValueError(f"Table '{data.name}' already exists")

        if data.policies is not None:
            access_json: dict[str, Any] | None = data.policies.model_dump(mode="json")
        else:
            access_json = make_seed_admin_bypass()

        table = Table(
            name=data.name,
            description=data.description,
            schema=data.schema,
            organization_id=self.org_id,
            created_by=created_by,
            access=access_json,
        )
        self.session.add(table)
        await self.session.flush()
        await self.session.refresh(table)

        logger.info(f"Created table '{log_safe(data.name)}' in org {self.org_id}")
        return table

    async def update_table(
        self,
        table_id: UUID,
        data: TableUpdate,
    ) -> Table | None:
        """Update a table by ID."""
        query = select(self.model).where(self.model.id == table_id)
        result = await self.session.execute(query)
        table = result.scalar_one_or_none()
        if not table:
            return None

        if data.name is not None:
            table.name = data.name
        if data.description is not None:
            table.description = data.description
        if data.schema is not None:
            table.schema = data.schema
        if "policies" in data.model_fields_set:
            table.access = (
                data.policies.model_dump(mode="json")
                if data.policies is not None
                else None
            )

        await self.session.flush()
        await self.session.refresh(table)

        logger.info(f"Updated table '{log_safe(table.name)}' (id={log_safe(table_id)})")
        return table

    async def delete_table(self, table_id: UUID) -> bool:
        """Delete a table and all its documents by ID."""
        query = select(self.model).where(self.model.id == table_id)
        result = await self.session.execute(query)
        table = result.scalar_one_or_none()
        if not table:
            return False

        await self.session.delete(table)
        await self.session.flush()

        logger.info(f"Deleted table '{log_safe(table.name)}' (id={log_safe(table_id)})")
        return True
