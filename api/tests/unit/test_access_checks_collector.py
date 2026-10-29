"""The request-scoped collector that report-only access checks note into."""

from __future__ import annotations

from uuid import UUID, uuid4

from shared import access_checks
from src.core.constants import SYSTEM_USER_ID
from src.core.principal import UserPrincipal
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority, RuntimeKind


def _runtime_principal(
    *,
    kind: RuntimeKind = "workflow_execution",
    credential_id: UUID | None = None,
    run_user_id: UUID | None = None,
    actor_id: UUID | None = None,
    workflow_id: UUID | None = None,
    powers: Powers | None = None,
    inline: bool = False,
) -> UserPrincipal:
    run_user_id = run_user_id or uuid4()
    actor_id = actor_id or uuid4()
    context = AuthorizationContext(
        user_id=run_user_id,
        home_organization_id=None,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset(),
    )
    principal = UserPrincipal(
        user_id=actor_id,
        email="runtime@example.test",
        organization_id=None,
        is_verified=True,
    )
    principal.runtime_authority = RuntimeAuthority(
        kind=kind,
        credential_id=credential_id or uuid4(),
        run_user=RunUser(run_user_id, context, None, is_active=True),
        powers=(
            Powers(WorkflowPermissionMode.FULL, ())
            if powers is None and kind == "workflow_execution" and not inline
            else powers
        ),
        acting_user_id=actor_id,
        organization_id=None,
        workflow_id=workflow_id,
        started_by_user_id=run_user_id if kind == "workflow_execution" else None,
        root_execution_id=uuid4() if kind == "workflow_execution" else None,
    )
    return principal


def _runtime_payload(principal: UserPrincipal) -> dict[str, str]:
    authority = principal.runtime_authority
    assert authority is not None
    return {"credential_kind": authority.kind, "sub": str(authority.credential_id)}


def test_note_outside_a_request_does_nothing() -> None:
    assert access_checks.current() is None
    access_checks.note("scope_switch", uuid4(), operation="x")
    assert access_checks.current() is None


def test_verified_engine_requests_collect_their_durable_lineage() -> None:
    run_user, workflow, execution = uuid4(), uuid4(), uuid4()
    principal = _runtime_principal(
        credential_id=execution,
        run_user_id=run_user,
        workflow_id=workflow,
    )
    token = access_checks.start_collecting(
        _runtime_payload(principal), principal=principal
    )
    try:
        target = uuid4()
        access_checks.note("scope_switch", target)
        collector = access_checks.current()
        assert collector is not None
        assert collector.run_user_id == run_user
        assert collector.workflow_id == workflow
        assert collector.execution_id == execution
        assert collector.cache == {
            access_checks.POLICY_RECORD_SUBJECT_CACHE_KEY: (
                principal.runtime_authority.run_user,
                principal.runtime_authority.powers,
            )
        }
        assert collector.notes == [access_checks.Note("scope_switch", target, {})]
    finally:
        access_checks.stop_collecting(token)


def test_runtime_access_checks_retain_verified_impersonation_provenance() -> None:
    from dataclasses import replace

    operator, session = uuid4(), uuid4()
    principal = _runtime_principal()
    assert principal.runtime_authority is not None
    principal.runtime_authority = replace(
        principal.runtime_authority,
        impersonated_by_user_id=operator,
        impersonation_session_id=session,
    )
    token = access_checks.start_collecting(_runtime_payload(principal), principal=principal)
    try:
        collector = access_checks.current()
        assert collector is not None
        assert collector.impersonated_by_user_id == operator
        assert collector.impersonation_session_id == session
    finally:
        access_checks.stop_collecting(token)
    assert access_checks.current() is None


def test_collect_run_allows_hook_tests_to_exercise_missing_lineage() -> None:
    token = access_checks.collect_run(uuid4(), None, None)
    try:
        collector = access_checks.current()
        assert collector is not None and collector.run_user_id is None
    finally:
        access_checks.stop_collecting(token)


def test_verified_agent_credentials_carry_a_run_user_and_are_collected() -> None:
    run_user, agent_run = uuid4(), uuid4()
    principal = _runtime_principal(
        kind="agent_run",
        credential_id=agent_run,
        run_user_id=run_user,
        powers=None,
    )
    token = access_checks.start_collecting(
        _runtime_payload(principal), principal=principal
    )
    try:
        collector = access_checks.current()
        assert collector is not None
        assert collector.run_user_id == run_user
        assert collector.execution_id is None
        assert collector.agent_run_id == agent_run
        assert collector.direct is True
    finally:
        access_checks.stop_collecting(token)


def test_verified_inline_runtime_has_no_workflow_power_and_collects_directly() -> None:
    principal = _runtime_principal(inline=True)
    token = access_checks.start_collecting(
        _runtime_payload(principal), principal=principal
    )
    try:
        collector = access_checks.current()
        assert collector is not None and collector.direct is True
    finally:
        access_checks.stop_collecting(token)


def test_anonymous_system_and_unverified_runtime_payloads_are_not_collected() -> None:
    assert access_checks.start_collecting(None) is None
    assert access_checks.start_collecting(
        {"credential_kind": "workflow_execution", "sub": str(uuid4())}
    ) is None
    assert access_checks.start_collecting(
        {"sub": str(uuid4()), "engine_execution_id": str(uuid4())}
    ) is None
    assert access_checks.start_collecting(
        {"credential_kind": "workflow_execution", "sub": str(uuid4())},
        principal=UserPrincipal(
            user_id=uuid4(), email="forged@example.test", organization_id=None
        ),
    ) is None
    assert access_checks.start_collecting({"sub": SYSTEM_USER_ID, "embed": True}) is None


def test_a_persons_request_collects_permissions_scope_switches_and_run_as_only() -> None:
    person, org, colleague = uuid4(), uuid4(), uuid4()
    token = access_checks.start_collecting(
        {"sub": str(person), "org_id": str(uuid4())}
    )
    try:
        access_checks.note("scope_switch", org)
        access_checks.note("secret", org, kind="config", name="k")
        access_checks.note_power("agents.read", org, subject="agent:1")
        access_checks.note("run_as", org, run_as_user_id=str(colleague), enforced=True)
        collector = access_checks.current()
        expected = [
            access_checks.Note("scope_switch", org, {}),
            access_checks.Note(
                "permission", org, {"permission": "agents.read", "subject": "agent:1"}
            ),
            access_checks.Note(
                "run_as", org, {"run_as_user_id": str(colleague), "enforced": True}
            ),
        ]
        assert collector is not None
        assert (collector.direct, collector.run_user_id, collector.execution_id) == (
            True,
            person,
            None,
        )
        assert collector.notes == expected
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


def test_renew_discards_verified_runtime_policy_recording_facts() -> None:
    principal = _runtime_principal()
    token = access_checks.start_collecting(_runtime_payload(principal), principal=principal)
    try:
        first = access_checks.renew()
        fresh = access_checks.current()
        assert first is not None
        assert access_checks.POLICY_RECORD_SUBJECT_CACHE_KEY in first.cache
        assert fresh is not None and fresh.cache == {}
    finally:
        access_checks.stop_collecting(token)


def test_a_persons_collector_keeps_impersonation_provenance() -> None:
    person, operator, session = uuid4(), uuid4(), uuid4()
    token = access_checks.start_collecting(
        {
            "sub": str(person),
            "impersonated_by_user_id": str(operator),
            "impersonation_session_id": str(session),
        }
    )
    try:
        collector = access_checks.current()
        assert collector is not None
        assert collector.impersonated_by_user_id == operator
        assert collector.impersonation_session_id == session
    finally:
        access_checks.stop_collecting(token)


def test_malformed_impersonation_provenance_does_not_skip_access_checks() -> None:
    token = access_checks.start_collecting(
        {
            "sub": str(uuid4()),
            "impersonated_by_user_id": "not-a-uuid",
            "impersonation_session_id": "not-a-uuid",
        }
    )
    try:
        collector = access_checks.current()
        assert collector is not None
        assert collector.impersonated_by_user_id is None
        assert collector.impersonation_session_id is None
    finally:
        access_checks.stop_collecting(token)


def test_a_global_object_is_launched_where_the_caller_is() -> None:
    contoso, fabrikam = uuid4(), uuid4()
    token = access_checks.collect_person(uuid4())
    try:
        access_checks.note_launch(
            "apps.readbasic", fabrikam, contoso, subject="application:1"
        )
        access_checks.note_launch(
            "apps.readbasic", None, contoso, subject="application:2"
        )
        collector = access_checks.current()
        assert collector is not None
        assert [note.target for note in collector.notes] == [fabrikam, contoso]
    finally:
        access_checks.stop_collecting(token)


def test_a_note_after_the_collector_closed_is_not_kept() -> None:
    token = access_checks.collect_run(uuid4(), uuid4(), uuid4())
    try:
        collector = access_checks.current()
        assert collector is not None
        collector.closed = True
        access_checks.note("secret", None, kind="config", name="k")
        assert collector.notes == []
    finally:
        access_checks.stop_collecting(token)


def test_suspending_collection_drops_notes_and_restores_after_an_error() -> None:
    token = access_checks.collect_person(uuid4())
    try:
        collector = access_checks.current()
        assert collector is not None

        try:
            with access_checks.suspend_collecting():
                assert access_checks.current() is None
                access_checks.note_power("users.impersonate", None, subject="user:operator")
                raise RuntimeError("revalidation denied")
        except RuntimeError as exc:
            assert str(exc) == "revalidation denied"

        assert access_checks.current() is collector
        assert collector.notes == []
        access_checks.note("scope_switch", None, name="restored")
        assert collector.notes == [access_checks.Note("scope_switch", None, {"name": "restored"})]
    finally:
        access_checks.stop_collecting(token)


def test_a_check_that_failed_to_compute_is_noted_as_a_gap() -> None:
    token = access_checks.collect_run(uuid4(), uuid4(), uuid4())
    try:
        access_checks.note_failure("entry", None, TimeoutError())
        collector = access_checks.current()
        assert collector is not None
        assert collector.notes == [
            access_checks.Note("entry", None, {"gap": "observer_error:TimeoutError"})
        ]
    finally:
        access_checks.stop_collecting(token)


def test_a_persons_verified_agent_bridge_is_collected_as_theirs() -> None:
    person = uuid4()
    principal = _runtime_principal(
        kind="agent_run",
        run_user_id=person,
        actor_id=person,
        powers=None,
    )
    token = access_checks.start_collecting(
        _runtime_payload(principal), principal=principal
    )
    try:
        collector = access_checks.current()
        assert collector is not None and collector.direct and collector.run_user_id == person
        assert collector.is_run is True
    finally:
        access_checks.stop_collecting(token)


def test_agent_attribution_survives_renew_without_changing_the_caller() -> None:
    person, run = uuid4(), uuid4()
    principal = _runtime_principal(
        kind="agent_run",
        credential_id=run,
        run_user_id=person,
        actor_id=person,
        powers=None,
    )
    token = access_checks.start_collecting(
        _runtime_payload(principal), surface="mcp", principal=principal
    )
    try:
        first = access_checks.renew()
        fresh = access_checks.current()
        assert first is not None and fresh is not None
        for collector in (first, fresh):
            assert collector.run_user_id == person
            assert collector.direct is True
            assert collector.is_run is True
            assert collector.execution_id is None
            assert collector.surface == "agent"
            assert collector.agent_run_id == run
    finally:
        access_checks.stop_collecting(token)
