"""Shared fixtures for ``test_cli_*`` E2E tests.

Two pieces consolidated here:

* ``cli_client`` — constructs a :class:`bifrost.client.BifrostClient` bound to
  the live E2E stack + the platform admin's JWT and installs it on the
  :data:`bifrost.client._thread_local` singleton for the duration of the test
  so each command's ``pass_resolver`` plumbing hands our client to the
  command body. The previous singleton (if any) is restored on teardown.
* ``invoke_cli`` — returns a callable ``(group, args) -> click.testing.Result``
  wrapping :class:`click.testing.CliRunner` with the project's standard
  invocation flags (``standalone_mode=False``, ``catch_exceptions=False``).

Also bumps ``sys.path`` so the standalone ``bifrost`` package (``api/bifrost``)
imports cleanly from these tests — mirrors the per-file shim that used to
live at the top of every ``test_cli_*.py``.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys
import uuid
from typing import Any

import pytest

logger = logging.getLogger(__name__)

# Standalone bifrost package import — mirrors the shim that used to live at
# the top of every ``test_cli_*.py``. ``parents[3]`` resolves to ``api/``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))


class _DeployResult:
    """httpx-Response-like shim for the now-async solution deploy.

    Deploy is enqueued (202 + ``deploy_job_id``) and observed by polling
    ``GET /api/solutions/deploy-jobs/{id}``. This shim lets the many existing
    e2e call sites keep their ``deploy.status_code`` / ``deploy.json()`` shape:
    on a succeeded job ``status_code`` is 200 and ``json()`` returns the deploy
    result counts; on a failed job ``status_code`` is 422 and ``json()`` carries
    a ``detail`` (mirroring the old synchronous error response).
    """

    def __init__(self, status_code: int, payload: dict, text: str) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload


def wait_for_deploy(e2e_client, post_resp, headers, *, timeout_s: float = 30.0):
    """Given a deploy POST response, return a terminal-state shim.

    A synchronous error (non-202 — git-connected, pending-capture block,
    downgrade gate) is returned unchanged. A 202 is polled to a terminal job
    status, then mapped onto the old response shape via :class:`_DeployResult`.
    """
    import time as _time

    if post_resp.status_code != 202:
        return post_resp
    job_id = post_resp.json()["deploy_job_id"]
    deadline = _time.monotonic() + timeout_s
    body: dict = {}
    while _time.monotonic() < deadline:
        st = e2e_client.get(f"/api/solutions/deploy-jobs/{job_id}", headers=headers)
        assert st.status_code == 200, f"status fetch failed: {st.status_code} {st.text}"
        body = st.json()
        if body["status"] == "succeeded":
            result = body.get("result") or {}
            return _DeployResult(200, result, post_resp.text)
        if body["status"] == "failed":
            detail = body.get("error") or "deploy failed"
            return _DeployResult(422, {"detail": detail}, detail)
        _time.sleep(0.25)
    raise AssertionError(f"deploy job {job_id} did not finish in {timeout_s}s: {body}")


def wait_for_install(e2e_client, post_resp, headers, *, timeout_s: float = 60.0):
    """Given an install POST response (zip or from-repo), return a terminal shim.

    Install is async (Task H1): a synchronous fail-fast refusal (non-202 — bad
    zip, wrong password → 422; slug/scope conflict on from-repo → 409) is returned
    unchanged. A 202 is polled to a terminal job status. On success the shim's
    ``status_code`` is 201 and ``json()`` returns the FULL installed Solution DTO
    (fetched by the job's ``result.solution_id``), so existing call sites keep
    reading ``["id"]`` / ``["version"]`` / ``["git_connected"]``. A failed build
    gate maps onto the old error shape: an inactive-install result → 409 with the
    structured detail, any other failure → 409 with a ``detail`` string.
    """
    import time as _time

    if post_resp.status_code != 202:
        return post_resp
    job_id = post_resp.json()["deploy_job_id"]
    deadline = _time.monotonic() + timeout_s
    body: dict = {}
    while _time.monotonic() < deadline:
        st = e2e_client.get(f"/api/solutions/deploy-jobs/{job_id}", headers=headers)
        assert st.status_code == 200, f"status fetch failed: {st.status_code} {st.text}"
        body = st.json()
        if body["status"] == "succeeded":
            result = body.get("result") or {}
            sid = result.get("solution_id") or body.get("install_id")
            sol = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
            assert sol.status_code == 200, f"solution fetch failed: {sol.text}"
            return _DeployResult(201, sol.json(), sol.text)
        if body["status"] == "failed":
            result = body.get("result") or {}
            error = body.get("error") or "install failed"
            if result.get("reason") == "inactive_install_exists":
                return _DeployResult(
                    409,
                    {
                        "detail": {
                            "reason": "inactive_install_exists",
                            "solution_id": result.get("solution_id"),
                            "slug": result.get("slug"),
                            "message": error,
                        }
                    },
                    error,
                )
            return _DeployResult(409, {"detail": error}, error)
        _time.sleep(0.25)
    raise AssertionError(f"install job {job_id} did not finish in {timeout_s}s: {body}")


def deploy_solution(e2e_client, solution_id, headers, body):
    """POST a deploy bundle and block until the async job is terminal.

    Drop-in for ``e2e_client.post(f"/api/solutions/{id}/deploy", ...)`` that
    returns a terminal-state shim (see :func:`wait_for_deploy`)."""
    resp = e2e_client.post(
        f"/api/solutions/{solution_id}/deploy", headers=headers, json=body
    )
    return wait_for_deploy(e2e_client, resp, headers)


@pytest.fixture
def cli_client(e2e_api_url, platform_admin):
    """Bind a ``BifrostClient`` to the E2E API + admin JWT for the CLI run."""
    from bifrost import client as bifrost_client_module
    from bifrost.client import BifrostClient

    client = BifrostClient(e2e_api_url, platform_admin.access_token)
    previous = getattr(bifrost_client_module._thread_local, "bifrost_client", None)
    bifrost_client_module._thread_local.bifrost_client = client
    try:
        yield client
    finally:
        if previous is None:
            bifrost_client_module._thread_local.__dict__.pop("bifrost_client", None)
        else:
            bifrost_client_module._thread_local.bifrost_client = previous


@pytest.fixture
def invoke_cli():
    """Return a callable that invokes a Click group with the project's defaults."""
    from click.testing import CliRunner

    def _invoke(group, args):
        return CliRunner().invoke(
            group, args, standalone_mode=False, catch_exceptions=False
        )

    return _invoke


def _clear_s3_bifrost_sync() -> None:
    """Delete all .bifrost/ files from S3 repo storage, using a fresh event loop.

    Creates its own loop so this works regardless of whether pytest-asyncio
    has a loop already running in the current thread.
    """
    async def _clear() -> None:
        from src.config import get_settings
        from src.services.repo_storage import RepoStorage

        settings = get_settings()
        if not settings.s3_configured:
            return
        repo = RepoStorage(settings)
        paths = await repo.list(".bifrost/")
        for path in paths:
            try:
                await repo.delete(path)
            except Exception as e:
                # Per-path delete is best-effort during cleanup
                logger.debug(f"_clear_s3_bifrost_sync could not delete {path}: {e}")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_clear())
    except Exception as e:
        # S3 not configured / unreachable — fixture is best-effort
        logger.debug(f"_clear_s3_bifrost_sync skipped: {e}")
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def isolate_s3_sync() -> None:
    """Wipe .bifrost/ from S3 before every test in this package.

    Runs on a fresh event loop to avoid conflicting with pytest-asyncio's
    managed loop. Covers the sync HTTP-client tests that can't use the
    async ``isolate_s3`` fixture from tests/conftest.py.
    """
    _clear_s3_bifrost_sync()


@pytest.fixture
def make_solution_with_required_config(e2e_client, platform_admin, db_session):
    """Factory: create a Solution via REST then insert a SolutionConfigSchema
    declaration row directly into the DB.  Returns a coroutine that accepts
    ``key``, ``required`` and ``set_value`` kwargs and returns the solution dict.

    When ``set_value`` is False (default) no Config value is created, so the
    declaration reads as unset (is_set=False).  When True, a matching Config
    row is inserted in the install's org scope so the declaration reads as set.
    """
    from src.models.orm.config import Config
    from src.models.orm.solution_config_schema import SolutionConfigSchema

    async def _make(
        key: str = "api_key", required: bool = True, set_value: bool = False
    ) -> dict[str, Any]:
        headers = platform_admin.headers
        slug = f"setup-status-{uuid.uuid4().hex[:8]}"
        r = e2e_client.post("/api/solutions", headers=headers, json={
            "slug": slug, "name": slug.upper(), "scope": "org",
        })
        assert r.status_code in (200, 201), r.text
        sol = r.json()
        sol_id = uuid.UUID(sol["id"])
        org_id = uuid.UUID(sol["organization_id"]) if sol.get("organization_id") else None

        decl = SolutionConfigSchema(
            solution_id=sol_id,
            key=key,
            type="string",
            required=required,
            description="Required config for setup-status test",
            default="a-default",
        )
        db_session.add(decl)
        if set_value:
            db_session.add(Config(
                key=key,
                value="a-value",
                organization_id=org_id,
                updated_by="setup-status-test",
            ))
        await db_session.commit()

        return sol

    return _make


@pytest.fixture
def make_solution_without_configs(e2e_client, platform_admin):
    """Factory: create a Solution via REST with NO config declarations at all.

    Used to assert the vacuous-true guard: setup_complete must be True when
    there are no required configs to satisfy.
    """

    async def _make() -> dict[str, Any]:
        headers = platform_admin.headers
        slug = f"setup-empty-{uuid.uuid4().hex[:8]}"
        r = e2e_client.post("/api/solutions", headers=headers, json={
            "slug": slug, "name": slug.upper(), "scope": "org",
        })
        assert r.status_code in (200, 201), r.text
        return r.json()

    return _make
