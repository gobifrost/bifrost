"""``GET /api/permissions/catalog``: any signed-in user reads every domain (it
is vocabulary); anonymous requests are refused."""

import pytest

from src.models.contracts.permissions import PERMISSION_DOMAINS

pytestmark = pytest.mark.e2e


def test_platform_admin_reads_every_domain(e2e_client, platform_admin):
    response = e2e_client.get("/api/permissions/catalog", headers=platform_admin.headers)
    assert response.status_code == 200, response.text
    entries = response.json()
    assert {entry["domain"] for entry in entries} == set(PERMISSION_DOMAINS)
    lifecycle = next(entry for entry in entries if entry["domain"] == "users.lifecycle")
    assert lifecycle["title"] == "Move, delete & change base role"
    assert lifecycle["privileged"] == ["users.lifecycle.readwrite"]


def test_plain_organization_user_reads_the_catalog(e2e_client, org1_user):
    response = e2e_client.get("/api/permissions/catalog", headers=org1_user.headers)
    assert response.status_code == 200, response.text
    assert {entry["domain"] for entry in response.json()} == set(PERMISSION_DOMAINS)


def test_anonymous_request_is_refused(e2e_client):
    response = e2e_client.get("/api/permissions/catalog")
    assert response.status_code == 401, response.text
