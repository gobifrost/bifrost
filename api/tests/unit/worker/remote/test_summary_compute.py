"""Wire contracts for credential-free remote agent summaries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from shared.agent_summary_compute import build_summary_completion_request
from shared.agent_summary_compute_wire import (
    SummaryComputeWireError,
    summary_compute_result_from_json,
    summary_completion_request_to_json,
    summary_input_from_json,
)
from src.worker.remote.summary_compute import run_summary_compute


class _Channel:
    def __init__(self, reply: object | BaseException) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def request(self, op: str, payload: dict[str, object], *, stream: bool = False) -> object:
        self.calls.append((op, payload))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def _plan() -> dict[str, object]:
    return {
        "surface": "summary",
        "claim_id": str(uuid4()),
        "input": {
            "agent_name": "Ticket Agent",
            "agent_system_prompt": "Resolve tickets.",
            "run_input": {"ticket": "T-1"},
            "run_output": {"resolved": True},
            "model": "summary-model",
        },
    }


def _completion(**overrides: object) -> dict[str, object]:
    return {
        "content": '{"asked":"Fix T-1","did":"Resolved it","answered":"Done","confidence":0.8,"confidence_reason":"evidence","metadata":{"ticket":"T-1"}}',
        "model": "summary-model",
        "input_tokens": 12,
        "output_tokens": 8,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "provider_cost": "0.0012",
        **overrides,
    }


@pytest.mark.asyncio
async def test_summary_compute_forwards_the_exact_canonical_completion_request() -> None:
    channel = _Channel(_completion())

    output = await run_summary_compute(_plan(), channel)

    assert channel.calls[0][0] == "summary_model_request"
    request = channel.calls[0][1]["request"]
    assert request == summary_completion_request_to_json(
        build_summary_completion_request(summary_input_from_json(_plan()["input"]))
    )
    assert output["failure"] is None
    restored = summary_compute_result_from_json(output)
    assert restored.fields is not None
    assert restored.fields.asked == "Fix T-1"
    assert restored.completion is not None
    assert restored.completion.provider_cost == "0.0012"


@pytest.mark.asyncio
async def test_summary_compute_returns_compute_failure_for_callback_or_strict_wire_error() -> None:
    callback_failure = await run_summary_compute(
        _plan(), _Channel(RuntimeError("network unavailable"))
    )
    malformed = await run_summary_compute(
        _plan(), _Channel(_completion(input_tokens=True))
    )

    callback_result = summary_compute_result_from_json(callback_failure)
    malformed_result = summary_compute_result_from_json(malformed)
    assert callback_result.failure is not None
    assert callback_result.failure.kind == "provider"
    assert malformed_result.failure is not None
    assert malformed_result.failure.kind == "provider"
    assert "SummaryComputeWireError" in malformed_result.failure.error


def test_summary_wire_rejects_nonfinite_or_oversized_json() -> None:
    with pytest.raises(SummaryComputeWireError, match="finite"):
        summary_input_from_json({
            "agent_name": "agent",
            "agent_system_prompt": "prompt",
            "run_input": float("nan"),
            "run_output": {},
            "model": "model",
        })
    with pytest.raises(SummaryComputeWireError, match="byte limit"):
        summary_compute_result_from_json({
            "fields": None,
            "completion": None,
            "failure": None,
            "raw_content": "x" * (8 * 1024 * 1024),
        })


def test_summary_compute_imports_no_settings_database_or_provider_modules() -> None:
    api_root = Path(__file__).resolve().parents[4]
    code = """
import json
import sys
import shared.agent_summary_compute_wire  # noqa: F401
import src.worker.remote.summary_compute  # noqa: F401
forbidden = ('src.config', 'src.core.database', 'src.models.orm', 'sqlalchemy', 'pydantic_ai.providers')
print(json.dumps(sorted(name for name in sys.modules if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=api_root, capture_output=True,
        env={}, text=True, timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1]) == []
