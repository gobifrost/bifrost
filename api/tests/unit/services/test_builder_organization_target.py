"""Organization-target Builder projects should persist and load like normal private work."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.solution_builder import SolutionBuilderProject
from src.models.orm.solutions import Solution
from src.services.builder.private_solutions import (
    create_private_solution,
    list_private_solutions,
    load_accessible_private_solution,
)
from src.services.builder.turns import BuilderTurnService
from src.services.solutions.access import SolutionAction


@pytest.mark.asyncio
async def test_create_private_solution_persists_organization_target(
    db_session,
    seed_user,
    monkeypatch,
) -> None:
    org = Organization(
        id=uuid4(),
        name=f"Org-{uuid4().hex[:8]}",
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.flush()

    async def fake_create_project(
        self,
        solution_id,
        *,
        slug,
        name,
        conversation_id,
        user_id,
        target_kind="solution",
    ):
        db_session.add(
            SolutionBuilderProject(
                solution_id=solution_id,
                target_kind=target_kind,
            )
        )
        await db_session.flush()
        return SimpleNamespace()

    monkeypatch.setattr(BuilderTurnService, "create_project", fake_create_project)

    solution, project = await create_private_solution(
        db_session,
        slug="alpha",
        name="Alpha",
        owner_user_id=seed_user.id,
        organization_id=org.id,
        target_kind="organization",
    )

    assert project.target_kind == "organization"
    assert solution.organization_id == org.id

    loaded = await load_accessible_private_solution(
        db_session,
        solution_id=solution.id,
        action=SolutionAction.VIEW,
        actor_user_id=seed_user.id,
        is_platform_admin=False,
        is_external=False,
    )
    assert loaded is not None
    assert loaded[1].target_kind == "organization"

    page = await list_private_solutions(
        db_session,
        actor_user_id=seed_user.id,
        is_external=False,
        view="mine",
    )
    assert page.total == 1
    assert page.records[0].project.target_kind == "organization"


@pytest.mark.asyncio
async def test_builder_turn_service_create_project_persists_target_kind(
    db_session,
    seed_user,
) -> None:
    org = Organization(
        id=uuid4(),
        name=f"Org-{uuid4().hex[:8]}",
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.flush()
    solution = Solution(
        id=uuid4(),
        slug=f"solution-{uuid4().hex[:8]}",
        name="Solution",
        organization_id=org.id,
        owner_user_id=seed_user.id,
        visibility="private",
        global_repo_access=False,
        status="active",
    )
    db_session.add(solution)
    await db_session.flush()

    revision = await BuilderTurnService(db_session).create_project(
        solution.id,
        slug=solution.slug,
        name=solution.name,
        conversation_id=None,
        user_id=seed_user.id,
        target_kind="organization",
    )

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.target_kind == "organization"
    assert revision.solution_id == solution.id
