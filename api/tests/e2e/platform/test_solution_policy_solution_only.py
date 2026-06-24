from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import update

from src.models.orm.file_metadata import FilePolicy
from src.models.orm.solution_file_location import SolutionFileLocation
from tests.e2e.file_policy_helpers import grant_file_policy

pytestmark = pytest.mark.e2e


LOCATION = "finance"


def _policy(actions: list[str]) -> dict:
    return {
        "policies": [
            {
                "name": "task7_policy",
                "actions": actions,
                "when": None,
            }
        ]
    }


def _create_solution(
    e2e_client,
    headers: dict[str, str],
    *,
    org_id: str,
    global_repo_access: bool,
) -> dict:
    slug = f"task7-files-{uuid.uuid4().hex[:8]}"
    response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": slug.upper(),
            "organization_id": org_id,
            "global_repo_access": global_repo_access,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def _declare_location(db_session, solution_id: str) -> None:
    db_session.add(
        SolutionFileLocation(solution_id=UUID(solution_id), location=LOCATION)
    )
    await db_session.commit()


async def _seed_solution_policy(
    db_session,
    *,
    org_id: str,
    solution_id: str,
    prefix: str,
    actions: list[str],
) -> None:
    db_session.add(
        FilePolicy(
            organization_id=UUID(org_id),
            solution_id=UUID(solution_id),
            location=LOCATION,
            path=prefix,
            policies=_policy(actions),
        )
    )
    await db_session.commit()


async def _set_solution_policy_actions(
    db_session,
    *,
    solution_id: str,
    prefix: str,
    actions: list[str],
) -> None:
    await db_session.execute(
        update(FilePolicy)
        .where(
            FilePolicy.solution_id == UUID(solution_id),
            FilePolicy.location == LOCATION,
            FilePolicy.path == prefix,
        )
        .values(policies=_policy(actions))
    )
    await db_session.commit()


def _grant_policy(
    e2e_client,
    headers: dict[str, str],
    *,
    scope: str,
    prefix: str,
) -> None:
    grant_file_policy(
        e2e_client,
        headers,
        location=LOCATION,
        scope=scope,
        prefix=prefix,
        actions=["read", "write", "delete", "list"],
        allow_all=True,
    )


def _write_file(
    e2e_client,
    headers: dict[str, str],
    *,
    path: str,
    content: str,
    solution_id: str | None = None,
    scope: str | None = None,
) -> None:
    url = "/api/files/write"
    if solution_id is not None:
        url = f"{url}?solution={solution_id}"
    body = {
        "location": LOCATION,
        "path": path,
        "content": content,
        "mode": "cloud",
    }
    if scope is not None:
        body["scope"] = scope
    response = e2e_client.post(url, headers=headers, json=body)
    assert response.status_code == 204, response.text


def _read_file(
    e2e_client,
    headers: dict[str, str],
    *,
    solution_id: str,
    path: str,
):
    return e2e_client.post(
        f"/api/files/read?solution={solution_id}",
        headers=headers,
        json={"location": LOCATION, "path": path, "mode": "cloud"},
    )


@pytest.mark.asyncio
async def test_solution_policy_governs_solution_tier_only(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    org_id = org1["id"]
    solution = _create_solution(
        e2e_client,
        headers,
        org_id=org_id,
        global_repo_access=False,
    )
    solution_id = solution["id"]
    await _declare_location(db_session, solution_id)

    prefix = f"task7-own/{uuid.uuid4().hex}/"
    path = f"{prefix}own.txt"
    await _seed_solution_policy(
        db_session,
        org_id=org_id,
        solution_id=solution_id,
        prefix=prefix,
        actions=["read", "write", "delete", "list"],
    )
    _write_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=path,
        content="solution bytes",
    )

    await _set_solution_policy_actions(
        db_session,
        solution_id=solution_id,
        prefix=prefix,
        actions=[],
    )

    response = _read_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=path,
    )

    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_org_fallback_data_uses_org_policy_not_solution_policy(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    org_id = org1["id"]
    solution = _create_solution(
        e2e_client,
        headers,
        org_id=org_id,
        global_repo_access=True,
    )
    solution_id = solution["id"]
    await _declare_location(db_session, solution_id)

    prefix = f"task7-org/{uuid.uuid4().hex}/"
    path = f"{prefix}org.txt"
    await _seed_solution_policy(
        db_session,
        org_id=org_id,
        solution_id=solution_id,
        prefix=prefix,
        actions=[],
    )
    _grant_policy(e2e_client, headers, scope=org_id, prefix=prefix)
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=path,
        content="org fallback bytes",
    )

    response = _read_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=path,
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == "org fallback bytes"


@pytest.mark.asyncio
async def test_global_fallback_data_uses_global_policy_not_solution_policy(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    org_id = org1["id"]
    solution = _create_solution(
        e2e_client,
        headers,
        org_id=org_id,
        global_repo_access=True,
    )
    solution_id = solution["id"]
    await _declare_location(db_session, solution_id)

    prefix = f"task7-global/{uuid.uuid4().hex}/"
    path = f"{prefix}global.txt"
    await _seed_solution_policy(
        db_session,
        org_id=org_id,
        solution_id=solution_id,
        prefix=prefix,
        actions=[],
    )
    _grant_policy(e2e_client, headers, scope="global", prefix=prefix)
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=path,
        content="global fallback bytes",
    )

    response = _read_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=path,
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == "global fallback bytes"
