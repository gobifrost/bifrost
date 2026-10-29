"""Tests for job status endpoint with preview data."""
from uuid import uuid4

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.users import User
from src.routers.jobs import JobStatusResponse, get_job_status


class TestJobStatusResponse:
    """Test that JobStatusResponse includes preview data."""

    def test_response_includes_preview_field(self):
        """JobStatusResponse should accept a preview dict."""
        response = JobStatusResponse(
            status="success",
            preview={
                "to_pull": [{"path": "workflows/billing.py", "action": "add"}],
                "to_push": [],
                "conflicts": [{
                    "path": "workflows/shared.py",
                    "display_name": "shared",
                    "entity_type": "workflow",
                }],
                "preflight": {"valid": True, "issues": []},
                "is_empty": False,
            },
        )
        assert response.status == "success"
        assert response.preview is not None
        assert len(response.preview["to_pull"]) == 1
        assert len(response.preview["conflicts"]) == 1

    def test_response_preview_defaults_to_none(self):
        """Preview should default to None for non-preview jobs."""
        response = JobStatusResponse(status="pending")
        assert response.preview is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform_status", "legacy_status"),
    [("waiting", "running"), ("requires_action", "failed")],
)
async def test_platform_jobs_use_safe_legacy_statuses(
    db_session,
    platform_status: str,
    legacy_status: str,
) -> None:
    organization = Organization(name="Contoso", created_by="test")
    db_session.add(organization)
    await db_session.flush()
    owner = User(
        id=uuid4(),
        email=f"job-owner-{uuid4().hex}@example.test",
        name="Job Owner",
        organization_id=organization.id,
        base_role_id=USER_ROLE_ID,
    )
    db_session.add(owner)
    await db_session.flush()
    job = PlatformJob(
        job_type="workspace.sync",
        payload={},
        requested_by_user_id=str(owner.id),
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
        title="Workspace sync",
        status=platform_status,
        phase="Confirm deletes" if platform_status == "requires_action" else "Waiting",
        result={"requires_action": "confirm_deletes"},
    )
    db_session.add(job)
    await db_session.flush()

    principal = UserPrincipal(
        user_id=owner.id, email=owner.email, organization_id=organization.id, is_superuser=False
    )
    response = await get_job_status(str(job.id), db_session, user=principal)

    assert response.status == legacy_status
    assert response.message == job.phase
    assert response.data == {"requires_action": "confirm_deletes"}
    if platform_status == "requires_action":
        assert response.error == "Action required: confirm_deletes"
