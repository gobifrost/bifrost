"""
Shared fixtures for API integration tests.

Integration tests make real HTTP requests to running API and use real
PostgreSQL database for data setup.
"""

import logging
import os

import pytest
import httpx

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def api_base_url():
    """
    Base URL for API.

    Default: http://localhost:18000 (docker-compose.test.yml)
    Can be overridden with TEST_API_URL environment variable.
    """
    return os.getenv("TEST_API_URL", "http://localhost:18000")


@pytest.fixture(scope="module")
def http_client(api_base_url):
    """HTTP client for making API requests."""
    with httpx.Client(base_url=api_base_url, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="module")
def test_org_id():
    """
    Test organization ID for integration tests.

    This org should be created during test setup/migrations.
    """
    return "test-org-integration-12345"


@pytest.fixture
def auth_headers():
    """
    Headers for authenticated requests with admin role.

    Uses JWT token format expected by the FastAPI auth middleware.
    """
    # For integration tests, we'll need to either:
    # 1. Create a real user and get a real JWT token
    # 2. Or use a test-only auth bypass

    # This is a placeholder - actual implementation depends on your auth setup
    return {
        "Authorization": "Bearer test-integration-token",
        "Content-Type": "application/json",
    }


@pytest.fixture
def platform_admin_headers():
    """
    Headers for platform admin user (GLOBAL scope).

    Platform admin endpoints require GLOBAL org scope.
    """
    return {
        "Authorization": "Bearer test-platform-admin-token",
        "Content-Type": "application/json",
    }


@pytest.fixture
def admin_headers():
    """Headers for admin user in test organization."""
    return {
        "Authorization": "Bearer test-admin-token",
        "Content-Type": "application/json",
    }


@pytest.fixture
def user_headers():
    """Headers for regular user in test organization."""
    return {
        "Authorization": "Bearer test-user-token",
        "Content-Type": "application/json",
    }
