"""Mirror canonical Solution deploy state onto Builder turn history.

The Solution deploy remains a scheduler-owned ``PlatformJob``. Builder adds
only an attributable link from that shared operation back to the immutable
source revision and turn that requested it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob


class BuilderDeployLinkInvalid(ValueError):
    """A deploy claimed to represent a Builder turn but its link is invalid."""


@dataclass(frozen=True)
class BuilderDeployLink:
    solution_id: UUID
    turn_id: UUID
    revision_id: UUID


def parse_builder_deploy_link(
    options: dict[str, Any],
    install_id: UUID | None,
) -> BuilderDeployLink | None:
    """Parse the internal Builder linkage carried by an encrypted deploy job."""
    raw_turn_id = options.get("builder_turn_id")
    raw_revision_id = options.get("source_revision_id")
    if raw_turn_id is None and raw_revision_id is None:
        return None
    if install_id is None or raw_turn_id is None or raw_revision_id is None:
        raise BuilderDeployLinkInvalid("Builder deploy linkage is incomplete")
    try:
        return BuilderDeployLink(
            solution_id=install_id,
            turn_id=UUID(str(raw_turn_id)),
            revision_id=UUID(str(raw_revision_id)),
        )
    except (TypeError, ValueError) as exc:
        raise BuilderDeployLinkInvalid("Builder deploy linkage is malformed") from exc


async def sync_builder_deploy_state(
    db: AsyncSession,
    link: BuilderDeployLink,
    *,
    deploy_job: SolutionDeployJob | None = None,
    running: bool = False,
) -> None:
    """Apply one validated shared-deploy transition to Builder projections."""
    turn = await db.get(SolutionBuilderTurn, link.turn_id)
    project = await db.get(SolutionBuilderProject, link.solution_id)
    revision = await db.get(SolutionSourceRevision, link.revision_id)
    if turn is None or project is None or revision is None:
        raise BuilderDeployLinkInvalid("Builder deploy linkage no longer exists")
    session = await db.get(SolutionBuilderSession, turn.session_id)
    if (
        session is None
        or session.solution_id != link.solution_id
        or revision.solution_id != link.solution_id
        or turn.output_revision_id != link.revision_id
    ):
        raise BuilderDeployLinkInvalid("Builder deploy linkage crosses Solution boundaries")

    if running:
        turn.status = "running"
        turn.error = None
        turn.completed_at = None
        return

    if deploy_job is None or deploy_job.status not in {"succeeded", "failed"}:
        raise BuilderDeployLinkInvalid("Builder deploy is not terminal")
    turn.completed_at = datetime.now(timezone.utc)
    if deploy_job.status == "succeeded":
        turn.status = "succeeded"
        turn.error = None
        raw_build_ids = (deploy_job.result or {}).get("build_job_ids") or []
        try:
            turn.build_job_id = UUID(str(raw_build_ids[0])) if raw_build_ids else None
        except (TypeError, ValueError) as exc:
            raise BuilderDeployLinkInvalid(
                "Builder deploy returned a malformed build identifier"
            ) from exc
        project.deployed_revision_id = link.revision_id
    else:
        turn.status = "failed"
        turn.error = deploy_job.error or "Deploy failed"


__all__ = [
    "BuilderDeployLink",
    "BuilderDeployLinkInvalid",
    "parse_builder_deploy_link",
    "sync_builder_deploy_state",
]
