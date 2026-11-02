"""End-to-end coverage for CLI password-grant boundaries on the real test API.

The default shared e2e stack has MFA enabled. This file therefore enforces the
live password-grant refusal path: a real ``bifrost login --email --password``
subprocess calls ``/auth/login`` and must exit before emitting credentials.

The successful password-grant mechanics are covered at lower layers by
``tests/unit/test_cli_login_ephemeral.py``: form login request shape, token
response handling, global credential storage, URL-only ``.env`` writing, and MFA
refusal branches. Real token issuance and MFA verification are covered by
``tests/e2e/api/test_auth.py``. The second test here preserves the remaining
live subprocess transport link by injecting real post-MFA fixture tokens through
``BIFROST_*`` env vars and calling ``bifrost api`` against the stack.
"""

from __future__ import annotations

import os
import subprocess
import sys

import httpx
import pytest
from sqlalchemy import text


def _mfa_required_for_password(api_url: str) -> bool:
    """Probe the test stack to see if global MFA is enabled."""
    with httpx.Client(base_url=api_url, timeout=10.0) as client:
        resp = client.get("/auth/status")
        resp.raise_for_status()
        return bool(resp.json().get("mfa_required_for_password", False))


def _bifrost_cli() -> list[str]:
    """Command vector for invoking the bifrost CLI via the same Python."""
    return [sys.executable, "-m", "bifrost"]


def test_ephemeral_login_refuses_mfa_required(e2e_api_url, platform_admin):
    """Default stack requires MFA, so password-grant login must refuse."""
    assert _mfa_required_for_password(e2e_api_url), (
        "default e2e stack must keep password-grant MFA enforcement enabled"
    )

    result = subprocess.run(
        _bifrost_cli()
        + [
            "login",
            "--email",
            platform_admin.email,
            "--password",
            platform_admin.password,
            "--url",
            e2e_api_url,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2, "password-grant login should refuse when MFA is required"
    assert "MFA" in result.stderr, "refusal should explain that MFA is required"


@pytest.mark.asyncio
async def test_ephemeral_env_vars_authenticate_bifrost_api_subprocess(
    cli_client, platform_admin, db_session, human_minter
):
    """The CLI's independent env credentials survive a subprocess refresh."""
    # cli_client exports its own real credential family through BIFROST_*.
    # A child can rotate that pair without stranding the shared REST fixture.
    child_env = os.environ.copy()

    # Exercise a real refresh in the child, independent of preceding tests.
    await db_session.execute(text("SELECT authz_epoch_bump_once()"))
    await db_session.commit()

    result = subprocess.run(
        _bifrost_cli() + ["api", "GET", "/api/integrations"],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, "env-token subprocess API request should succeed"
    # The child must not consume the shared REST fixture's refresh family.
    human_minter(platform_admin)
