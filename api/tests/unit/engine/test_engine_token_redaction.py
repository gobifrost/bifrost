"""The engine token a workflow child installs as its SDK credentials must
never appear in the execution's stored output.

worker.py installs the pre-minted engine token as this process's
BIFROST_ACCESS_TOKEN/BIFROST_REFRESH_TOKEN env vars (see
_set_process_engine_credentials) and hands the same value to the engine via
ExecutionRequest.engine_token. execute() registers it as a dynamic secret on
the ExecutionContext at creation time - the same mechanism SDK modules use
for config/integration secrets - so _scrub_outputs redacts it like any
other secret from the result, logs, variables and error message.
"""

import json

from src.models.enums import ExecutionStatus
from src.sdk.context import Caller, Organization
from src.services.execution.engine import ExecutionRequest, execute

_ENGINE_TOKEN = "eng-secret-jwt-abcdefghijklmnop"


def _caller():
    return Caller(
        user_id="00000000-0000-0000-0000-000000000001",
        email="engine@bifrost.internal",
        name="Bifrost Engine",
    )


def _org():
    return Organization(
        id="22222222-2222-4222-8222-222222222222",
        name="Acme",
        is_active=True,
    )


def _request(func, **overrides):
    args = {
        "execution_id": "11111111-1111-4111-8111-111111111111",
        "caller": _caller(),
        "organization": _org(),
        "func": func,
        "name": "leaky_workflow",
        "engine_token": _ENGINE_TOKEN,
    }
    args.update(overrides)
    return ExecutionRequest(**args)


async def test_engine_token_redacted_from_result():
    async def leaky():
        import os
        return {"token": os.environ.get("BIFROST_ACCESS_TOKEN", _ENGINE_TOKEN)}

    result = await execute(_request(leaky))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["token"] == "[REDACTED]"


async def test_engine_token_redacted_from_logs():
    import logging

    async def leaky():
        logging.getLogger(__name__).info("using token %s", _ENGINE_TOKEN)
        return "ok"

    result = await execute(_request(leaky))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.logs, "expected the log line to be captured"
    assert _ENGINE_TOKEN not in json.dumps(result.logs)


async def test_engine_token_redacted_from_error_message():
    async def leaky():
        raise RuntimeError(f"auth failed for {_ENGINE_TOKEN}")

    result = await execute(_request(leaky))
    assert result.status == ExecutionStatus.FAILED
    assert _ENGINE_TOKEN not in (result.error_message or "")


async def test_engine_token_redacted_from_variables():
    async def leaky():
        captured_token = _ENGINE_TOKEN  # noqa: F841 - captured via variable tracing
        return "ok"

    result = await execute(_request(leaky))
    assert result.status == ExecutionStatus.SUCCESS
    assert _ENGINE_TOKEN not in json.dumps(result.variables or {})


async def test_no_engine_token_means_no_secret_registered():
    """A request without a pre-minted token (e.g. direct engine.execute()
    tests) must not register an empty string as a secret."""
    async def clean():
        return "ok"

    result = await execute(_request(clean, engine_token=None))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == "ok"
