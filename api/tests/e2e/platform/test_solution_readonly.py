"""End-to-end read-only enforcement for solution-managed entities.

Criterion 6: a deployed (solution-managed) workflow is read-only on the platform
— every non-deploy mutation API returns the locked "Solution-managed…" error.
Criterion 7's table carve-out (row data editable) is covered by the unit guard
tests + the table router only guarding schema/delete, not document/* mutations.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e

_MSG = "Solution-managed entities can only be managed by deployment methods."


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_solution_workflow_is_read_only(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"ro-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    wf_id = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/w.py": "from bifrost import workflow\n@workflow\nasync def w():\n    return {}\n"},
        "workflows": [{
            "id": wf_id, "name": f"w_{slug}", "function_name": "w",
            "path": "workflows/w.py", "type": "workflow",
        }],
    })
    assert dep.status_code in (200, 201), dep.text

    # Deploy remaps the manifest id to uuid5(install_id, manifest_id); address by that.
    real_id = str(solution_entity_id(UUID(sid), UUID(wf_id)))

    # PATCH must be refused with the exact locked message.
    patch = e2e_client.patch(f"/api/workflows/{real_id}", headers=headers, json={"display_name": "hijack"})
    assert patch.status_code == 409, patch.text
    assert patch.json()["detail"] == _MSG

    # DELETE must be refused likewise (so the entity is NOT removed).
    dele = e2e_client.delete(f"/api/workflows/{real_id}", headers=headers)
    assert dele.status_code == 409, dele.text
    assert dele.json()["detail"] == _MSG

    # Re-running the workflow still succeeds — it was neither mutated nor deleted.
    from tests.e2e.conftest import execute_workflow_sync
    result = execute_workflow_sync(e2e_client, headers, real_id, request_sync=True)
    assert result["status"] == "Success", result


def test_repo_workflow_still_mutable(e2e_client, platform_admin):
    """A non-solution _repo/ workflow remains fully mutable (no regression)."""
    from tests.e2e.conftest import write_and_register

    headers = platform_admin.headers
    fn = f"repo_rw_{uuid.uuid4().hex[:8]}"
    wf = write_and_register(
        e2e_client, headers,
        path=f"workflows/{fn}.py",
        content=f"from bifrost import workflow\n\n@workflow\nasync def {fn}() -> dict:\n    return {{}}\n",
        function_name=fn,
    )
    patch = e2e_client.patch(f"/api/workflows/{wf['id']}", headers=headers, json={"display_name": "ok"})
    assert patch.status_code == 200, patch.text
