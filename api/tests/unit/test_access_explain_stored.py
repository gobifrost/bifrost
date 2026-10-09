"""Explaining access checks stored without a trace or without inputs.

Older and worker-written ``access.check`` rows may lack ``details.trace``
(then is unknown) or ``details.inputs`` too (now can't be judged).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.audit import AuditLog
from src.services.access_explain import rerun, stored_trace

_TRACE = {
    "outcome": "failure",
    "enforced": False,
    "steps": [{"key": "target", "label": "Target in reach", "status": "stopped", "reason": "outside", "facts": {}}],
}


def _row(details: dict) -> AuditLog:
    return AuditLog(action="access.check", resource_type="scope_switch", outcome="failure", user_id=uuid4(), details=details)


def test_stored_trace_is_the_trace_the_writer_kept() -> None:
    trace = stored_trace(_row({"trace": _TRACE, "inputs": {}}))

    assert trace is not None and (trace.outcome, trace.steps[0].key) == ("failure", "target")


def test_no_stored_trace_is_none() -> None:
    assert stored_trace(_row({"workflow_id": None, "inputs": {"operation": "GET /api/tables", "target": None}})) is None


def test_no_stored_inputs_cannot_be_judged_now() -> None:
    # Nothing is read: the row alone says it can't be re-run.
    assert asyncio.run(rerun(AsyncSession(), _row({"workflow_id": None}))) == (None, "inputs_not_stored")
