"""
Sync Operation Dataclasses

Declarative database operations used by the manifest import pipeline.
Each Op describes a single DB mutation and exposes:

  - execute(db): carries out the mutation against an AsyncSession
  - describe(): returns a human-readable string for validation output / dry-run

These ops intentionally avoid PostgreSQL-specific constructs (no ON CONFLICT)
so that they stay testable with any SQLAlchemy-compatible backend.

Usage example::

    ops: list[Upsert | SyncRoles | MergeRoles | Delete | Deactivate] = []
    ops.append(Upsert(model=Workflow, id=wf_id, values={"name": "my_wf"}))
    ops.append(SyncRoles(
        junction_model=WorkflowRole,
        entity_fk="workflow_id",
        entity_id=wf_id,
        role_ids={role_a, role_b},
    ))
    async with db_session() as db:
        for op in ops:
            await op.execute(db)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _has_column(model: type, column_name: str) -> bool:
    """Return True if the ORM model table has a column with the given name."""
    return column_name in model.__table__.columns  # type: ignore[attr-defined]


# =============================================================================
# Op dataclasses
# =============================================================================


@dataclass
class Upsert:
    """Insert or update a single row.

    Attributes:
        model:    SQLAlchemy ORM model class (must have a mapped table).
        id:       Primary-key UUID of the target row.
        values:   Column-value mapping to apply (must not include ``id``).
        match_on: Strategy used to locate an existing row:
                  - ``"id"``         – match on the primary-key column
                  - ``"name"``       – match WHERE name == values["name"]
                  - ``"natural_key"``– alias for ``"name"`` (same behaviour)
    """

    model: type
    id: UUID
    values: dict[str, Any]
    match_on: str = "id"
    action_taken: str | None = field(default=None, init=False)

    async def execute(self, db: AsyncSession) -> None:
        """Apply the upsert to the database.

        Strategy:
        1. Attempt an UPDATE with the appropriate WHERE clause.
        2. If UPDATE touched zero rows, fall back to INSERT.

        ``updated_at`` is automatically set to the current UTC time on every
        UPDATE, provided the model table has that column.
        """
        now = datetime.now(timezone.utc)
        table = self.model.__table__  # type: ignore[attr-defined]

        # Build the UPDATE payload
        update_values: dict[str, Any] = dict(self.values)
        if _has_column(self.model, "updated_at"):
            update_values["updated_at"] = now

        # Determine the WHERE clause for the UPDATE
        if self.match_on == "id":
            where_clause = table.c.id == self.id
        elif self.match_on in ("name", "natural_key"):
            name_val = self.values.get("name")
            if name_val is None:
                raise ValueError(
                    f"Upsert with match_on={self.match_on!r} requires 'name' in values"
                )
            where_clause = table.c.name == name_val
        else:
            raise ValueError(f"Unknown match_on strategy: {self.match_on!r}")

        stmt_update = (
            update(self.model)
            .where(where_clause)
            .values(**update_values)
        )
        result = await db.execute(stmt_update)

        if result.rowcount == 0:  # type: ignore[union-attr]
            # No existing row – INSERT instead
            insert_values: dict[str, Any] = {"id": self.id, **self.values}
            stmt_insert = insert(self.model).values(**insert_values)
            await db.execute(stmt_insert)
            self.action_taken = "inserted"
            logger.debug(
                "Upsert(%s, %s): inserted new row",
                self.model.__tablename__,  # type: ignore[attr-defined]
                self.id,
            )
        else:
            self.action_taken = "updated"
            logger.debug(
                "Upsert(%s, %s): updated existing row",
                self.model.__tablename__,  # type: ignore[attr-defined]
                self.id,
            )

    def describe(self) -> str:
        table_name: str = getattr(self.model, "__tablename__", repr(self.model))
        return (
            f"Upsert {table_name} id={self.id} "
            f"(match_on={self.match_on!r}, fields={sorted(self.values.keys())})"
        )


@dataclass
class SyncRoles:
    """Replace role assignments for a single entity.

    Deletes **all** existing rows in ``junction_model`` where
    ``entity_fk == entity_id``, then inserts one row per UUID in
    ``role_ids``.  This is a full replace – callers should include
    every desired role_id, not just additions.

    Attributes:
        junction_model: ORM model class for the junction table
                        (e.g. ``WorkflowRole``, ``FormRole``).
        entity_fk:      Name of the foreign-key column that references the
                        owning entity (e.g. ``"workflow_id"``).
        entity_id:      UUID value of the owning entity.
        role_ids:       Complete set of role UUIDs that should exist after
                        the sync.  An empty set removes all role rows.
        extra_fields:   Additional column-value pairs to include in every
                        INSERT row (e.g. ``{"assigned_by": "git-sync"}``).
                        Useful for junction tables with NOT NULL columns
                        beyond the two FK columns.
    """

    junction_model: type
    entity_fk: str
    entity_id: UUID
    role_ids: set[UUID] = field(default_factory=set)
    extra_fields: dict[str, Any] = field(default_factory=dict)

    async def execute(self, db: AsyncSession) -> None:
        """Delete existing role rows then insert the desired set."""
        table = self.junction_model.__table__  # type: ignore[attr-defined]

        # Remove all current role assignments for this entity
        stmt_delete = delete(self.junction_model).where(
            table.c[self.entity_fk] == self.entity_id
        )
        await db.execute(stmt_delete)

        # Insert the desired role assignments
        if self.role_ids:
            rows = [
                {self.entity_fk: self.entity_id, "role_id": role_id, **self.extra_fields}
                for role_id in self.role_ids
            ]
            stmt_insert = insert(self.junction_model).values(rows)
            await db.execute(stmt_insert)

        logger.debug(
            "SyncRoles(%s, %s=%s): set %d role(s)",
            self.junction_model.__tablename__,  # type: ignore[attr-defined]
            self.entity_fk,
            self.entity_id,
            len(self.role_ids),
        )

    def describe(self) -> str:
        table_name: str = getattr(
            self.junction_model, "__tablename__", repr(self.junction_model)
        )
        sorted_roles = sorted(str(r) for r in self.role_ids)
        extra = f" extra={sorted(self.extra_fields.keys())}" if self.extra_fields else ""
        return (
            f"SyncRoles {table_name} {self.entity_fk}={self.entity_id} "
            f"-> {len(self.role_ids)} role(s): {sorted_roles}{extra}"
        )


@dataclass
class MergeRoles:
    """Additive role assignments for a single entity.

    Inserts missing ``(entity, role)`` junction rows and never deletes: the
    destination keeps bindings the package doesn't mention. Workspace imports
    use this so a replace merges the package's access grants into (rather
    than full-replacing) the destination's existing assignments.

    Attributes: same as :class:`SyncRoles`.
    """

    junction_model: type
    entity_fk: str
    entity_id: UUID
    role_ids: set[UUID] = field(default_factory=set)
    extra_fields: dict[str, Any] = field(default_factory=dict)

    async def execute(self, db: AsyncSession) -> None:
        """Insert missing role rows, leaving existing ones untouched."""
        if not self.role_ids:
            return
        table = self.junction_model.__table__  # type: ignore[attr-defined]
        existing = set(
            (
                await db.execute(
                    select(table.c[self.entity_fk], table.c["role_id"]).where(
                        table.c[self.entity_fk] == self.entity_id
                    )
                )
            ).all()
        )
        rows = [
            {self.entity_fk: self.entity_id, "role_id": role_id, **self.extra_fields}
            for role_id in self.role_ids
            if (self.entity_id, role_id) not in existing
        ]
        if rows:
            await db.execute(insert(self.junction_model).values(rows))

        logger.debug(
            "MergeRoles(%s, %s=%s): merged %d role(s)",
            self.junction_model.__tablename__,  # type: ignore[attr-defined]
            self.entity_fk,
            self.entity_id,
            len(self.role_ids),
        )

    def describe(self) -> str:
        table_name: str = getattr(
            self.junction_model, "__tablename__", repr(self.junction_model)
        )
        sorted_roles = sorted(str(r) for r in self.role_ids)
        extra = f" extra={sorted(self.extra_fields.keys())}" if self.extra_fields else ""
        return (
            f"MergeRoles {table_name} {self.entity_fk}={self.entity_id} "
            f"-> {len(self.role_ids)} role(s): {sorted_roles}{extra}"
        )


@dataclass
class Delete:
    """Hard-delete a single row by primary key.

    Attributes:
        model: ORM model class whose table will be targeted.
        id:    Primary-key UUID of the row to remove.
    """

    model: type
    id: UUID

    async def execute(self, db: AsyncSession) -> None:
        """Execute DELETE WHERE id == self.id."""
        table = self.model.__table__  # type: ignore[attr-defined]
        stmt = delete(self.model).where(table.c.id == self.id)
        await db.execute(stmt)
        logger.debug(
            "Delete(%s, %s): row removed",
            self.model.__tablename__,  # type: ignore[attr-defined]
            self.id,
        )

    def describe(self) -> str:
        table_name: str = getattr(self.model, "__tablename__", repr(self.model))
        return f"Delete {table_name} id={self.id}"


@dataclass
class Deactivate:
    """Soft-delete a row by setting ``is_active=False``.

    Also updates ``updated_at`` to the current UTC time when the model
    table has that column.

    Attributes:
        model: ORM model class with an ``is_active`` boolean column.
        id:    Primary-key UUID of the row to deactivate.
    """

    model: type
    id: UUID

    async def execute(self, db: AsyncSession) -> None:
        """Execute UPDATE SET is_active=False WHERE id == self.id."""
        now = datetime.now(timezone.utc)
        table = self.model.__table__  # type: ignore[attr-defined]

        update_values: dict[str, Any] = {"is_active": False}
        if _has_column(self.model, "updated_at"):
            update_values["updated_at"] = now

        stmt = (
            update(self.model)
            .where(table.c.id == self.id)
            .values(**update_values)
        )
        await db.execute(stmt)
        logger.debug(
            "Deactivate(%s, %s): is_active set to False",
            self.model.__tablename__,  # type: ignore[attr-defined]
            self.id,
        )

    def describe(self) -> str:
        table_name: str = getattr(self.model, "__tablename__", repr(self.model))
        return f"Deactivate {table_name} id={self.id}"


# =============================================================================
# Type alias for callers
# =============================================================================

#: Union type for all concrete Op types.
SyncOp = Upsert | SyncRoles | Delete | Deactivate
