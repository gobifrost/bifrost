"""E2E (live REST + DB read): deploy a Solution with a config DECLARATION → the
SolutionConfigSchema row lands. Values are never written here (they are
instance-owned Config rows)."""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.solution_config_schema import SolutionConfigSchema
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_deploy_solution_with_config_declaration(e2e_client, platform_admin, db_session):
    headers = platform_admin.headers
    slug = f"cfg-e2e-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "config_schemas": [{
            "id": str(uuid.uuid4()), "key": "API_KEY", "type": "secret",
            "required": True, "description": "needed", "position": 0,
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text

    rows = (
        await db_session.execute(
            select(SolutionConfigSchema).where(
                SolutionConfigSchema.solution_id == UUID(sid)
            )
        )
    ).scalars().all()
    assert len(rows) == 1, f"expected one config declaration, got {len(rows)}"
    decl = rows[0]
    assert decl.key == "API_KEY"
    assert decl.type == "secret"
    assert decl.required is True
    assert decl.description == "needed"
