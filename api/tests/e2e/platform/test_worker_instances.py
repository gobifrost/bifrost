from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete

from src.models.orm.execution_workers import ExecutionWorker


async def test_worker_inventory_pages_terminal_instances(
    db_session, e2e_client, platform_admin
):
    now = datetime.now(timezone.utc)
    workers = [
        ExecutionWorker(id=uuid4(), form="external", state="ready", created_at=now),
        ExecutionWorker(
            id=uuid4(), form="external", state="lost", created_at=now,
            ended_at=now + timedelta(seconds=1), end_reason="heartbeat_lost",
            capacity_scope="test-scope", capacity_resource_uid="test-resource",
            version="v2.0.1",
        ),
        ExecutionWorker(id=uuid4(), form="static", state="ready", created_at=now),
    ]
    db_session.add_all(workers)
    await db_session.commit()
    ids = [worker.id for worker in workers]
    path = "/api/platform/workers/instances"
    try:
        first = e2e_client.get(
            path, params={"form": "external", "limit": 1}, headers=platform_admin.headers
        )
        assert first.status_code == 200, first.text
        second = e2e_client.get(
            path,
            params={"form": "external", "limit": 1, **first.json()["next_cursor"]},
            headers=platform_admin.headers,
        )
        assert second.status_code == 200, second.text
        items = first.json()["items"] + second.json()["items"]
        assert [item["id"] for item in items] == [
            str(worker_id) for worker_id in sorted(ids[:2], reverse=True)
        ]
        assert next(item for item in items if item["state"] == "lost") == {
            "id": str(ids[1]),
            "form": "external",
            "state": "lost",
            "capacity_scope": "test-scope",
            "capacity_resource_uid": "test-resource",
            "version": "v2.0.1",
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "ended_at": (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "end_reason": "heartbeat_lost",
        }
        assert second.json()["next_cursor"] is None
    finally:
        await db_session.execute(delete(ExecutionWorker).where(ExecutionWorker.id.in_(ids)))
        await db_session.commit()


def test_worker_inventory_requires_global_platform_read(e2e_client, org1_user):
    response = e2e_client.get("/api/platform/workers/instances", headers=org1_user.headers)
    assert response.status_code == 403


def test_worker_inventory_rejects_partial_cursor(e2e_client, platform_admin):
    response = e2e_client.get(
        "/api/platform/workers/instances",
        params={"before_id": str(uuid4())},
        headers=platform_admin.headers,
    )
    assert response.status_code == 422
