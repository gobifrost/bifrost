"""E2E coverage for POST /api/files/signed-urls policy behavior."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any
from uuid import UUID

import pytest


async def _replace_file_policies(db_session, policies: list[dict[str, Any]]) -> None:
    from sqlalchemy import delete

    from src.models.contracts.policies import FilePolicies
    from src.models.orm.file_metadata import FilePolicy
    from src.services.file_policy_service import FilePolicyService

    service = FilePolicyService(db_session)
    await db_session.execute(delete(FilePolicy))
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in policies:
        grouped[(item["scope"], item["location"], item["prefix"])].append(
            {
                key: value
                for key, value in item.items()
                if key not in {"scope", "location", "prefix"}
            }
        )
    for (scope, location, prefix), rules in grouped.items():
        await service.upsert_policy(
            organization_id=UUID(scope),
            location=location,
            path=prefix,
            policies=FilePolicies.model_validate({"policies": rules}),
        )
    await db_session.commit()


def _has_role_when(role_id: str) -> dict[str, Any]:
    return {"call": "has_role", "args": [role_id]}


def _policy(
    name: str,
    *,
    scope: str,
    prefix: str,
    actions: list[str],
    when: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "location": "uploads",
        "scope": scope,
        "prefix": prefix,
        "actions": actions,
        "when": when,
    }


@pytest.fixture
def signed_url_role(e2e_client, platform_admin):
    name = f"signed-url-role-{uuid.uuid4().hex[:8]}"
    create = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": name, "description": "signed url batch e2e role"},
    )
    assert create.status_code == 201, create.text
    role = create.json()
    yield role


def _assign_role(e2e_client, platform_admin, role_id: str, *users) -> None:
    resp = e2e_client.post(
        f"/api/roles/{role_id}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(user.user_id) for user in users]},
    )
    assert resp.status_code == 204, resp.text


def _item(path: str, *, method: str, scope: str) -> dict[str, Any]:
    return {
        "path": path,
        "method": method,
        "location": "uploads",
        "scope": scope,
        "content_type": "text/plain",
    }


@pytest.mark.e2e
def test_batch_signed_urls_default_deny_returns_per_item_errors(
    e2e_client,
    org1_user,
    org1,
):
    root = f"batch-default-deny/{uuid.uuid4().hex}/"
    path = f"{root}blocked.txt"

    resp = e2e_client.post(
        "/api/files/signed-urls",
        headers=org1_user.headers,
        json={"requests": [_item(path, method="GET", scope=org1["id"])]},
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["path"] == path
    assert result["url"] is None
    assert result["error"] == "forbidden"
    assert result["status_code"] == 403


@pytest.mark.e2e
async def test_batch_signed_urls_mixed_allowed_denied_per_path(
    e2e_client,
    platform_admin,
    org1_user,
    org2_user,
    org1,
    signed_url_role,
    db_session,
):
    role_id = signed_url_role["id"]
    _assign_role(e2e_client, platform_admin, role_id, org1_user, org2_user)

    root = f"batch-mixed/{uuid.uuid4().hex}/"
    allowed_read = f"{root}allowed/read.txt"
    denied_read = f"{root}denied/read.txt"
    allowed_put = f"{root}allowed/upload.txt"

    await _replace_file_policies(
        db_session,
        [
            _policy(
                "role-read-write-allowed-prefix",
                scope=org1["id"],
                prefix=f"{root}allowed/",
                actions=["read", "write"],
                when=_has_role_when(role_id),
            )
        ],
    )

    mixed = e2e_client.post(
        "/api/files/signed-urls",
        headers=org1_user.headers,
        json={
            "requests": [
                _item(allowed_read, method="GET", scope=org1["id"]),
                _item(denied_read, method="GET", scope=org1["id"]),
                _item(allowed_put, method="PUT", scope=org1["id"]),
            ]
        },
    )
    assert mixed.status_code == 200
    results = mixed.json()["results"]
    assert [r["path"] for r in results] == [allowed_read, denied_read, allowed_put]

    assert results[0]["status_code"] == 200
    assert results[0]["url"]
    assert results[0]["error"] is None
    assert results[0]["resolved_path"] == f"uploads/{org1['id']}/{allowed_read}"

    assert results[1]["status_code"] == 403
    assert results[1]["url"] is None
    assert results[1]["error"] == "forbidden"

    assert results[2]["status_code"] == 200
    assert results[2]["url"]
    assert results[2]["error"] is None

    cross_org = e2e_client.post(
        "/api/files/signed-urls",
        headers=org2_user.headers,
        json={"requests": [_item(allowed_read, method="GET", scope=org1["id"])]},
    )
    assert cross_org.status_code == 200
    result = cross_org.json()["results"][0]
    assert result["status_code"] == 403
    assert result["url"] is None
    assert result["error"] == "forbidden"


@pytest.mark.e2e
async def test_signed_put_metadata_is_recorded_only_after_upload_complete(
    e2e_client,
    org1_user,
    org1,
    db_session,
):
    from shared.file_paths import resolve_s3_key
    from src.models.orm.file_metadata import FileMetadata
    from src.services.file_storage import FileStorageService
    from sqlalchemy import select

    root = f"signed-complete/{uuid.uuid4().hex}/"
    path = f"{root}uploaded.txt"
    s3_path = resolve_s3_key("uploads", org1["id"], path)

    await _replace_file_policies(
        db_session,
        [
            _policy(
                "allow-upload",
                scope=org1["id"],
                prefix=root,
                actions=["read", "write", "list"],
                when={"eq": [{"user": "user_id"}, str(org1_user.user_id)]},
            )
        ],
    )

    signed = e2e_client.post(
        "/api/files/signed-url",
        headers=org1_user.headers,
        json=_item(path, method="PUT", scope=org1["id"]),
    )
    assert signed.status_code == 200, signed.text

    before = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.organization_id == UUID(org1["id"]),
            FileMetadata.location == "uploads",
            FileMetadata.path == path,
        )
    )
    assert before.scalar_one_or_none() is None

    missing_complete = e2e_client.post(
        "/api/files/complete-upload",
        headers=org1_user.headers,
        json={
            "path": path,
            "location": "uploads",
            "scope": org1["id"],
            "content_type": "text/plain",
            "size_bytes": 7,
        },
    )
    assert missing_complete.status_code == 404

    await FileStorageService(db_session).write_raw_to_s3(s3_path, b"uploaded")

    complete = e2e_client.post(
        "/api/files/complete-upload",
        headers=org1_user.headers,
        json={
            "path": path,
            "location": "uploads",
            "scope": org1["id"],
            "content_type": "text/plain",
            "size_bytes": 8,
        },
    )
    assert complete.status_code == 204, complete.text

    after = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.organization_id == UUID(org1["id"]),
            FileMetadata.location == "uploads",
            FileMetadata.path == path,
        )
    )
    row = after.scalar_one()
    assert row.s3_key == s3_path
    assert row.size_bytes == 8
