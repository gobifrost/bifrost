"""Re-home every organization-stamped row owned by a Solution install."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.events import EventSource
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.forms import Form
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow

ORGANIZATION_STAMPED_MODELS = (
    Workflow,
    Application,
    Form,
    Agent,
    CustomClaim,
    Table,
    Config,
    FileMetadata,
    FilePolicy,
    PolicyRule,
    EventSource,
    SolutionExportJob,
)


async def rehome_solution_owned_rows(
    db: AsyncSession,
    *,
    solution_id: UUID,
    organization_id: UUID | None,
) -> None:
    """Stamp the install's target scope onto all directly owned scoped rows.

    Tables such as builder revisions, event subscriptions, file-location
    declarations, and deploy/build jobs carry ``solution_id`` but no
    ``organization_id``; identity is unchanged and they need no update.
    """
    for model in ORGANIZATION_STAMPED_MODELS:
        await db.execute(
            update(model)
            .where(model.solution_id == solution_id)
            .values(organization_id=organization_id)
        )
