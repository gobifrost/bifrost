"""Caller-mode App slug lookups resolve across the caller's reach.

The name cascade only queried the home org and global, so a caller whose reach
covers another org (a Platform Admin) could list a client org's App but not
open it by slug. These tests pin the cross-org resolution and its preference
order; admission itself is ``_can_access_entity`` (covered elsewhere).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository


def _app(slug: str, org_id: UUID | None, solution_id: UUID | None = None) -> Application:
    return Application(
        id=uuid4(),
        name=slug,
        slug=slug,
        organization_id=org_id,
        access_level="authenticated",
        repo_path=f"apps/{slug}",
        solution_id=solution_id,
    )


def _repo(rows: list[Application], readable: set[UUID], *, home_org: UUID, selected=None):
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    caller = MagicMock()
    caller.principal.embed = None
    caller.ctx.user_id = uuid4()
    caller.ctx.is_external = False
    kwargs = {} if selected is None else {"selected_organization_id": selected}
    repo = ApplicationRepository(session, home_org, caller=caller, **kwargs)

    async def can_access(entity: Application) -> bool:
        return entity.id in readable

    repo._can_access_entity = can_access  # type: ignore[method-assign]
    return repo, session


@pytest.mark.asyncio
async def test_readable_app_in_another_org_resolves_by_slug():
    home, client = uuid4(), uuid4()
    client_app = _app("crm", client)
    repo, _ = _repo([client_app], {client_app.id}, home_org=home)

    assert await repo.get(slug="crm") is client_app


@pytest.mark.asyncio
async def test_unreadable_app_in_another_org_stays_hidden():
    home, client = uuid4(), uuid4()
    client_app = _app("crm", client)
    repo, _ = _repo([client_app], set(), home_org=home)

    assert await repo.get(slug="crm") is None


@pytest.mark.asyncio
async def test_shared_solution_slug_prefers_selected_then_home_then_global():
    home, selected, other = uuid4(), uuid4(), uuid4()
    rows = [
        _app("desk", other, uuid4()),
        _app("desk", None, uuid4()),
        _app("desk", home, uuid4()),
        _app("desk", selected, uuid4()),
    ]
    by_org = {row.organization_id: row for row in rows}
    all_ids = {row.id for row in rows}

    repo, _ = _repo(rows, all_ids, home_org=home, selected=selected)
    assert await repo.get(slug="desk", include_solution_managed=True) is by_org[selected]

    repo, _ = _repo(rows, all_ids, home_org=home)
    assert await repo.get(slug="desk", include_solution_managed=True) is by_org[home]

    repo, _ = _repo(rows, all_ids - {by_org[home].id}, home_org=home)
    assert await repo.get(slug="desk", include_solution_managed=True) is by_org[None]


@pytest.mark.asyncio
async def test_unreadable_preferred_row_does_not_block_a_readable_one():
    home, client = uuid4(), uuid4()
    home_row, client_row = _app("desk", home, uuid4()), _app("desk", client, uuid4())
    repo, _ = _repo([home_row, client_row], {client_row.id}, home_org=home)

    assert await repo.get(slug="desk", include_solution_managed=True) is client_row


@pytest.mark.asyncio
async def test_solution_managed_rows_excluded_unless_requested():
    home = uuid4()
    repo, session = _repo([], set(), home_org=home)

    await repo.get(slug="desk")
    statement = str(session.execute.await_args.args[0])
    assert "solution_id IS NULL" in statement
    assert "organization_id" not in statement.split("WHERE", 1)[1]

    await repo.get(slug="desk", include_solution_managed=True)
    statement = str(session.execute.await_args.args[0])
    assert "solution_id IS NULL" not in statement
