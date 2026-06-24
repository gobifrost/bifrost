from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.models.orm.solution_file_location import SolutionFileLocation
from tests.e2e.file_policy_helpers import grant_file_policy

pytestmark = pytest.mark.e2e


LOCATION = "finance"


def _create_solution(
    e2e_client,
    headers: dict[str, str],
    *,
    org_id: str,
    global_repo_access: bool,
) -> dict:
    slug = f"file-tier-{uuid.uuid4().hex[:8]}"
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


async def _declare_file_location(db_session, solution_id: str) -> None:
    db_session.add(
        SolutionFileLocation(solution_id=UUID(solution_id), location=LOCATION)
    )
    await db_session.commit()


def _grant_tier_policies(
    e2e_client,
    headers: dict[str, str],
    *,
    org_id: str,
) -> None:
    for scope in (org_id, "global"):
        grant_file_policy(
            e2e_client,
            headers,
            location=LOCATION,
            scope=scope,
            prefix="",
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


def _exists(
    e2e_client,
    headers: dict[str, str],
    *,
    solution_id: str,
    path: str,
) -> bool:
    response = e2e_client.post(
        f"/api/files/exists?solution={solution_id}",
        headers=headers,
        json={"location": LOCATION, "path": path, "mode": "cloud"},
    )
    assert response.status_code == 200, response.text
    return bool(response.json()["exists"])


def _list_files(
    e2e_client,
    headers: dict[str, str],
    *,
    solution_id: str,
    directory: str,
) -> list[str]:
    response = e2e_client.post(
        f"/api/files/list?solution={solution_id}",
        headers=headers,
        json={"location": LOCATION, "directory": directory, "mode": "cloud"},
    )
    assert response.status_code == 200, response.text
    return response.json()["files"]


@pytest.mark.asyncio
async def test_sealed_solution_reads_own_file_but_not_org_or_global_fallback(
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
    await _declare_file_location(db_session, solution_id)
    _grant_tier_policies(e2e_client, headers, org_id=org_id)

    prefix = f"sealed/{uuid.uuid4().hex}"
    own_path = f"{prefix}/own.txt"
    org_path = f"{prefix}/org.txt"
    global_path = f"{prefix}/global.txt"
    _write_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=own_path,
        content="own sealed",
    )
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=org_path,
        content="org fallback",
    )
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=global_path,
        content="global fallback",
    )

    own_read = _read_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=own_path,
    )
    assert own_read.status_code == 200, own_read.text
    assert own_read.json()["content"] == "own sealed"

    org_read = _read_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=org_path,
    )
    assert org_read.status_code == 404, org_read.text

    global_read = _read_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=global_path,
    )
    assert global_read.status_code == 404, global_read.text


@pytest.mark.asyncio
async def test_open_solution_reads_own_then_org_then_global(
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
    await _declare_file_location(db_session, solution_id)
    _grant_tier_policies(e2e_client, headers, org_id=org_id)

    prefix = f"open/{uuid.uuid4().hex}"
    own_path = f"{prefix}/own-only.txt"
    org_path = f"{prefix}/org-only.txt"
    global_path = f"{prefix}/global-only.txt"
    org_wins_path = f"{prefix}/org-wins.txt"
    _write_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=own_path,
        content="own open",
    )
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=org_path,
        content="org open",
    )
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=global_path,
        content="global open",
    )
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=org_wins_path,
        content="org wins",
    )
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=org_wins_path,
        content="global loses",
    )

    for path, expected in (
        (own_path, "own open"),
        (org_path, "org open"),
        (global_path, "global open"),
        (org_wins_path, "org wins"),
    ):
        response = _read_file(
            e2e_client,
            headers,
            solution_id=solution_id,
            path=path,
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"] == expected


@pytest.mark.asyncio
async def test_solution_owned_file_wins_over_org_and_global_same_path(
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
    await _declare_file_location(db_session, solution_id)
    _grant_tier_policies(e2e_client, headers, org_id=org_id)

    path = f"priority/{uuid.uuid4().hex}/same.txt"
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=path,
        content="global loses",
    )
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=path,
        content="org loses",
    )
    _write_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=path,
        content="own wins",
    )

    response = _read_file(
        e2e_client,
        headers,
        solution_id=solution_id,
        path=path,
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "own wins"


@pytest.mark.asyncio
async def test_exists_uses_same_tier_resolution_as_read(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    org_id = org1["id"]
    open_solution = _create_solution(
        e2e_client,
        headers,
        org_id=org_id,
        global_repo_access=True,
    )
    sealed_solution = _create_solution(
        e2e_client,
        headers,
        org_id=org_id,
        global_repo_access=False,
    )
    open_solution_id = open_solution["id"]
    sealed_solution_id = sealed_solution["id"]
    await _declare_file_location(db_session, open_solution_id)
    await _declare_file_location(db_session, sealed_solution_id)
    _grant_tier_policies(e2e_client, headers, org_id=org_id)

    prefix = f"exists/{uuid.uuid4().hex}"
    own_path = f"{prefix}/own.txt"
    org_path = f"{prefix}/org.txt"
    global_path = f"{prefix}/global.txt"
    missing_path = f"{prefix}/missing.txt"
    _write_file(
        e2e_client,
        headers,
        solution_id=open_solution_id,
        path=own_path,
        content="own exists",
    )
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=org_path,
        content="org exists",
    )
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=global_path,
        content="global exists",
    )

    assert _exists(e2e_client, headers, solution_id=open_solution_id, path=own_path)
    assert _exists(e2e_client, headers, solution_id=open_solution_id, path=org_path)
    assert _exists(
        e2e_client,
        headers,
        solution_id=open_solution_id,
        path=global_path,
    )
    assert not _exists(
        e2e_client,
        headers,
        solution_id=open_solution_id,
        path=missing_path,
    )
    assert not _exists(
        e2e_client,
        headers,
        solution_id=sealed_solution_id,
        path=org_path,
    )
    assert not _exists(
        e2e_client,
        headers,
        solution_id=sealed_solution_id,
        path=global_path,
    )


@pytest.mark.asyncio
async def test_list_returns_priority_ordered_union_when_open_and_solution_only_when_sealed(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    org_id = org1["id"]
    open_solution = _create_solution(
        e2e_client,
        headers,
        org_id=org_id,
        global_repo_access=True,
    )
    sealed_solution = _create_solution(
        e2e_client,
        headers,
        org_id=org_id,
        global_repo_access=False,
    )
    open_solution_id = open_solution["id"]
    sealed_solution_id = sealed_solution["id"]
    await _declare_file_location(db_session, open_solution_id)
    await _declare_file_location(db_session, sealed_solution_id)
    _grant_tier_policies(e2e_client, headers, org_id=org_id)

    directory = f"list/{uuid.uuid4().hex}"
    duplicate_path = f"{directory}/duplicate.txt"
    open_own_path = f"{directory}/open-own.txt"
    sealed_own_path = f"{directory}/sealed-own.txt"
    org_path = f"{directory}/org-only.txt"
    global_path = f"{directory}/global-only.txt"

    _write_file(
        e2e_client,
        headers,
        solution_id=open_solution_id,
        path=open_own_path,
        content="open own",
    )
    _write_file(
        e2e_client,
        headers,
        solution_id=open_solution_id,
        path=duplicate_path,
        content="open duplicate wins",
    )
    _write_file(
        e2e_client,
        headers,
        solution_id=sealed_solution_id,
        path=sealed_own_path,
        content="sealed own",
    )
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=org_path,
        content="org listed",
    )
    _write_file(
        e2e_client,
        headers,
        scope=org_id,
        path=duplicate_path,
        content="org duplicate loses",
    )
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=global_path,
        content="global listed",
    )
    _write_file(
        e2e_client,
        headers,
        scope="global",
        path=duplicate_path,
        content="global duplicate loses",
    )

    open_files = _list_files(
        e2e_client,
        headers,
        solution_id=open_solution_id,
        directory=directory,
    )
    assert open_files == [
        duplicate_path,
        open_own_path,
        org_path,
        global_path,
    ]
    assert open_files.count(duplicate_path) == 1

    sealed_files = _list_files(
        e2e_client,
        headers,
        solution_id=sealed_solution_id,
        directory=directory,
    )
    assert sealed_files == [sealed_own_path]
