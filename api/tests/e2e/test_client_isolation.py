"""The shared ``e2e_client`` must stay anonymous no matter which fixtures ran."""

import pytest


@pytest.mark.e2e
def test_credential_fixtures_leave_shared_client_anonymous(
    e2e_client, platform_admin, org1_user, refresh_user_tokens
):
    """Logging users in must not leave auth cookies that authenticate later requests."""
    refresh_user_tokens(org1_user)

    assert not list(e2e_client.cookies.jar)
    response = e2e_client.get("/api/config")
    assert response.status_code == 401, response.text
