"""Read-only build preflight; deployment remains the authoritative validator."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import SolutionAppBuildLookup, SolutionAppBuildMatches
from src.models.orm.applications import Application
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import solution_entity_id


async def lookup_app_builds(
    db: AsyncSession, solution_id: UUID, request: SolutionAppBuildLookup
) -> SolutionAppBuildMatches:
    candidates = {
        solution_entity_id(solution_id, app.app_id): app for app in request.apps
    }
    if not candidates:
        return SolutionAppBuildMatches(matches={})
    rows = (
        await db.execute(
            select(Application.id, Application.active_deployment_id).where(
                Application.solution_id == solution_id,
                Application.id.in_(candidates),
            )
        )
    ).all()
    builder = SolutionAppBuilder()
    matches = {}
    for app_id, deployment_id in rows:
        candidate = candidates[app_id]
        if await builder.can_reuse(app_id, deployment_id, candidate.fingerprint):
            matches[str(candidate.app_id)] = deployment_id
    return SolutionAppBuildMatches(matches=matches)
