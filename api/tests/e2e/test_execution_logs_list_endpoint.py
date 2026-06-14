"""
Integration tests for the execution logs list endpoint.

Tests the admin-only endpoint GET /api/executions/logs for listing logs
across all executions with filtering and pagination.

These tests make real HTTP requests to the running API.
"""

import os

import httpx
import pytest

from tests.fixtures.auth import create_test_jwt, auth_headers


# API URL (from docker-compose.test.yml)
TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")


@pytest.fixture(scope="module")
def http_client():
    """HTTP client for making API requests."""
    with httpx.Client(base_url=TEST_API_URL, timeout=30.0) as client:
        yield client


@pytest.fixture
def admin_token():
    """JWT token for a platform admin (superuser)."""
    return create_test_jwt(
        email="admin@test.com",
        name="Test Admin",
        is_superuser=True,
    )


@pytest.fixture
def regular_user_token():
    """JWT token for a regular org user (non-superuser)."""
    return create_test_jwt(
        email="user@test.com",
        name="Test User",
        is_superuser=False,
    )


@pytest.mark.e2e
class TestLogsListEndpoint:
    """Integration tests for GET /api/executions/logs endpoint."""

    def test_list_logs_requires_admin(
        self,
        http_client: httpx.Client,
        regular_user_token: str,
    ):
        """Non-admin users should get 403 Forbidden."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(regular_user_token),
        )
        assert response.status_code == 403
        # Gated by the declarative RequirePlatformAdmin dependency, which
        # returns the canonical "Superuser privileges required" detail.
        detail = response.json().get("detail", "").lower()
        assert "superuser" in detail or "admin" in detail

    def test_list_logs_returns_paginated_results(
        self,
        http_client: httpx.Client,
        admin_token: str,
    ):
        """Admin can list logs with pagination."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
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
        admin_token: str,
    ):
        """Admin can filter logs by level."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
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
        admin_token: str,
    ):
        """Admin can filter logs by workflow name (partial match)."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
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
        admin_token: str,
    ):
        """Admin can search in log message content."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={"message_search": "error", "limit": 50},
        )
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert isinstance(data["logs"], list)

        # If any logs are returned, verify they contain the search term
        for log in data["logs"]:
            assert "error" in log["message"].lower()

    def test_list_logs_pagination_with_token(
        self,
        http_client: httpx.Client,
        admin_token: str,
    ):
        """Pagination with continuation token works correctly."""
        # First request with small limit
        response1 = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={"limit": 2},
        )
        assert response1.status_code == 200
        data1 = response1.json()
        assert "logs" in data1

        # If there's a continuation token, fetch next page
        if data1.get("continuation_token"):
            response2 = http_client.get(
                "/api/executions/logs",
                headers=auth_headers(admin_token),
                params={
                    "limit": 2,
                    "continuation_token": data1["continuation_token"],
                },
            )
            assert response2.status_code == 200
            data2 = response2.json()
            assert "logs" in data2

            # The pages should be different (if there are enough logs)
            if data1["logs"] and data2["logs"]:
                # Log IDs should be different between pages
                ids1 = {log["id"] for log in data1["logs"]}
                ids2 = {log["id"] for log in data2["logs"]}
                assert ids1.isdisjoint(ids2), "Pages should not contain duplicate logs"

    def test_list_logs_date_range_filter(
        self,
        http_client: httpx.Client,
        admin_token: str,
    ):
        """Admin can filter logs by date range."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
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
        admin_token: str,
    ):
        """Verify log entries have the expected structure."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
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
