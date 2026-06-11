"""
E2E security guardrail: child process environment isolation (Phase 2).

Registers real workflows and executes them through the real path
(API -> queue -> worker container -> template fork -> child), then asserts
on the env the workflow saw from inside its own process. This probes the
exact process that runs customer code — not the test-runner.

RED on main (children inherit the full worker env, including
BIFROST_SECRET_KEY and the DB/RabbitMQ/S3 credentials). GREEN with the
Phase 2 template env scrub + engine-token hand-down + API module-fetch
endpoint on this branch. No env gate: the scrub is unconditional in the
worker, so these assertions must always hold.
"""

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register

ENV_PROBE_WORKFLOW = '''
"""Security probe: report the child process's credential env state."""
import os
from bifrost import workflow


@workflow(name="e2e_security_env_probe", execution_mode="async")
async def e2e_security_env_probe() -> dict:
    """Introspect this process's environment for platform credentials."""
    env = os.environ
    return {
        "pid": os.getpid(),
        "secret_key_present": "BIFROST_SECRET_KEY" in env,
        "database_url_present": "BIFROST_DATABASE_URL" in env,
        "database_url_sync_present": "BIFROST_DATABASE_URL_SYNC" in env,
        "rabbitmq_url_present": "BIFROST_RABBITMQ_URL" in env,
        "s3_vars": sorted(k for k in env if k.startswith("BIFROST_S3_")),
        "redis_url_present": bool(env.get("BIFROST_REDIS_URL")),
    }
'''

SDK_CONTROL_WORKFLOW = '''
"""Positive control: the SDK's authenticated HTTP path works post-scrub."""
import bifrost
from bifrost import workflow


@workflow(name="e2e_security_sdk_control", execution_mode="async")
async def e2e_security_sdk_control() -> dict:
    """Exercise child -> API auth (token hand-down) with a real SDK call.

    The security property is that the authenticated call *succeeds* — i.e.
    the parent-minted engine token carries the child's HTTP request without
    SECRET_KEY in the env. We do NOT assert on org_count: organizations.list()
    is org-scoped and the visible count depends on whatever else ran in the
    suite. A 401/403 would raise; reaching the return proves auth worked.
    """
    orgs = await bifrost.organizations.list()
    return {"org_count": len(orgs), "auth_succeeded": True}
'''


@pytest.fixture(scope="module")
def env_probe_workflow(e2e_client, platform_admin):
    """Register the env-probe workflow."""
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_security_env_probe.py",
        ENV_PROBE_WORKFLOW,
        "e2e_security_env_probe",
    )
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_security_env_probe.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def sdk_control_workflow(e2e_client, platform_admin):
    """Register the SDK positive-control workflow."""
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_security_sdk_control.py",
        SDK_CONTROL_WORKFLOW,
        "e2e_security_sdk_control",
    )
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_security_sdk_control.py",
        headers=platform_admin.headers,
    )


@pytest.mark.e2e
class TestChildEnvIsolation:
    """Phase 2 guardrails: forked execution children hold no platform credentials."""

    def test_child_env_has_no_platform_credentials(
        self, e2e_client, platform_admin, env_probe_workflow
    ):
        """The child env must contain no SECRET_KEY / DB / RabbitMQ / S3 vars.

        BIFROST_REDIS_URL must remain — the child reads execution context,
        streams logs, and buffers SDK writes through Redis.
        """
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            env_probe_workflow["id"],
            {},
            max_wait=30.0,
        )
        assert data["status"] == "Success", f"Env probe failed to execute: {data}"
        probe = data.get("result", {})

        assert probe.get("secret_key_present") is False, (
            "BIFROST_SECRET_KEY is present in the execution child env. "
            "The template scrub (sentinel + primed settings cache) is not applied."
        )
        assert probe.get("database_url_present") is False, (
            "BIFROST_DATABASE_URL is present in the execution child env."
        )
        assert probe.get("database_url_sync_present") is False, (
            "BIFROST_DATABASE_URL_SYNC is present in the execution child env."
        )
        assert probe.get("rabbitmq_url_present") is False, (
            "BIFROST_RABBITMQ_URL is present in the execution child env."
        )
        assert probe.get("s3_vars") == [], (
            f"S3 credentials present in execution child env: {probe.get('s3_vars')}"
        )
        assert probe.get("redis_url_present") is True, (
            "BIFROST_REDIS_URL is absent from the child env — "
            "engine Redis I/O would be broken."
        )

    def test_sdk_authenticated_call_works_post_scrub(
        self, e2e_client, platform_admin, sdk_control_workflow
    ):
        """Positive control: child -> API SDK auth works without SECRET_KEY.

        The child can no longer mint its own token (no key in env); this
        proves the parent-minted engine token hand-down carries the SDK's
        authenticated HTTP path end to end.
        """
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sdk_control_workflow["id"],
            {},
            max_wait=30.0,
        )
        # A 401/403 in the child would surface as a Failed execution (the SDK
        # raises), so reaching Success with auth_succeeded proves the
        # token hand-down carried the authenticated call. org_count is
        # reported for context but intentionally not asserted (org-scoped,
        # suite-state-dependent).
        assert data["status"] == "Success", f"SDK control workflow failed: {data}"
        result = data.get("result", {})
        assert result.get("auth_succeeded") is True, (
            "Child -> API authenticated SDK call did not complete — "
            "token hand-down may be broken."
        )
