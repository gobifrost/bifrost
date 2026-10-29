"""
Integration tests for the execution logs list endpoint.

Tests the admin-only endpoint GET /api/executions/logs for listing logs
across all executions with filtering and pagination.

These tests make real HTTP requests to the running API.
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete
from src.models.orm.executions import Execution, ExecutionLog

import httpx
import pytest


# API URL (from docker-compose.test.yml)
TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")


@pytest.fixture(scope="module")
def http_client():
    """HTTP client for making API requests."""
    with httpx.Client(base_url=TEST_API_URL, timeout=30.0) as client:
        yield client


@pytest.mark.e2e
class TestLogsListEndpoint:
    """Integration tests for GET /api/executions/logs endpoint."""

    @pytest.fixture(autouse=True)
    def _persisted_callers(self, platform_admin, org1_user):
        """Use users whose persisted roles and reach the API can resolve."""
        self.admin_headers = platform_admin.headers
        self.org_user_headers = org1_user.headers

    def test_list_logs_requires_admin(
        self,
        http_client: httpx.Client,
    ):
        """Users without execution-log reach should get 403 Forbidden."""
        response = http_client.get(
            "/api/executions/logs",
            headers=self.org_user_headers,
        )
        assert response.status_code == 403

    def test_list_logs_returns_paginated_results(
        self,
        http_client: httpx.Client,
    ):
        """Admin can list logs with pagination."""
        response = http_client.get(
            "/api/executions/logs",
            headers=self.admin_headers,
            params={"limit": 10},
        )
        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "logs" in data
        assert isinstance(data["logs"], list)
        # continuation_token may be None or a string
        assert "continuation_token" in data

    def test_list_logs_filters_by_level(
        self,
        http_client: httpx.Client,
    ):
        """Admin can filter logs by level."""
        response = http_client.get(
            "/api/executions/logs",
            headers=self.admin_headers,
            params={"levels": "ERROR,WARNING", "limit": 50},
        )
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert isinstance(data["logs"], list)

        # If any logs are returned, verify they have the expected levels
        for log in data["logs"]:
            assert log["level"] in ["ERROR", "WARNING"]

    def test_list_logs_filters_by_workflow_name(
        self,
        http_client: httpx.Client,
    ):
        """Admin can filter logs by workflow name (partial match)."""
        response = http_client.get(
            "/api/executions/logs",
            headers=self.admin_headers,
            params={"workflow_name": "test", "limit": 50},
        )
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert isinstance(data["logs"], list)

        # If any logs are returned, verify they contain the search term
        for log in data["logs"]:
            assert "test" in log["workflow_name"].lower()

    def test_list_logs_message_search(
        self,
        http_client: httpx.Client,
    ):
        """Admin can search in log message content."""
        response = http_client.get(
            "/api/executions/logs",
            headers=self.admin_headers,
            params={"message_search": "error", "limit": 50},
        )
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert isinstance(data["logs"], list)

        # If any logs are returned, verify they contain the search term
        for log in data["logs"]:
            assert "error" in log["message"].lower()

    @pytest.mark.asyncio
    async def test_list_logs_pagination_with_token(
        self,
        http_client: httpx.Client,
        db_session,
    ):
        """Equal timestamps and arriving logs cannot duplicate or omit older rows."""
        execution_id = uuid4()
        workflow_name = f"log-cursor-{uuid4().hex}"
        timestamp = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        db_session.add(Execution(
            id=execution_id,
            workflow_name=workflow_name,
            executed_by_name="Log pagination fixture",
        ))
        await db_session.flush()
        seeded = [ExecutionLog(
            execution_id=execution_id, level="INFO",
            message=f"seed-{index}", timestamp=timestamp, sequence=index,
        ) for index in range(5)]
        db_session.add_all(seeded)
        await db_session.commit()
        expected_ids = sorted((log.id for log in seeded), reverse=True)
        headers = self.admin_headers
        params = {"limit": 2, "workflow_name": workflow_name}

        try:
            first_response = http_client.get(
                "/api/executions/logs", headers=headers, params=params,
            )
            assert first_response.status_code == 200
            first = first_response.json()
            assert [log["id"] for log in first["logs"]] == expected_ids[:2]
            assert first["continuation_token"]

            # Arrivals before the cursor must not shift the remaining pages.
            # Include a same-timestamp arrival to exercise the ID tie-breaker.
            db_session.add_all([
                ExecutionLog(execution_id=execution_id, level="INFO",
                             message="newer", timestamp=timestamp + timedelta(seconds=1)),
                ExecutionLog(execution_id=execution_id, level="INFO",
                             message="same-time arrival", timestamp=timestamp),
            ])
            await db_session.commit()

            second_response = http_client.get(
                "/api/executions/logs", headers=headers,
                params={**params, "continuation_token": first["continuation_token"]},
            )
            assert second_response.status_code == 200
            second = second_response.json()
            assert [log["id"] for log in second["logs"]] == expected_ids[2:4]
            assert second["continuation_token"]

            third_response = http_client.get(
                "/api/executions/logs", headers=headers,
                params={**params, "continuation_token": second["continuation_token"]},
            )
            assert third_response.status_code == 200
            third = third_response.json()
            assert [log["id"] for log in third["logs"]] == expected_ids[4:]
            assert third["continuation_token"] is None
        finally:
            await db_session.execute(delete(ExecutionLog).where(
                ExecutionLog.execution_id == execution_id,
            ))
            await db_session.execute(delete(Execution).where(Execution.id == execution_id))
            await db_session.commit()

    def test_list_logs_date_range_filter(
        self,
        http_client: httpx.Client,
    ):
        """Admin can filter logs by date range."""
        response = http_client.get(
            "/api/executions/logs",
            headers=self.admin_headers,
            params={
                "start_date": "2020-01-01T00:00:00Z",
                "end_date": "2030-12-31T23:59:59Z",
                "limit": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert isinstance(data["logs"], list)

    def test_list_logs_response_structure(
        self,
        http_client: httpx.Client,
    ):
        """Verify log entries have the expected structure."""
        response = http_client.get(
            "/api/executions/logs",
            headers=self.admin_headers,
            params={"limit": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data

        # If there are any logs, check their structure
        for log in data["logs"]:
            assert "id" in log
            assert "execution_id" in log
            assert "level" in log
            assert "message" in log
            assert "timestamp" in log
            assert "workflow_name" in log
            # organization_name may be None for global executions

    def test_list_logs_without_auth(
        self,
        http_client: httpx.Client,
    ):
        """Unauthenticated requests should be rejected."""
        response = http_client.get("/api/executions/logs")
        # Should get 401 (unauthenticated) or 403 (forbidden)
        assert response.status_code in [401, 403]
