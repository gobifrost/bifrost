"""End-to-end coverage for audit retention settings, preview and the archive job."""

import time
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import AuditArchiveSegment

SETTINGS = "/api/maintenance/audit-retention/settings"
RUN = "/api/maintenance/audit-retention/run"
PREVIEW = "/api/maintenance/audit-retention/preview"
DEFAULTS = {"hot_days": 90, "archive_days": 365}


@pytest.fixture
def restore_defaults(e2e_client, platform_admin):
    yield
    restored = e2e_client.put(SETTINGS, json=DEFAULTS, headers=platform_admin.headers)
    assert restored.status_code == 200, restored.text


@pytest_asyncio.fixture
async def archived_2001(db_session: AsyncSession):
    """A cataloged 2001 segment; the preview reads only the catalog."""
    segment = AuditArchiveSegment(
        id=uuid4(),
        organization_id=None,
        day=date(2001, 1, 1),
        object_key=f"_audit/v1/org=global/day=2001-01-01/{uuid4().hex}.jsonl.gz",
        schema_version=1,
        row_count=3,
        byte_size=100,
        sha256="0" * 64,
        first_created_at=datetime(2001, 1, 1, 0, 0, 1, tzinfo=UTC),
        last_created_at=datetime(2001, 1, 1, 12, 0, tzinfo=UTC),
        first_id=uuid4(),
        last_id=uuid4(),
    )
    db_session.add(segment)
    await db_session.commit()
    yield segment.id
    await db_session.execute(delete(AuditArchiveSegment).where(AuditArchiveSegment.id == segment.id))
    await db_session.commit()


@pytest.mark.e2e
class TestAuditRetentionApi:
    def test_settings_default_and_status(self, e2e_client, platform_admin):
        response = e2e_client.get(SETTINGS, headers=platform_admin.headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["settings"] == DEFAULTS
        assert body["info"]["hot_days"] == 90
        assert body["info"]["archived_segments"] >= 0
        assert "last_run" in body

    def test_update_validates_and_is_audited(self, e2e_client, platform_admin, restore_defaults):
        shorter = e2e_client.put(
            SETTINGS, json={"hot_days": 90, "archive_days": 30}, headers=platform_admin.headers
        )
        assert shorter.status_code == 422, shorter.text

        forever = e2e_client.put(
            SETTINGS, json={"hot_days": 90, "archive_days": None}, headers=platform_admin.headers
        )
        assert forever.status_code == 200, forever.text
        assert forever.json()["settings"] == {"hot_days": 90, "archive_days": None}

        events = e2e_client.get(
            "/api/audit?action=settings.audit_retention.update", headers=platform_admin.headers
        )
        assert events.status_code == 200, events.text
        newest = events.json()["entries"][0]
        assert newest["action"] == "settings.audit_retention.update"
        assert newest["resource_type"] == "system_config"
        assert newest["details"]["after"] == {"hot_days": 90, "archive_days": None}
        assert newest["details"]["before"]["hot_days"] == 90

    def test_dry_run_job_reports_plan(self, e2e_client, platform_admin):
        accepted = e2e_client.post(RUN, json={"dry_run": True}, headers=platform_admin.headers)
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        assert accepted.headers["location"] == f"/api/platform-jobs/{body['job_id']}"

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = e2e_client.get(f"/api/platform-jobs/{body['job_id']}", headers=platform_admin.headers)
            assert job.status_code == 200, job.text
            if job.json()["status"] == "succeeded":
                result = job.json()["result"]
                assert result["dry_run"] is True
                assert result["eligible_rows"] >= 0
                break
            assert job.json()["status"] not in {"failed", "cancelled"}, job.text
            time.sleep(0.25)
        else:
            raise AssertionError("Audit archive dry run did not finish within 15 seconds")

        status = e2e_client.get(SETTINGS, headers=platform_admin.headers)
        assert status.json()["last_run"]["id"] == body["job_id"]

    async def test_preview_counts_expiring_segments(self, e2e_client, platform_admin, archived_2001):
        expiring = e2e_client.get(f"{PREVIEW}?archive_days=1", headers=platform_admin.headers)
        assert expiring.status_code == 200, expiring.text
        preview = expiring.json()
        assert preview["expiring_segments"] >= 1
        assert preview["expiring_rows"] >= 3
        assert preview["expiring_from"] <= "2001-01-01"

        forever = e2e_client.get(PREVIEW, headers=platform_admin.headers)
        assert forever.status_code == 200, forever.text
        assert forever.json() == {
            "expiring_segments": 0,
            "expiring_rows": 0,
            "expiring_from": None,
            "expiring_to": None,
        }

    def test_non_admin_is_forbidden(self, e2e_client, org1_user):
        headers = org1_user.headers
        assert e2e_client.get(SETTINGS, headers=headers).status_code == 403
        assert e2e_client.put(SETTINGS, json=DEFAULTS, headers=headers).status_code == 403
        assert e2e_client.post(RUN, json={"dry_run": True}, headers=headers).status_code == 403
        assert e2e_client.get(PREVIEW, headers=headers).status_code == 403
