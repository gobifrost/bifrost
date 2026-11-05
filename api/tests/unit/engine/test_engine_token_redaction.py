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
from io import StringIO
import logging

import pytest

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
        "name": "engine_token_workflow",
        "engine_token": _ENGINE_TOKEN,
    }
    args.update(overrides)
    return ExecutionRequest(**args)


async def test_engine_token_redacted_from_result():
    async def uses_engine_token():
        import os
        return {"token": os.environ.get("BIFROST_ACCESS_TOKEN", _ENGINE_TOKEN)}

    result = await execute(_request(uses_engine_token))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["token"] == "[REDACTED]"


async def test_engine_token_redacted_from_logs():
    import logging

    async def uses_engine_token():
        logging.getLogger(__name__).info("using token %s", _ENGINE_TOKEN)
        return "ok"

    result = await execute(_request(uses_engine_token))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.logs, "expected the log line to be captured"
    assert _ENGINE_TOKEN not in json.dumps(result.logs)


async def test_engine_token_redacted_from_error_message():
    async def uses_engine_token():
        raise RuntimeError(f"auth failed for {_ENGINE_TOKEN}")

    result = await execute(_request(uses_engine_token))
    assert result.status == ExecutionStatus.FAILED
    assert _ENGINE_TOKEN not in (result.error_message or "")


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError])
async def test_unknown_header_credential_is_redacted_from_failed_workflow_outputs(error_type):
    """Both wrapped workflow errors and the outer error path scrub their sinks."""
    async def fails_with_header():
        raise error_type("Authorization: Bearer workflow-exception-secret")

    stream = StringIO()
    console = logging.StreamHandler(stream)
    console.setFormatter(logging.Formatter("[worker] %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.addHandler(console)
    try:
        result = await execute(_request(fails_with_header, engine_token=None))
    finally:
        root.removeHandler(console)

    assert result.status == ExecutionStatus.FAILED
    assert result.error_type == error_type.__name__
    assert "workflow-exception-secret" not in (result.error_message or "")
    assert "workflow-exception-secret" not in json.dumps(result.logs)
    assert "workflow-exception-secret" not in stream.getvalue()
    assert "[REDACTED]" in (result.error_message or "")


async def test_engine_token_redacted_from_variables():
    async def holds_token_in_local():
        captured_token = _ENGINE_TOKEN  # traced as a local variable
        return "ok" if captured_token else "missing"

    result = await execute(_request(holds_token_in_local))
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


async def test_a_token_refreshed_while_the_workflow_loads_is_redacted(monkeypatch):
    """An epoch change between dispatch and the child's module fetch: the
    fetch is challenged and refreshes before the execution context exists.
    The refreshed token must still be redacted from the run's output."""
    import httpx

    import bifrost.client as client_mod
    from bifrost._context import clear_execution_context

    api = "http://api.loading.test"
    refreshed = "refreshed-while-loading-abcdefghijk"
    monkeypatch.setenv("BIFROST_API_URL", api)
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", _ENGINE_TOKEN)
    monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "run-refresh-token-abcdefghijklmnop")
    client_mod._reset_refresh_coordinators_for_tests()
    clear_execution_context()
    # The run's log delivery builds and caches an SDK client from these
    # process credentials; never leave it behind for later tests.
    monkeypatch.setattr(client_mod._thread_local, "bifrost_client", None, raising=False)

    class RefreshEndpoint:
        def __init__(self, *, base_url, timeout):
            assert base_url == api

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, *, json):
            return httpx.Response(
                200,
                json={"access_token": refreshed, "refresh_token": json["refresh_token"], "expires_in": 3600},
                request=httpx.Request("POST", f"{api}{path}"),
            )

    with monkeypatch.context() as patched:
        patched.setattr(client_mod.httpx, "AsyncClient", RefreshEndpoint)
        # The cold module fetch refreshes synchronously, before execute()
        # creates the execution context.
        assert client_mod._refresh_connection_access_token_sync(api, _ENGINE_TOKEN) == refreshed

    async def logs_its_current_token():
        import os

        token = os.environ["BIFROST_ACCESS_TOKEN"]
        logging.getLogger(__name__).info("current token %s", token)
        return {"token": token}

    result = await execute(_request(logs_its_current_token))
    client_mod._reset_refresh_coordinators_for_tests()

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["token"] == "[REDACTED]"
    assert result.logs
    assert refreshed not in json.dumps(result.logs)
