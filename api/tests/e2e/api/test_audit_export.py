"""
Audit export: archived and current events in one gzip JSONL file.

Every test seeds two events dated 2001 (one per organization), archives them
through the archiver services, and inserts one current event. Exports run as
real ``audit.query`` platform jobs on the scheduler.
"""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.orm import AuditArchiveSegment, AuditLog, Organization, PlatformJob
from src.services.audit_retention.archiver import archive_segment, select_batch
from src.services.audit_retention.export import cleanup_expired_exports, export_key
from src.services.audit_retention.format import build_segments
from src.services.audit_retention.store import AuditArchiveStore
from tests.e2e.fixtures.setup import PROVIDER_ORG_ID, _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

EXPORTS = "/api/audit/exports"
PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000007"
USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"
YEAR_2001 = {"start_date": "2001-01-01T00:00:00Z", "end_date": "2001-12-31T23:59:59Z"}


@dataclass
class Seeded:
    in_reach: Organization
    out_of_reach: Organization
    archived: dict[str, UUID]  # action -> id
    current: UUID
    current_action: str
    keys: list[str]
    actor_email: str
    exports: list[UUID] = field(default_factory=list)


def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


@pytest_asyncio.fixture
async def store() -> AuditArchiveStore:
    return AuditArchiveStore(get_settings())


async def _archive(db: AsyncSession, store: AuditArchiveStore, ids: set[UUID]) -> list[str]:
    """Archive exactly ``ids`` under a throwaway lease, as the audit.archive job would."""
    rows = [r for r in await select_batch(db, cutoff=datetime(2002, 1, 1, tzinfo=UTC)) if r.id in ids]
    await db.commit()
    assert {r.id for r in rows} == ids
    now = datetime.now(UTC)
    lease = PlatformJob(
        id=uuid4(), job_type="audit.archive", payload_version=1, payload={"dry_run": False},
        requested_by_user_id=str(uuid4()), requested_by_email="archiver@example.com",
        requested_by_name="Archiver Test", title="Archive audit events", status="running",
        execution_backend="local", lease_owner="test-export", lease_token=uuid4(),
        heartbeat_at=now, lease_expires_at=now + timedelta(hours=1),
    )
    db.add(lease)
    await db.commit()
    segments = build_segments(rows)
    try:
        for segment in segments:
            await archive_segment(store, segment, job_id=lease.id, lease_token=lease.lease_token)
    finally:
        await db.execute(delete(PlatformJob).where(PlatformJob.id == lease.id))
        await db.commit()
    return [s.key for s in segments]


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession, store: AuditArchiveStore, platform_admin):
    tag = uuid4().hex[:8]
    in_reach = Organization(id=uuid4(), name=f"Export Org In {tag}", created_by="test")
    out_of_reach = Organization(id=uuid4(), name=f"Export Org Out {tag}", created_by="test")
    db_session.add_all([in_reach, out_of_reach])
    await db_session.flush()
    archived = {"access.check": uuid4(), "user.update": uuid4()}
    current_action = f"access.check.export-{tag}"
    current = uuid4()
    db_session.add_all(
        [
            AuditLog(id=archived["access.check"], organization_id=in_reach.id, user_id=platform_admin.user_id,
                     action="access.check", outcome="failure", source="workflow",
                     details={"tag": tag}, created_at=datetime(2001, 3, 1, 9, 0, tzinfo=UTC)),
            AuditLog(id=archived["user.update"], organization_id=out_of_reach.id, user_id=platform_admin.user_id,
                     action="user.update", outcome="success", source="http",
                     details={"tag": tag}, created_at=datetime(2001, 3, 2, 9, 0, tzinfo=UTC)),
            AuditLog(id=current, organization_id=in_reach.id, action=current_action, outcome="success",
                     source="workflow", created_at=datetime.now(UTC)),
        ]
    )
    await db_session.commit()
    db_session.expunge_all()
    keys = await _archive(db_session, store, set(archived.values()))
    world = Seeded(in_reach, out_of_reach, archived, current, current_action, keys, platform_admin.email)

    yield world

    await db_session.rollback()
    await db_session.execute(delete(PlatformJob).where(PlatformJob.id.in_(world.exports)))
    await db_session.execute(delete(AuditLog).where(AuditLog.id.in_([*archived.values(), current])))
    await db_session.execute(delete(AuditArchiveSegment).where(AuditArchiveSegment.object_key.in_(keys)))
    await db_session.execute(delete(Organization).where(Organization.id.in_([in_reach.id, out_of_reach.id])))
    await db_session.commit()
    for key in [*keys, *(export_key(job_id) for job_id in world.exports)]:
        await store.delete(key)


def _assign_operator(e2e_client, platform_admin, user_id: str, organization_id: UUID) -> None:
    _ok(
        e2e_client.put(
            f"/api/users/{user_id}/role-assignments",
            headers=platform_admin.headers,
            json={
                "base_role_id": USER_ROLE_ID,
                "additional": [
                    {
                        "role_id": PLATFORM_OPERATOR_ROLE_ID,
                        "boundaries": [{"kind": "organization", "organization_id": str(organization_id)}],
                    }
                ],
            },
        )
    )


@pytest.fixture
def operator(e2e_client, platform_admin, seeded):
    """A Platform Operator placed in the in-reach organization only."""
    tag = uuid4().hex[:8]
    user = E2EUser(
        email=f"export-operator-{tag}@example.com",
        password="ExportOperator123!",
        name="Export Operator",
        organization_id=PROVIDER_ORG_ID,
    )
    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={"email": user.email, "name": user.name, "organization_id": str(PROVIDER_ORG_ID)},
        ),
        201,
    )
    user = _register_and_authenticate_user(user)
    _assign_operator(e2e_client, platform_admin, created["id"], seeded.in_reach.id)
    yield created["id"], user
    e2e_client.delete(f"/api/users/{created['id']}", headers=platform_admin.headers)


def _export(e2e_client, user: E2EUser, body: dict[str, Any], seeded: Seeded) -> dict[str, Any]:
    """Enqueue an export and wait for it to finish; returns the final job."""
    response = e2e_client.post(EXPORTS, headers=user.headers, json=body)
    accepted = _ok(response, 202)
    seeded.exports.append(UUID(accepted["job_id"]))
    assert response.headers["location"] == f"/api/platform-jobs/{accepted['job_id']}"
    assert accepted["notification_id"] is not None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = _ok(e2e_client.get(f"/api/platform-jobs/{accepted['job_id']}", headers=user.headers))
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.25)
    raise AssertionError(f"Audit export {accepted['job_id']} did not finish within 30 seconds")


def _download(e2e_client, user: E2EUser, job_id: str):
    return e2e_client.get(f"{EXPORTS}/{job_id}/download", headers=user.headers)


def _lines(response) -> list[dict[str, Any]]:
    assert response.status_code == 200, f"{response.request.url}: {response.status_code} {response.text}"
    assert response.headers["content-type"] == "application/gzip"
    return [json.loads(line) for line in gzip.decompress(response.content).decode().split("\n") if line]


@pytest.mark.asyncio
class TestAuditExport:
    async def test_admin_exports_archived_events_with_snapshot_names(
        self, e2e_client, platform_admin, seeded, db_session
    ):
        # A rename after archiving does not reach the archived snapshot.
        await db_session.execute(
            update(Organization).where(Organization.id == seeded.in_reach.id).values(name="Renamed Org")
        )
        await db_session.commit()

        job = _export(e2e_client, platform_admin, YEAR_2001, seeded)

        assert job["status"] == "succeeded", job
        assert (job["result"]["rows"], job["result"]["segments"]) == (2, 2)
        response = _download(e2e_client, platform_admin, job["id"])
        assert 'filename="audit-export-2001-01-01-2001-12-31.jsonl.gz"' in response.headers["content-disposition"]
        lines = _lines(response)
        assert [line["id"] for line in lines] == [str(seeded.archived["access.check"]), str(seeded.archived["user.update"])]
        assert [line["organization_name"] for line in lines] == [seeded.in_reach.name, seeded.out_of_reach.name]
        assert {line["actor_email"] for line in lines} == {seeded.actor_email}
        assert {line["schema"] for line in lines} == {"audit.v1"}

        # The same export, seven days on.
        await db_session.execute(
            update(PlatformJob)
            .where(PlatformJob.id == UUID(job["id"]))
            .values(completed_at=datetime.now(UTC) - timedelta(days=8))
        )
        await db_session.commit()
        expired = _download(e2e_client, platform_admin, job["id"])
        assert expired.status_code == 410, expired.text
        assert expired.json()["detail"] == "Export expired; run it again."

    async def test_export_includes_current_events(self, e2e_client, platform_admin, seeded):
        now = datetime.now(UTC)
        job = _export(
            e2e_client,
            platform_admin,
            {
                "start_date": (now - timedelta(days=1)).isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "action": seeded.current_action,
            },
            seeded,
        )

        assert job["status"] == "succeeded", job
        assert [line["id"] for line in _lines(_download(e2e_client, platform_admin, job["id"]))] == [str(seeded.current)]

    async def test_operator_exports_access_checks_in_reach_only(self, e2e_client, platform_admin, seeded, operator):
        user_id, user = operator
        refused = e2e_client.post(EXPORTS, headers=user.headers, json={**YEAR_2001, "action": "user."})
        assert refused.status_code == 403, refused.text
        elsewhere = e2e_client.post(
            EXPORTS,
            headers=user.headers,
            json={**YEAR_2001, "action": "access.check", "organization_id": str(seeded.out_of_reach.id)},
        )
        assert elsewhere.status_code == 403, elsewhere.text

        job = _export(e2e_client, user, {**YEAR_2001, "action": "access.check"}, seeded)

        assert job["status"] == "succeeded", job
        lines = _lines(_download(e2e_client, user, job["id"]))
        assert [line["id"] for line in lines] == [str(seeded.archived["access.check"])]
        # Only the requester downloads it, and only while their reach is unchanged.
        assert _download(e2e_client, platform_admin, job["id"]).status_code == 404
        _assign_operator(e2e_client, platform_admin, user_id, seeded.out_of_reach.id)
        changed = _download(e2e_client, user, job["id"])
        assert changed.status_code == 403, changed.text
        assert changed.json()["detail"] == "Your access changed since this export was made; run it again."

    async def test_corrupt_segment_fails_the_export(self, e2e_client, platform_admin, seeded, store):
        await store.put(seeded.keys[0], b"not an archive")

        job = _export(e2e_client, platform_admin, YEAR_2001, seeded)

        assert job["status"] == "failed", job
        assert job["error"]["code"] == "archive_corrupt"
        assert seeded.keys[0] in job["error"]["message"]


@pytest.mark.asyncio
async def test_cleanup_removes_expired_and_orphaned_exports(db_session: AsyncSession, store: AuditArchiveStore):
    now = datetime.now(UTC)
    jobs = {
        name: PlatformJob(
            id=uuid4(), job_type="audit.query", payload_version=1, payload={}, requested_by_user_id=str(uuid4()),
            requested_by_email="exporter@example.com", requested_by_name="Export Test",
            title="Export audit events", status="succeeded", completed_at=completed_at,
        )
        for name, completed_at in (("expired", now - timedelta(days=8)), ("fresh", now - timedelta(days=1)))
    }
    db_session.add_all(jobs.values())
    await db_session.commit()
    orphan = uuid4()
    keys = {name: export_key(job.id) for name, job in jobs.items()} | {
        "orphan": export_key(orphan),
        "unnamed": "_audit_exports/not-a-job.jsonl.gz",
    }
    for key in keys.values():
        await store.put(key, b"export")
    try:
        removed = await cleanup_expired_exports(store, older_than=now - timedelta(days=7))

        assert removed >= 3
        await store.get(keys["fresh"])
        for name in ("expired", "orphan", "unnamed"):
            with pytest.raises(FileNotFoundError):
                await store.get(keys[name])
    finally:
        for key in keys.values():
            await store.delete(key)
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id.in_([j.id for j in jobs.values()])))
        await db_session.commit()
