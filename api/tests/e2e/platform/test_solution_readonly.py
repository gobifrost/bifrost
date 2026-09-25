"""An ordinary workspace workflow remains mutable alongside managed ones."""
from __future__ import annotations

import uuid
import pytest

pytestmark = pytest.mark.e2e

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
