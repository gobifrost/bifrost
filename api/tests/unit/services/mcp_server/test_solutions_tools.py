from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import solutions


@pytest.mark.asyncio
async def test_list_solutions_forwards_global_boundary(monkeypatch) -> None:
    bridge = AsyncMock(return_value=(200, {"solutions": []}))
    monkeypatch.setattr(solutions, "call_rest", bridge)

    await solutions.bifrost_list_solutions(SimpleNamespace(), scope="global")

    bridge.assert_awaited_once_with(
        SimpleNamespace(),
        "GET",
        "/api/solutions",
        authorization_boundary="platform",
    )


@pytest.mark.asyncio
async def test_create_solution_forwards_global_target_and_boundary(monkeypatch) -> None:
    context = SimpleNamespace()
    bridge = AsyncMock(
        return_value=(
            201,
            {
                "id": str(uuid4()),
                "slug": "expense-tracker",
                "name": "Expense Tracker",
            },
        )
    )
    monkeypatch.setattr(solutions, "call_rest", bridge)

    await solutions.bifrost_create_solution(
        context,
        slug="expense-tracker",
        name="Expense Tracker",
        scope="global",
    )

    bridge.assert_awaited_once_with(
        context,
        "POST",
        "/api/solutions",
        json_body={
            "slug": "expense-tracker",
            "name": "Expense Tracker",
            "global_repo_access": False,
            "git_connected": False,
            "git_repo_url": None,
            "repo_subpath": None,
            "git_ref": None,
            "organization_id": None,
        },
        authorization_boundary="platform",
    )


@pytest.mark.asyncio
async def test_delete_solution_resolves_then_confirms_slug(monkeypatch) -> None:
    context = SimpleNamespace()
    solution_id = str(uuid4())
    bridge = AsyncMock(
        side_effect=[
            (
                200,
                {
                    "solutions": [
                        {
                            "id": solution_id,
                            "slug": "expense-tracker",
                            "name": "Expense Tracker",
                        }
                    ]
                },
            ),
            (200, {"solution_id": solution_id}),
        ]
    )
    monkeypatch.setattr(solutions, "call_rest", bridge)

    await solutions.bifrost_delete_solution(
        context,
        "expense-tracker",
        scope="global",
    )

    assert bridge.await_args_list == [
        call(
            context,
            "GET",
            "/api/solutions",
            authorization_boundary="platform",
        ),
        call(
            context,
            "DELETE",
            f"/api/solutions/{solution_id}",
            params={"confirm": "expense-tracker"},
            authorization_boundary="platform",
        ),
    ]
