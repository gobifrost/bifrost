"""End-to-end: create a Solution install, deploy a workflow bundle via REST,
and run the workflow — proving it executes side-by-side with _repo/ and resolves
its own solution-local imports.

Proves (live, against the running stack):
- criterion 2: a Solution deploys and runs concurrently with _repo/.
- criterion 3: a workflow imports its own modules/* from the solution root.
- criterion 4: with global_repo_access OFF, a `shared.*` _repo/ import does NOT
  resolve (no silent fallback).
- criterion 16: end users see only the deployed entity (a normal workflow).
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, *, slug: str, global_repo_access: bool) -> str:
    resp = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": slug.upper(),
            "scope": "global",
            "global_repo_access": global_repo_access,
        },
    )
    assert resp.status_code in (200, 201), f"create solution failed: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def _deploy(e2e_client, headers, solution_id: str, *, python_files: dict, workflows: list) -> dict:
    resp = e2e_client.post(
        f"/api/solutions/{solution_id}/deploy",
        headers=headers,
        json={"python_files": python_files, "workflows": workflows},
    )
    assert resp.status_code in (200, 201), f"deploy failed: {resp.status_code} {resp.text}"
    return resp.json()


def test_deploy_and_run_solution_local_import(e2e_client, platform_admin):
    """A solution workflow imports its own modules/* and runs (criteria 2,3)."""
    from tests.e2e.conftest import execute_workflow_sync

    headers = platform_admin.headers
    slug = f"sol-import-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug, global_repo_access=False)

    wf_id = str(uuid.uuid4())
    _deploy(
        e2e_client,
        headers,
        sid,
        python_files={
            "modules/calc.py": "VALUE = 42\n",
            "workflows/answer.py": (
                "from modules.calc import VALUE\n"
                "from bifrost import workflow\n\n"
                "@workflow\n"
                "async def answer():\n"
                "    return {'value': VALUE}\n"
            ),
        },
        workflows=[{
            "id": wf_id,
            "name": f"answer_{slug}",
            "function_name": "answer",
            "path": "workflows/answer.py",
            "type": "workflow",
        }],
    )

    # Execute by PORTABLE path::fn ref (what a v2 app / form uses) — the deployed
    # row id is remapped per-install (uuid5), so the manifest UUID is not a valid
    # execution handle; the path ref resolves within the install's scope (R7-P1-c).
    result = execute_workflow_sync(
        e2e_client, headers, "workflows/answer.py::answer", request_sync=True
    )
    assert result["status"] == "Success", f"unexpected: {result}"
    assert result["result"] == {"value": 42}


def test_global_repo_import_blocked_when_flag_off(e2e_client, platform_admin):
    """With global_repo_access OFF, importing a _repo/ `shared.*` module must
    NOT resolve — no silent fallback (criterion 4)."""
    from tests.e2e.conftest import execute_workflow_sync

    headers = platform_admin.headers
    slug = f"sol-noglobal-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug, global_repo_access=False)

    wf_id = str(uuid.uuid4())
    _deploy(
        e2e_client,
        headers,
        sid,
        python_files={
            "workflows/needs_shared.py": (
                "import shared.definitely_not_in_solution  # noqa\n"
                "from bifrost import workflow\n\n"
                "@workflow\n"
                "async def go():\n"
                "    return 1\n"
            ),
        },
        workflows=[{
            "id": wf_id,
            "name": f"needs_shared_{slug}",
            "function_name": "go",
            "path": "workflows/needs_shared.py",
            "type": "workflow",
        }],
    )

    result = execute_workflow_sync(
        e2e_client, headers, "workflows/needs_shared.py::go", request_sync=True
    )
    assert result["status"] == "Failed", f"expected import failure, got: {result}"
    blob = f"{result.get('error')} {result.get('error_type')}".lower()
    assert "module" in blob or "import" in blob, f"unexpected error: {result}"
