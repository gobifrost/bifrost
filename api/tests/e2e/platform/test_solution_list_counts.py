"""Solutions list entity counts.

The Solutions catalog should be able to render per-install content counts
without fetching each install's full entity inventory.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.file_metadata import FileMetadata
from src.models.orm.forms import Form
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_solution_list_includes_entity_counts(e2e_client, platform_admin, db_session):
    slug = f"counted-{uuid.uuid4().hex[:8]}"
    create = e2e_client.post(
        "/api/solutions",
        headers=platform_admin.headers,
        json={"slug": slug, "name": "Counted Solution", "organization_id": None},
    )
    assert create.status_code in (200, 201), create.text
    solution_id = UUID(create.json()["id"])

    db_session.add_all(
        [
            Workflow(
                name="counted_workflow",
                function_name="run",
                path="workflows/counted.py",
                solution_id=solution_id,
            ),
            Application(
                name="Counted App",
                slug=f"counted-app-{uuid.uuid4().hex[:8]}",
                repo_path="apps/counted",
                solution_id=solution_id,
            ),
            Form(
                name="Counted Form",
                created_by=str(platform_admin.user_id),
                solution_id=solution_id,
            ),
            Agent(
                name="Counted Agent",
                system_prompt="Help with counting.",
                created_by=str(platform_admin.user_id),
                solution_id=solution_id,
            ),
            Table(name=f"counted_table_{uuid.uuid4().hex[:8]}", solution_id=solution_id),
            CustomClaim(
                name=f"counted_claim_{uuid.uuid4().hex[:8]}",
                type="list",
                query={"table": "counted_table", "select": "id"},
                solution_id=solution_id,
            ),
            FileMetadata(
                solution_id=solution_id,
                location="reports",
                path="demo/readme.txt",
                s3_key=f"solutions/{solution_id}/reports/demo/readme.txt",
                size_bytes=12,
            ),
        ]
    )
    await db_session.commit()

    listed = e2e_client.get("/api/solutions", headers=platform_admin.headers)
    assert listed.status_code == 200, listed.text
    solution = next(
        item for item in listed.json()["solutions"] if item["id"] == str(solution_id)
    )

    assert solution["entity_counts"] == {
        "workflows": 1,
        "apps": 1,
        "forms": 1,
        "agents": 1,
        "tables": 1,
        "claims": 1,
        "files": 1,
    }
