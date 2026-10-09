"""The request-scoped collector that report-only access checks note into."""

from __future__ import annotations

from uuid import uuid4

from shared import access_checks
from src.core.constants import SYSTEM_USER_ID


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


def test_anonymous_and_system_subject_tokens_are_not_collected() -> None:
    assert access_checks.start_collecting(None) is None
    service = {"sub": SYSTEM_USER_ID, "is_superuser": False, "engine_execution_id": str(uuid4()), "service_id": "s"}
    assert access_checks.start_collecting(service) is None
    embed = {"sub": SYSTEM_USER_ID, "is_superuser": False, "embed": True}
    assert access_checks.start_collecting(embed) is None


def test_a_persons_request_collects_named_permissions_only() -> None:
    person, org = uuid4(), uuid4()
    token = access_checks.start_collecting({"sub": str(person), "org_id": str(uuid4())})
    try:
        access_checks.note("scope_switch", org)
        access_checks.note("secret", org, kind="config", name="k")
        access_checks.note_power("agents.read", org, subject="agent:1")
        collector = access_checks.current()
        assert collector is not None
        assert (collector.direct, collector.run_user_id, collector.execution_id) == (True, person, None)
        assert collector.notes == [
            access_checks.Note("permission", org, {"permission": "agents.read", "subject": "agent:1"})
        ]
    finally:
        access_checks.stop_collecting(token)


def test_renew_hands_back_the_notes_and_collects_afresh() -> None:
    person = uuid4()
    token = access_checks.collect_person(person)
    try:
        access_checks.note_power("platform.read", None, subject="channel:agent-runs")
        first = access_checks.renew()
        assert first is not None and len(first.notes) == 1
        fresh = access_checks.current()
        assert fresh is not None and fresh is not first
        assert (fresh.notes, fresh.direct, fresh.run_user_id) == ([], True, person)
    finally:
        access_checks.stop_collecting(token)
    assert access_checks.current() is None


def test_a_global_object_is_launched_where_the_caller_is() -> None:
    contoso, fabrikam = uuid4(), uuid4()
    assert access_checks.launch_target(fabrikam, contoso) == fabrikam
    assert access_checks.launch_target(None, contoso) == contoso


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


def test_a_check_that_failed_to_compute_is_noted_as_a_gap() -> None:
    token = access_checks.start_collecting(_engine_payload(engine_run_user_id=str(uuid4())))
    try:
        access_checks.note_failure("entry", None, TimeoutError())
        collector = access_checks.current()
        assert collector is not None
        assert collector.notes == [access_checks.Note("entry", None, {"gap": "observer_error:TimeoutError"})]
    finally:
        access_checks.stop_collecting(token)


def test_a_persons_own_bridge_call_is_collected_as_theirs() -> None:
    """The bridge acts as the person who started the agent: their own request."""
    person = uuid4()
    token = access_checks.start_collecting({"sub": str(person), "engine_run_user_id": str(person)})
    try:
        collector = access_checks.current()
        assert collector is not None and collector.direct and collector.run_user_id == person
    finally:
        access_checks.stop_collecting(token)
