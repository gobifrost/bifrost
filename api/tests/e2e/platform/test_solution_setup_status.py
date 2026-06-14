"""E2E: GET /api/solutions/{id}/setup — required-config setup status.

Verifies that the endpoint returns a ``setup_complete`` flag and an ``items``
list that includes each SolutionConfigSchema declaration paired with whether a
matching Config value is set in the install's org scope.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


async def test_setup_status_lists_required_unset_configs(
    e2e_client, platform_admin, make_solution_with_required_config
):
    sol = await make_solution_with_required_config(key="api_key", required=True)
    headers = platform_admin.headers
    resp = e2e_client.get(f"/api/solutions/{sol['id']}/setup", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["setup_complete"] is False
    keys = {i["key"]: i for i in body["items"]}
    assert keys["api_key"]["is_set"] is False
    assert keys["api_key"]["required"] is True
