"""E2E: RequiredConnectionUnset runtime escalation.

When a workflow calls ``integrations.get("X")`` and X is missing BUT the calling
solution DECLARED X via ``SolutionConnectionSchema``, the server raises a loud
424 naming X (mirroring RequiredConfigUnset) instead of returning a silent None.
Non-declared / loose calls keep returning 200 + null.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.models.orm.solution_connection_schema import SolutionConnectionSchema

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "scope": "global"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_declared_missing_integration_raises(e2e_client, platform_admin, db_session):
    """Declared but unconfigured integration -> 424 naming the integration."""
    headers = platform_admin.headers
    slug = f"conn-rt-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    # Unique name so no real/global Integration shadows it.
    ghost = f"Ghost-{uuid.uuid4().hex[:8]}"
    db_session.add(
        SolutionConnectionSchema(
            solution_id=UUID(sid),
            integration_name=ghost,
            template={},
            position=0,
        )
    )
    await db_session.commit()

    resp = e2e_client.post(
        "/api/sdk/integrations/get",
        headers=headers,
        json={"name": ghost, "scope": "global", "solution": sid},
    )
    assert resp.status_code == 424, resp.text
    assert ghost in resp.text


async def test_undeclared_missing_integration_returns_none(e2e_client, platform_admin):
    """No solution context (or a name no solution declares) -> 200 + null."""
    resp = e2e_client.post(
        "/api/sdk/integrations/get",
        headers=platform_admin.headers,
        json={"name": f"Nonexistent-{uuid.uuid4().hex[:8]}", "scope": "global"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() is None
