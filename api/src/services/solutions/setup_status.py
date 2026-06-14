"""Compute required-config setup status for a Solution install.

A required config is "unset" when a SolutionConfigSchema row with
required=True exists but no Config row with the same key exists in the
install's org scope (NULL org for global installs).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solutions import SolutionSetupItem, SolutionSetupStatus
from src.models.orm.config import Config
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution


async def compute_setup_status(db: AsyncSession, solution: Solution) -> SolutionSetupStatus:
    decls = (
        await db.execute(
            select(SolutionConfigSchema)
            .where(SolutionConfigSchema.solution_id == solution.id)
            .order_by(SolutionConfigSchema.position)
        )
    ).scalars().all()

    # Mirror the pattern used in the /entities endpoint: match Config rows by
    # key in the install's org scope (NULL org == global install).
    if solution.organization_id is not None:
        set_keys_q = select(Config.key).where(Config.organization_id == solution.organization_id)
    else:
        set_keys_q = select(Config.key).where(Config.organization_id.is_(None))
    set_keys = set((await db.execute(set_keys_q)).scalars().all())

    items = [
        SolutionSetupItem(
            key=d.key,
            type=d.type,
            required=d.required,
            is_set=d.key in set_keys,
            description=d.description,
        )
        for d in decls
    ]
    complete = all(i.is_set for i in items if i.required)
    return SolutionSetupStatus(setup_complete=complete, items=items)
