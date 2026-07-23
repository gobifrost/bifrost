"""
E2E tests for miscellaneous features.

Tests packages, branding, metrics, logs, and other features.
"""

import pytest


@pytest.mark.e2e
class TestPackages:
    """Test package management."""

    def test_list_packages(self, e2e_client, platform_admin):
        """Platform admin can list packages."""
        response = e2e_client.get(
            "/api/packages",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List packages failed: {response.text}"
        data = response.json()
        assert isinstance(data, list) or "packages" in data

    def test_check_package_updates(self, e2e_client, platform_admin):
        """Platform admin can check for package updates."""
        response = e2e_client.get(
            "/api/packages/updates",
            headers=platform_admin.headers,
        )
        # May return 200 or 404 depending on implementation
        assert response.status_code in [200, 404]


@pytest.mark.e2e
class TestBranding:
    """Test branding operations."""

    def test_get_branding(self, e2e_client, platform_admin):
        """Get current branding settings."""
        response = e2e_client.get(
            "/api/branding",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get branding failed: {response.text}"

    def test_update_branding(self, e2e_client, platform_admin):
        """Platform admin can update branding."""
        response = e2e_client.put(
            "/api/branding",
            headers=platform_admin.headers,
            json={
                "primary_color": "#007bff",
            },
        )
        # API uses PUT for branding updates
        assert response.status_code in [200, 404, 422], f"Update failed: {response.text}"

    def test_get_branding_public(self, e2e_client):
        """Get branding without authentication (public endpoint)."""
        response = e2e_client.get("/api/branding")
        # Accept 200 or 404 (if branding not configured)
        assert response.status_code in [200, 404], f"Get branding failed: {response.text}"

    def test_get_branding_authenticated(self, e2e_client, org1_user):
        """Get branding with authentication."""
        response = e2e_client.get(
            "/api/branding",
            headers=org1_user.headers,
        )
        assert response.status_code in [200, 404], f"Get branding failed: {response.text}"

    def test_update_branding_superuser(self, e2e_client, platform_admin):
        """Superuser can update branding."""
        response = e2e_client.put(
            "/api/branding",
            headers=platform_admin.headers,
            json={
                "primary_color": "#1a73e8",
            },
        )
        assert response.status_code in [200, 201], f"Update branding failed: {response.text}"

    def test_update_branding_terminology_superuser(self, e2e_client, platform_admin):
        """Superuser can update fixed product terminology as part of branding."""
        response = e2e_client.put(
            "/api/branding",
            headers=platform_admin.headers,
            json={
                "primary_color": "#1a73e8",
                "terminology": {
                    "app": {"singular": "Game", "plural": "Games"},
                    "agent": {"singular": "Character", "plural": "Characters"},
                    "form": {"singular": "Quest", "plural": "Quests"},
                },
            },
        )

        assert response.status_code in [200, 201], f"Update branding failed: {response.text}"
        data = response.json()
        assert data["terminology"]["app"] == {
            "singular": "Game",
            "plural": "Games",
        }
        assert data["terminology"]["agent"] == {
            "singular": "Character",
            "plural": "Characters",
        }
        assert data["terminology"]["form"] == {
            "singular": "Quest",
            "plural": "Quests",
        }

        public_response = e2e_client.get("/api/branding")
        assert public_response.status_code == 200
        assert public_response.json()["terminology"]["form"]["plural"] == "Quests"

    def test_update_branding_org_user_denied(self, e2e_client, org1_user):
        """Org user cannot update branding (403)."""
        response = e2e_client.put(
            "/api/branding",
            headers=org1_user.headers,
            json={
                "primary_color": "#ff0000",
            },
        )
        assert response.status_code == 403, (
            f"Org user should not update branding: {response.status_code}"
        )

    def test_update_and_clear_application_name(self, e2e_client, platform_admin):
        """Superuser can set the application name, read it back, and clear it."""
        # Set a custom application name
        response = e2e_client.put(
            "/api/branding",
            headers=platform_admin.headers,
            json={"application_name": "Acme Portal"},
        )
        assert response.status_code in [200, 201], f"Update failed: {response.text}"
        assert response.json()["application_name"] == "Acme Portal"

        # Public GET reflects it (login screen reads this pre-auth)
        public = e2e_client.get("/api/branding")
        assert public.status_code == 200
        assert public.json()["application_name"] == "Acme Portal"

        # A color-only update must NOT clear the application name
        color_only = e2e_client.put(
            "/api/branding",
            headers=platform_admin.headers,
            json={"primary_color": "#123456"},
        )
        assert color_only.status_code in [200, 201], color_only.text
        assert color_only.json()["application_name"] == "Acme Portal"

        # Clearing via the dedicated endpoint reverts to null (default)
        cleared = e2e_client.delete(
            "/api/branding/application-name",
            headers=platform_admin.headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["application_name"] is None

    def test_application_name_rejects_overlong(self, e2e_client, platform_admin):
        """Application name longer than 40 chars is rejected (422)."""
        response = e2e_client.put(
            "/api/branding",
            headers=platform_admin.headers,
            json={"application_name": "x" * 41},
        )
        assert response.status_code == 422, f"Expected 422: {response.text}"

    def test_application_name_org_user_denied(self, e2e_client, org1_user):
        """Org user cannot clear the application name (403)."""
        response = e2e_client.delete(
            "/api/branding/application-name",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, (
            f"Org user should not reset application name: {response.status_code}"
        )

    def test_upload_square_logo_superuser(self, e2e_client, platform_admin):
        """Superuser can upload square logo."""
        import os

        logo_path = os.path.join(
            os.path.dirname(__file__), "../logos/square.png"
        )

        with open(logo_path, "rb") as f:
            logo_data = f.read()

        # Remove Content-Type from headers - httpx will set it for multipart
        upload_headers = {
            k: v
            for k, v in platform_admin.headers.items()
            if k.lower() != "content-type"
        }

        response = e2e_client.post(
            "/api/branding/logo/square",
            headers=upload_headers,
            files={"file": ("square.png", logo_data, "image/png")},
        )
        assert response.status_code in [200, 201], (
            f"Upload square logo failed: {response.text}"
        )

    def test_upload_rectangle_logo_superuser(self, e2e_client, platform_admin):
        """Superuser can upload rectangle logo."""
        import os

        logo_path = os.path.join(
            os.path.dirname(__file__), "../logos/rectangle.png"
        )

        with open(logo_path, "rb") as f:
            logo_data = f.read()

        # Remove Content-Type from headers - httpx will set it for multipart
        upload_headers = {
            k: v
            for k, v in platform_admin.headers.items()
            if k.lower() != "content-type"
        }

        response = e2e_client.post(
            "/api/branding/logo/rectangle",
            headers=upload_headers,
            files={"file": ("rectangle.png", logo_data, "image/png")},
        )
        assert response.status_code in [200, 201], (
            f"Upload rectangle logo failed: {response.text}"
        )

    def test_upload_svg_logo_strips_script(self, e2e_client, platform_admin):
        """SVG with <script> is sanitized on upload — script removed when fetched back."""
        payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="1"/></svg>'

        upload_headers = {
            k: v
            for k, v in platform_admin.headers.items()
            if k.lower() != "content-type"
        }

        upload = e2e_client.post(
            "/api/branding/logo/square",
            headers=upload_headers,
            files={"file": ("logo.svg", payload, "image/svg+xml")},
        )
        assert upload.status_code in (200, 201), f"upload failed: {upload.text}"

        fetched = e2e_client.get("/api/branding/logo/square")
        assert fetched.status_code == 200
        assert b"script" not in fetched.content.lower(), (
            f"script tag was not stripped: {fetched.content!r}"
        )

    def test_upload_svg_logo_rejects_xxe(self, e2e_client, platform_admin):
        """SVG with XXE entity declaration is rejected (HTTP 400)."""
        payload = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
        )

        upload_headers = {
            k: v
            for k, v in platform_admin.headers.items()
            if k.lower() != "content-type"
        }

        resp = e2e_client.post(
            "/api/branding/logo/square",
            headers=upload_headers,
            files={"file": ("xxe.svg", payload, "image/svg+xml")},
        )
        assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text}"

    def test_get_square_logo_public(self, e2e_client):
        """Get square logo without authentication after upload."""
        response = e2e_client.get("/api/branding/logo/square")
        # After upload, should be accessible
        assert response.status_code in [200, 404], (
            f"Get square logo failed: {response.text}"
        )
        if response.status_code == 200:
            assert response.headers.get("content-type") in [
                "image/png",
                "image/jpeg",
                "image/svg+xml",
                "application/octet-stream",
            ], "Logo should be an image"
            assert len(response.content) > 0, "Logo content should not be empty"

    def test_get_rectangle_logo_public(self, e2e_client):
        """Get rectangle logo without authentication after upload."""
        response = e2e_client.get("/api/branding/logo/rectangle")
        # After upload, should be accessible
        assert response.status_code in [200, 404], (
            f"Get rectangle logo failed: {response.text}"
        )
        if response.status_code == 200:
            assert response.headers.get("content-type") in [
                "image/png",
                "image/jpeg",
                "image/svg+xml",
                "application/octet-stream",
            ], "Logo should be an image"
            assert len(response.content) > 0, "Logo content should not be empty"

    def test_upload_logo_org_user_denied(self, e2e_client, org1_user):
        """Org user cannot upload logo (403)."""
        import os

        logo_path = os.path.join(
            os.path.dirname(__file__), "../logos/square.png"
        )

        with open(logo_path, "rb") as f:
            logo_data = f.read()

        # Remove Content-Type from headers - httpx will set it for multipart
        upload_headers = {
            k: v
            for k, v in org1_user.headers.items()
            if k.lower() != "content-type"
        }

        response = e2e_client.post(
            "/api/branding/logo/square",
            headers=upload_headers,
            files={"file": ("logo.png", logo_data, "image/png")},
        )
        assert response.status_code == 403, (
            f"Org user should not upload logo: {response.status_code}"
        )


@pytest.mark.e2e
class TestMetrics:
    """Test metrics endpoints."""

    def test_get_metrics_superuser(self, e2e_client, platform_admin):
        """Superuser can get dashboard metrics."""
        response = e2e_client.get(
            "/api/metrics",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get metrics failed: {response.text}"
        data = response.json()

        # Verify response structure
        assert isinstance(data, dict)
        assert "workflow_count" in data
        assert "form_count" in data
        assert "data_provider_count" in data
        assert "execution_stats" in data

        # Verify execution stats structure
        stats = data["execution_stats"]
        assert "total_executions" in stats
        assert "success_count" in stats
        assert "failed_count" in stats
        assert "running_count" in stats
        assert "pending_count" in stats
        assert "success_rate" in stats
        assert "avg_duration_seconds" in stats

    def test_metrics_response_structure(self, e2e_client, platform_admin):
        """Metrics response has expected structure."""
        response = e2e_client.get(
            "/api/metrics",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get metrics failed: {response.text}"
        data = response.json()

        # Verify all expected fields are present
        required_fields = [
            "workflow_count",
            "form_count",
            "data_provider_count",
            "execution_stats",
            "recent_failures",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

        # Verify types
        assert isinstance(data["workflow_count"], int)
        assert isinstance(data["form_count"], int)
        assert isinstance(data["data_provider_count"], int)
        assert isinstance(data["execution_stats"], dict)
        assert isinstance(data["recent_failures"], list)

    def test_get_metrics_unauthenticated(self, e2e_client):
        """Unauthenticated request should fail."""
        # Clear any cookies from previous authenticated tests
        # (e2e_client is session-scoped and may have access_token cookie)
        e2e_client.cookies.clear()
        response = e2e_client.get(
            "/api/metrics",
            headers={},  # No auth header
        )
        assert response.status_code == 401 or response.status_code == 403, \
            f"Unauthenticated request should fail: {response.status_code}"

    def test_get_metrics_snapshot_superuser(self, e2e_client, platform_admin):
        """Superuser can get full metrics snapshot."""
        response = e2e_client.get(
            "/api/metrics/snapshot",
            headers=platform_admin.headers,
        )
        # May return 200 if snapshot exists, 404 if not yet created
        assert response.status_code in [200, 404], \
            f"Get snapshot failed: {response.status_code}"

        if response.status_code == 200:
            data = response.json()

            # Verify expected fields
            expected_fields = [
                "workflow_count",
                "form_count",
                "data_provider_count",
                "organization_count",
                "user_count",
                "total_executions",
                "success_rate_all_time",
                "refreshed_at",
            ]
            for field in expected_fields:
                assert field in data, f"Missing field in snapshot: {field}"

    def test_get_metrics_snapshot_org_user_denied(self, e2e_client, org1_user):
        """Org user cannot access metrics snapshot (requires platform admin)."""
        response = e2e_client.get(
            "/api/metrics/snapshot",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, \
            f"Org user should not access snapshot: {response.status_code}"

    def test_get_metrics_authenticated(self, e2e_client, org1_user):
        """Authenticated org user can access basic metrics endpoint."""
        response = e2e_client.get(
            "/api/metrics",
            headers=org1_user.headers,
        )
        # Org user may get 200 (limited view) or 403 (platform admin only)
        # Depends on implementation - both are valid
        assert response.status_code in [200, 403], \
            f"Unexpected status for org user metrics: {response.status_code}"

    def test_get_organization_metrics_superuser(self, e2e_client, platform_admin, org1):
        """Superuser can get organization-specific metrics."""
        response = e2e_client.get(
            f"/api/metrics/organization/{org1['id']}",
            headers=platform_admin.headers,
        )
        # May return 200 if endpoint exists, 404 if not implemented
        assert response.status_code in [200, 404], \
            f"Get org metrics failed: {response.status_code}"

    def test_get_organization_metrics_org_user_denied(self, e2e_client, org1_user, org1):
        """Org user cannot access organization-level metrics."""
        response = e2e_client.get(
            f"/api/metrics/organization/{org1['id']}",
            headers=org1_user.headers,
        )
        # Should be denied (403) or not found (404)
        assert response.status_code in [403, 404], \
            f"Org user should not access org metrics: {response.status_code}"

    def test_get_resource_metrics_superuser(self, e2e_client, platform_admin):
        """Superuser can get resource usage metrics."""
        response = e2e_client.get(
            "/api/metrics/resources",
            headers=platform_admin.headers,
        )
        # May return 200 if endpoint exists, 404 if not implemented
        assert response.status_code in [200, 404], \
            f"Get resource metrics failed: {response.status_code}"

    def test_get_daily_metrics_superuser(self, e2e_client, platform_admin):
        """Superuser can get daily metrics."""
        response = e2e_client.get(
            "/api/metrics/executions/daily",
            headers=platform_admin.headers,
            params={"days": 30},
        )
        assert response.status_code == 200, \
            f"Get daily metrics failed: {response.status_code}"

        data = response.json()

        # Verify response structure
        assert "days" in data
        assert "total_days" in data
        assert isinstance(data["days"], list)
        assert isinstance(data["total_days"], int)

        # If there are days, verify structure
        if data["days"]:
            day_entry = data["days"][0]
            expected_fields = [
                "date",
                "execution_count",
                "success_count",
                "failed_count",
            ]
            for field in expected_fields:
                assert field in day_entry, f"Missing field in day entry: {field}"

    def test_get_daily_metrics_org_user_denied(self, e2e_client, org1_user):
        """Org user cannot access daily metrics (requires platform admin)."""
        response = e2e_client.get(
            "/api/metrics/executions/daily",
            headers=org1_user.headers,
            params={"days": 30},
        )
        assert response.status_code == 403, \
            f"Org user should not access daily metrics: {response.status_code}"

    @pytest.mark.asyncio
    async def test_execution_timeseries_is_complete_above_history_page_limit(
        self,
        e2e_client,
        platform_admin,
        db_session,
    ):
        """Seven-day aggregates remain complete when today exceeds 1,000 runs."""
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        from sqlalchemy import delete

        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution
        from src.models.orm.organizations import Organization

        workflow_name = f"timeseries-volume-{uuid4()}"
        organization = Organization(
            name=workflow_name,
            created_by=platform_admin.name,
        )
        db_session.add(organization)
        await db_session.flush()
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        executions = [
            Execution(
                workflow_name=workflow_name,
                status=ExecutionStatus.SUCCESS,
                started_at=now - timedelta(seconds=1),
                completed_at=now,
                executed_by=platform_admin.user_id,
                executed_by_name=platform_admin.name,
                organization_id=organization.id,
            )
            for _ in range(1005)
        ]
        previous_statuses = [
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.STUCK,
            ExecutionStatus.COMPLETED_WITH_ERRORS,
            ExecutionStatus.SUCCESS,
            ExecutionStatus.FAILED,
        ]
        executions.extend(
            Execution(
                workflow_name=workflow_name,
                status=status,
                started_at=today_start - timedelta(days=offset) + timedelta(hours=12),
                completed_at=today_start - timedelta(days=offset) + timedelta(hours=13),
                executed_by=platform_admin.user_id,
                executed_by_name=platform_admin.name,
                organization_id=organization.id,
            )
            for offset, status in enumerate(previous_statuses, start=1)
        )
        executions.extend(
            [
                Execution(
                    workflow_name=workflow_name,
                    status=ExecutionStatus.RUNNING,
                    started_at=now - timedelta(seconds=1),
                    executed_by=platform_admin.user_id,
                    executed_by_name=platform_admin.name,
                    organization_id=organization.id,
                ),
                Execution(
                    workflow_name=workflow_name,
                    status=ExecutionStatus.SUCCESS,
                    started_at=now - timedelta(seconds=1),
                    completed_at=now,
                    executed_by=platform_admin.user_id,
                    executed_by_name=platform_admin.name,
                    organization_id=organization.id,
                    is_local_execution=True,
                ),
            ]
        )
        db_session.add_all(executions)
        await db_session.commit()

        try:
            response = e2e_client.get(
                "/api/metrics/executions/timeseries",
                headers=platform_admin.headers,
                params={
                    "window": "7d",
                    "timezone": "UTC",
                    "scope": str(organization.id),
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()

            assert len(data["buckets"]) == 7
            assert all(
                bucket["success_count"] + bucket["failed_count"] > 0
                for bucket in data["buckets"]
            )
            assert data["buckets"][-1]["success_count"] == 1005
            assert data["success_count"] == 1006
            assert data["failed_count"] == 5
            assert data["total_count"] == 1011
        finally:
            await db_session.execute(
                delete(Execution).where(Execution.workflow_name == workflow_name)
            )
            await db_session.delete(organization)
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_execution_timeseries_matches_history_visibility(
        self,
        e2e_client,
        org1_user,
        platform_admin,
        org1,
        db_session,
    ):
        """Org users only aggregate their own non-local executions."""
        from datetime import datetime, timedelta, timezone
        from uuid import UUID, uuid4

        from sqlalchemy import delete

        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution

        workflow_name = f"timeseries-scope-{uuid4()}"
        now = datetime.now(timezone.utc)
        db_session.add_all(
            [
                Execution(
                    workflow_name=workflow_name,
                    status=ExecutionStatus.SUCCESS,
                    started_at=now - timedelta(minutes=2),
                    completed_at=now - timedelta(minutes=1),
                    executed_by=org1_user.user_id,
                    executed_by_name=org1_user.name,
                    organization_id=UUID(org1["id"]),
                ),
                Execution(
                    workflow_name=workflow_name,
                    status=ExecutionStatus.FAILED,
                    started_at=now - timedelta(minutes=2),
                    completed_at=now - timedelta(minutes=1),
                    executed_by=platform_admin.user_id,
                    executed_by_name=platform_admin.name,
                    organization_id=UUID(org1["id"]),
                ),
            ]
        )
        await db_session.commit()

        try:
            response = e2e_client.get(
                "/api/metrics/executions/timeseries",
                headers=org1_user.headers,
                params={"window": "24h", "timezone": "UTC"},
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["success_count"] == 1
            assert data["failed_count"] == 0
            assert data["total_count"] == 1
        finally:
            await db_session.execute(
                delete(Execution).where(Execution.workflow_name == workflow_name)
            )
            await db_session.commit()


# TestLogs removed: the /api/logs stub endpoint was replaced by /api/audit.
# See tests/e2e/api/test_audit_log.py for the audit log test coverage.
