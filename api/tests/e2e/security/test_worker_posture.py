"""
E2E security guardrail test: worker process posture.

Executes a workflow script inside the worker process that introspects
/proc/self/status to verify security posture. The test is parameterized
by an env marker (BIFROST_POSTURE_HARDENED=1) so it is:

- SKIPPED on unmodified debug/test stacks (marker absent) — these stacks
  run as root before Phase 1 changes. The positive-control execution still
  runs to prove the worker is alive.
- RED on any stack with the marker but without the Phase 1 compose changes.
- GREEN after C1.4–C1.5 (user: "1000:1000", cap_drop: [ALL],
  no-new-privileges:true) are applied.

Run with marker after Phase 1 changes:
    BIFROST_POSTURE_HARDENED=1 ./test.sh tests/e2e/security/test_worker_posture.py -v
"""

from __future__ import annotations

import base64
import os

import pytest

from src.sdk.context import Caller, Organization
from src.services.execution.engine import ExecutionRequest, ExecutionResult, execute


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode(code: str) -> str:
    return base64.b64encode(code.encode()).decode()


def _posture_hardened() -> bool:
    return os.environ.get("BIFROST_POSTURE_HARDENED", "") == "1"


async def _run_introspect_script(script: str) -> ExecutionResult:
    """Run a script in the worker and return its ExecutionResult."""
    request = ExecutionRequest(
        execution_id="test-posture-introspect",
        caller=Caller(
            user_id="test-user",
            email="test@example.com",
            name="Test User",
        ),
        organization=Organization(
            id="test-org",
            name="Test Org",
            is_active=True,
        ),
        code=_encode(script),
        name=None,
        parameters={},
        transient=True,
    )
    return await execute(request)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWorkerPosture:
    """Worker process security posture assertions (Phase 1 guardrail)."""

    @pytest.mark.asyncio
    async def test_worker_executes_scripts(self) -> None:
        """Positive control: worker can run a script. Always active.

        Ensures the worker itself is live and the execution engine is healthy,
        independent of the BIFROST_POSTURE_HARDENED marker.
        """
        result = await _run_introspect_script(
            "result = 'worker_alive'\n"
        )
        assert result.status.value in ("Success", "CompletedWithErrors"), (
            f"Worker did not execute script: {result.status}, error={getattr(result, 'error_message', None)}"
        )

    @pytest.mark.asyncio
    async def test_worker_is_not_root(self) -> None:
        """Worker process must not run as UID 0 (root).

        RED before Phase 1 (compose has no user: directive).
        GREEN after C1.4 adds user: "1000:1000".
        """
        if not _posture_hardened():
            pytest.skip(
                "BIFROST_POSTURE_HARDENED not set; skipping posture assertion. "
                "Set BIFROST_POSTURE_HARDENED=1 after applying Phase 1 changes."
            )

        script = "import os\nresult = os.getuid()\n"
        result = await _run_introspect_script(script)
        assert result.status.value == "Success", f"Script failed: {result}"
        uid = result.result
        assert uid != 0, (
            "Worker process is running as root (UID=0). "
            "Apply Phase 1 (C1.4: user: '1000:1000' in docker-compose.yml)."
        )
        assert uid == 1000, (
            f"Worker UID={uid}, expected 1000 (bifrost user)."
        )

    @pytest.mark.asyncio
    async def test_worker_capabilities_empty(self) -> None:
        """Worker process must have no effective capabilities (CapEff=0).

        RED before Phase 1 (no cap_drop: [ALL]).
        GREEN after C1.4 adds cap_drop: [ALL].
        """
        if not _posture_hardened():
            pytest.skip(
                "BIFROST_POSTURE_HARDENED not set; skipping posture assertion."
            )

        script = dedent_script("""
            import re
            cap_eff = 0
            try:
                with open('/proc/self/status') as f:
                    for line in f:
                        m = re.match(r'CapEff:\\s+([0-9a-f]+)', line)
                        if m:
                            cap_eff = int(m.group(1), 16)
                            break
            except OSError:
                cap_eff = -1
            result = cap_eff
        """)
        result = await _run_introspect_script(script)
        assert result.status.value == "Success", f"Script failed: {result}"
        cap_value = result.result
        assert cap_value != -1, "Could not read /proc/self/status in worker"
        assert cap_value == 0, (
            f"Worker has effective capabilities: {cap_value:#018x}. "
            f"Apply Phase 1 (C1.4: cap_drop: [ALL] in docker-compose.yml)."
        )

    @pytest.mark.asyncio
    async def test_worker_no_new_privileges(self) -> None:
        """Worker must have NoNewPrivs=1 (no-new-privileges:true).

        RED before Phase 1.
        GREEN after C1.4.
        """
        if not _posture_hardened():
            pytest.skip(
                "BIFROST_POSTURE_HARDENED not set; skipping posture assertion."
            )

        script = dedent_script("""
            import re
            nnp = -1
            try:
                with open('/proc/self/status') as f:
                    for line in f:
                        m = re.match(r'NoNewPrivs:\\s+(\\d+)', line)
                        if m:
                            nnp = int(m.group(1))
                            break
            except OSError:
                nnp = -1
            result = nnp
        """)
        result = await _run_introspect_script(script)
        assert result.status.value == "Success", f"Script failed: {result}"
        nnp = result.result
        assert nnp != -1, "Could not read NoNewPrivs from /proc/self/status"
        assert nnp == 1, (
            f"Worker NoNewPrivs={nnp}, expected 1. "
            f"Apply Phase 1 (C1.4: security_opt: [no-new-privileges:true])."
        )


def dedent_script(s: str) -> str:
    """Remove leading indentation from a multi-line script string."""
    import textwrap
    return textwrap.dedent(s).lstrip("\n")
