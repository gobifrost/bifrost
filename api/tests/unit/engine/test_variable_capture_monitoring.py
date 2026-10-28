"""Variable capture observes only the workflow's final return.

The engine records a workflow's (or a script ``main``'s) local variables
when it returns. That capture must not install a per-line or per-call
Python tracer over the workflow's code: doing so made CPU-bound workflows
roughly ten times slower than the same code run as plain Python.
"""

import asyncio
import base64
import functools
import sys

from src.models.enums import ExecutionStatus
from src.sdk.context import Caller, Organization
from src.services.execution.engine import ExecutionRequest, execute


def _request(**overrides) -> ExecutionRequest:
    args = {
        "execution_id": "11111111-1111-4111-8111-111111111111",
        "caller": Caller(
            user_id="00000000-0000-0000-0000-000000000001",
            email="capture@example.com",
            name="Capture Tester",
        ),
        "organization": Organization(
            id="22222222-2222-4222-8222-222222222222",
            name="Acme",
            is_active=True,
        ),
        "name": "capture_workflow",
    }
    args.update(overrides)
    return ExecutionRequest(**args)


async def test_captures_locals_of_workflow_without_awaits():
    async def straight_line_workflow(count: int):
        doubled = count * 2
        labels = [f"item-{i}" for i in range(count)]
        return {"doubled": doubled, "labels": labels}

    result = await execute(_request(func=straight_line_workflow, parameters={"count": 3}))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.variables == {
        "count": 3,
        "doubled": 6,
        "labels": ["item-0", "item-1", "item-2"],
    }


async def test_captures_locals_at_final_return_of_async_workflow():
    async def awaiting_workflow():
        stage = "started"
        await asyncio.sleep(0)
        stage = f"{stage}->finished"
        total = 42
        return f"{stage}:{total}"

    result = await execute(_request(func=awaiting_workflow))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == "started->finished:42"
    assert result.variables == {"stage": "started->finished", "total": 42}


async def test_captures_locals_at_early_return():
    async def early_return_workflow(flag: bool):
        reason = "short-circuit"
        if flag:
            return reason
        unreachable = "never assigned"
        return unreachable

    result = await execute(_request(func=early_return_workflow, parameters={"flag": True}))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == "short-circuit"
    assert result.variables == {"flag": True, "reason": "short-circuit"}


async def test_captures_locals_of_function_under_wraps_decorator():
    def retrying(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            attempt = 1
            assert attempt
            return await fn(*args, **kwargs)

        return wrapper

    @retrying
    async def wrapped_workflow(count: int):
        doubled = count * 2
        return doubled

    result = await execute(_request(func=wrapped_workflow, parameters={"count": 4}))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == 8
    assert result.variables == {"count": 4, "doubled": 8}


async def test_await_suspension_does_not_capture_locals():
    async def suspending_workflow():
        transient = "only alive across the await"
        await asyncio.sleep(0)
        del transient
        kept = "present at return"
        return kept

    result = await execute(_request(func=suspending_workflow))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.variables == {"kept": "present at return"}


async def test_captures_locals_of_script_main():
    script = 'greeting = "hello"\nresult = {"greeting": greeting}\n'

    result = await execute(
        _request(code=base64.b64encode(script.encode()).decode(), name="capture_script")
    )

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == {"greeting": "hello"}
    assert result.variables == {"greeting": "hello", "result": {"greeting": "hello"}}


async def test_existing_tracer_keeps_receiving_events_and_stays_installed():
    async def debugged_workflow():
        note = "visible to the debugger"
        return note

    seen_codes = []

    def recording_tracer(frame, event, arg):
        if event == "call":
            seen_codes.append(frame.f_code)
        return None

    original_tracer = sys.gettrace()
    sys.settrace(recording_tracer)
    try:
        result = await execute(_request(func=debugged_workflow))
        tracer_after_execution = sys.gettrace()
    finally:
        sys.settrace(original_tracer)

    assert result.status == ExecutionStatus.SUCCESS
    assert debugged_workflow.__code__ in seen_codes
    assert tracer_after_execution is recording_tracer
    assert result.variables == {"note": "visible to the debugger"}


async def test_workflow_code_runs_without_line_or_call_tracing():
    monitoring = sys.monitoring
    tools_before = {t for t in range(6) if monitoring.get_tool(t) is not None}
    outer_tracer = sys.gettrace()
    observed = {}

    def helper(value):
        return value + 1

    async def looping_workflow():
        observed["tracer"] = sys.gettrace()
        added_tools = [
            t for t in range(6)
            if monitoring.get_tool(t) is not None and t not in tools_before
        ]
        observed["global_events"] = [monitoring.get_events(t) for t in added_tools]
        observed["workflow_events"] = [
            monitoring.get_local_events(t, looping_workflow.__code__) for t in added_tools
        ]
        observed["helper_events"] = [
            monitoring.get_local_events(t, helper.__code__) for t in added_tools
        ]
        total = 0
        for i in range(10_000):
            total = helper(total)
        return total

    result = await execute(_request(func=looping_workflow))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.variables["total"] == 10_000
    assert observed["tracer"] is outer_tracer
    assert observed["global_events"] == [0]
    assert observed["workflow_events"] == [monitoring.events.PY_RETURN]
    assert observed["helper_events"] == [0]
    assert {t for t in range(6) if monitoring.get_tool(t) is not None} == tools_before


async def test_monitoring_tool_id_is_freed_after_failing_workflow():
    monitoring = sys.monitoring
    tools_before = {t for t in range(6) if monitoring.get_tool(t) is not None}

    async def failing_workflow():
        marker = "captured from the traceback"
        await asyncio.sleep(0)
        raise ValueError(marker)

    result = await execute(_request(func=failing_workflow))

    assert result.status == ExecutionStatus.FAILED
    assert result.variables == {"marker": "captured from the traceback"}
    assert {t for t in range(6) if monitoring.get_tool(t) is not None} == tools_before
