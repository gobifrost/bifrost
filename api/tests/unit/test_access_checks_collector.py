"""The request-scoped collector that report-only access checks note into."""

from __future__ import annotations

from uuid import uuid4

from shared import access_checks


def _engine_payload(**extra) -> dict:
    return {"sub": str(uuid4()), "is_superuser": True, "engine_execution_id": str(uuid4()), **extra}


def test_note_outside_a_request_does_nothing() -> None:
    assert access_checks.current() is None
    access_checks.note("scope_switch", uuid4(), operation="x")
    assert access_checks.current() is None


def test_engine_requests_collect_notes_with_their_lineage() -> None:
    run_user, workflow = uuid4(), uuid4()
    payload = _engine_payload(engine_run_user_id=str(run_user), engine_workflow_id=str(workflow))
    token = access_checks.start_collecting(payload)
    try:
        target = uuid4()
        access_checks.note("scope_switch", target)
        collector = access_checks.current()
        assert collector is not None
        assert collector.run_user_id == run_user
        assert collector.workflow_id == workflow
        assert str(collector.execution_id) == payload["engine_execution_id"]
        assert collector.notes == [access_checks.Note("scope_switch", target, {})]
    finally:
        access_checks.stop_collecting(token)
    assert access_checks.current() is None


def test_an_engine_token_without_lineage_collects_with_no_run_user() -> None:
    token = access_checks.start_collecting(_engine_payload())
    try:
        collector = access_checks.current()
        assert collector is not None and collector.run_user_id is None
    finally:
        access_checks.stop_collecting(token)


def test_bridge_tokens_carry_a_run_user_and_are_collected() -> None:
    run_user = uuid4()
    token = access_checks.start_collecting({"sub": str(uuid4()), "engine_run_user_id": str(run_user)})
    try:
        collector = access_checks.current()
        assert collector is not None
        assert collector.run_user_id == run_user and collector.execution_id is None
    finally:
        access_checks.stop_collecting(token)


def test_people_and_service_tokens_are_not_collected() -> None:
    assert access_checks.start_collecting(None) is None
    assert access_checks.start_collecting({"sub": str(uuid4()), "org_id": str(uuid4())}) is None
    service = {"sub": str(uuid4()), "is_superuser": False, "engine_execution_id": str(uuid4()), "service_id": "s"}
    assert access_checks.start_collecting(service) is None


def test_a_note_after_the_collector_closed_is_not_kept() -> None:
    token = access_checks.start_collecting(_engine_payload(engine_run_user_id=str(uuid4())))
    try:
        collector = access_checks.current()
        assert collector is not None
        collector.closed = True
        access_checks.note("secret", None, kind="config", name="k")
        assert collector.notes == []
    finally:
        access_checks.stop_collecting(token)
