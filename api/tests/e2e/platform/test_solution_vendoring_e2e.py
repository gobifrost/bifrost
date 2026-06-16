"""End-to-end (live): a Solution whose shared deps have been VENDORED into its
bundle installs self-contained and its ``shared.*`` import resolves WITHOUT
global-repo-access (criterion 5).

This is the falsifiable proof of vendoring: vendor_shared_deps pulls a
``shared.*`` module into the bundle as ``shared/<x>.py``; deploy installs it
under ``_solutions/{id}/shared/<x>.py``; the workflow's ``from shared.x import``
then resolves to the vendored copy via the per-execution solution import root —
even though global_repo_access is OFF (so a non-vendored _repo/ import would
fail, as proven in test_solution_deploy_execution.py).
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None, "global_repo_access": False,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_vendored_shared_dep_resolves_without_global_repo_access(e2e_client, platform_admin):
    from src.services.solutions.vendoring import vendor_shared_deps
    from tests.e2e.conftest import execute_workflow_sync

    headers = platform_admin.headers
    slug = f"vend-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    wf_id = str(uuid.uuid4())

    # The solution's own file imports a _repo/ shared module.
    solution_files = {
        "workflows/uses_shared.py": (
            "from shared.vend_calc import VALUE\n"
            "from bifrost import workflow\n\n"
            "@workflow\n"
            "async def go():\n"
            "    return {'value': VALUE}\n"
        ),
    }
    # Stand in for the origin _repo/ that vendor_shared_deps reads from.
    repo = {"shared/vend_calc.py": "VALUE = 7\n"}

    async def _repo_read(path: str):
        return repo.get(path)

    import asyncio

    vendored = asyncio.run(vendor_shared_deps(solution_files, _repo_read))
    assert vendored == {"shared/vend_calc.py": "VALUE = 7\n"}

    # The self-contained bundle = solution files + vendored shared deps.
    bundle_files = {**solution_files, **vendored}

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": bundle_files,
        "workflows": [{
            "id": wf_id, "name": f"go_{slug}", "function_name": "go",
            "path": "workflows/uses_shared.py", "type": "workflow",
        }],
    })
    assert dep.status_code in (200, 201), dep.text

    # The vendored shared.* import resolves even with global_repo_access OFF.
    # Deploy remaps manifest ids per-install (uuid5(install, manifest_id),
    # criterion 9/10) — execute by the DEPLOYED id, as real callers do.
    from src.services.solutions.deploy import solution_entity_id

    deployed_wf_id = str(solution_entity_id(uuid.UUID(sid), uuid.UUID(wf_id)))
    result = execute_workflow_sync(e2e_client, headers, deployed_wf_id, request_sync=True)
    assert result["status"] == "Success", result
    assert result["result"] == {"value": 7}
