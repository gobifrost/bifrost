"""Scale and safety quotas for the Agent Evaluation Studio.

Every bound is a module-level tunable with an environment override, matching
the durable runtime's settings pattern. Enforcement fails closed at the API
boundary (HTTP 429/422) and inside the simulator; nothing here silently
truncates fixtures or evidence.
"""

from __future__ import annotations

import json
import os
from typing import Any


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MAX_FIXTURE_BYTES = _int_env("BIFROST_EVAL_MAX_FIXTURE_BYTES", 256 * 1024)
"""Largest accepted case fixture (JSON-encoded) per case version."""

MAX_CASES_PER_SUITE = _int_env("BIFROST_EVAL_MAX_CASES_PER_SUITE", 200)
"""Accepted + draft case versions allowed per suite."""

MAX_DESIGNER_PROPOSALS_PER_REQUEST = _int_env(
    "BIFROST_EVAL_MAX_DESIGNER_PROPOSALS", 10
)
"""Designer proposals validated per drafts request."""

MAX_ACTIVE_EXECUTIONS_PER_ORG = _int_env(
    "BIFROST_EVAL_MAX_ACTIVE_EXECUTIONS_PER_ORG", 5
)
"""Queued/running/waiting executions allowed per organization."""

MAX_REPETITIONS_PER_CASE = 10
"""Repetitions per case side (mirrors the contract bound)."""

MAX_SYNTHETIC_TIMER_SECONDS = _int_env("BIFROST_EVAL_MAX_SYNTHETIC_TIMER_S", 300)
"""Wall-clock-free deterministic cap for synthetic ``sleep_until``."""

MAX_SIM_RECORDS_PER_RUN = _int_env("BIFROST_EVAL_MAX_SIM_RECORDS", 2000)
"""Append-only synthetic tool records kept per simulation session."""

MAX_EVAL_JOURNAL_BYTES = _int_env("BIFROST_EVAL_MAX_JOURNAL_BYTES", 4 * 1024 * 1024)
"""Bound for one evaluation evidence payload (assertion inputs)."""


class QuotaExceeded(Exception):
    """A Studio quota would be exceeded; the request fails closed."""

    def __init__(self, message: str, *, status_code: int = 429) -> None:
        super().__init__(message)
        self.status_code = status_code


def check_fixture_size(fixture: dict[str, Any]) -> int:
    size = len(json.dumps(fixture, default=str).encode())
    if size > MAX_FIXTURE_BYTES:
        raise QuotaExceeded(
            f"Fixture is {size} bytes; the per-case limit is {MAX_FIXTURE_BYTES}."
        )
    return size


def check_cases_per_suite(existing_count: int) -> None:
    if existing_count >= MAX_CASES_PER_SUITE:
        raise QuotaExceeded(
            f"Suite already holds {existing_count} case versions "
            f"(limit {MAX_CASES_PER_SUITE})."
        )


def check_designer_proposal_count(count: int) -> None:
    if count < 1 or count > MAX_DESIGNER_PROPOSALS_PER_REQUEST:
        raise QuotaExceeded(
            "Designer requests must propose 1-"
            f"{MAX_DESIGNER_PROPOSALS_PER_REQUEST} cases; got {count}.",
            status_code=422,
        )


def check_active_executions_per_org(active_count: int) -> None:
    if active_count >= MAX_ACTIVE_EXECUTIONS_PER_ORG:
        raise QuotaExceeded(
            f"Organization already has {active_count} active executions "
            f"(limit {MAX_ACTIVE_EXECUTIONS_PER_ORG}); wait or cancel one."
        )


def check_repetitions(repetitions: int | None) -> None:
    if repetitions is not None and not (1 <= repetitions <= MAX_REPETITIONS_PER_CASE):
        raise QuotaExceeded(
            f"Repetitions must be 1-{MAX_REPETITIONS_PER_CASE}; got {repetitions}.",
            status_code=422,
        )


def check_evidence_size(evidence: dict[str, Any]) -> int:
    size = len(json.dumps(evidence, default=str).encode())
    if size > MAX_EVAL_JOURNAL_BYTES:
        raise QuotaExceeded(
            f"Evaluation evidence is {size} bytes; the limit is "
            f"{MAX_EVAL_JOURNAL_BYTES}."
        )
    return size
