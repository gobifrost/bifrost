"""Elevated branches note the named permission that replaces their flag, on
the branch the flag unlocks and nowhere else (report-only)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from shared import access_checks
from shared.home import can_edit_collection, can_read_collection
from shared.sdk_video import can_read_platform_job
from shared.table_document_writes import TableWriteForbidden, resolve_attribution
from src.core.principal import UserPrincipal
from src.models.contracts.agents import AgentAccessLevel, AgentCreate, AgentUpdate
from src.models.orm.agents import Agent
from src.models.orm.home import HomeCollection
from src.models.orm.platform_jobs import PlatformJob
from src.routers.websocket import can_access_agent_run, can_access_execution, can_access_service
from src.services.agent_write_policy import needs_admin_to_create, needs_admin_to_update
from src.services.authorization.enforce import require_unprotected
from src.services.authorization.privilege import may_change_role_assignment
from src.services.mcp_server.tools.code_editor import _check_read_scope, _check_write_scope
from tests.helpers.authorization import admin_caller

CONTOSO = UUID("00000000-0000-0000-0000-00000000c001")
FABRIKAM = UUID("00000000-0000-0000-0000-00000000f001")


@pytest.fixture
def notes():
    token = access_checks.collect_person(uuid4())
    try:
        collector = access_checks.current()
        assert collector is not None
        yield collector.notes
    finally:
        access_checks.stop_collecting(token)


def _powers(notes) -> list[tuple[str, object]]:
    return [(note.facts["permission"], note.target) for note in notes]


def _person(*, admin: bool = False, provider: bool = False, org: UUID = CONTOSO) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(), email="p@contoso.example", organization_id=org, is_superuser=admin, is_provider_org=provider
    )


def test_editing_a_shared_collection_notes_whose_it_is(notes) -> None:
    admin = _person(admin=True)
    own = HomeCollection(id=uuid4(), owner_id=admin.user_id, organization_id=FABRIKAM, shared=True)
    theirs = HomeCollection(id=uuid4(), owner_id=uuid4(), organization_id=FABRIKAM, shared=True)
    private = HomeCollection(id=uuid4(), owner_id=admin.user_id, organization_id=None, shared=False)

    assert can_edit_collection(own, admin) and can_edit_collection(theirs, admin) and can_edit_collection(private, admin)

    assert _powers(notes) == [("home.readwrite", FABRIKAM), ("home.readwrite.all", FABRIKAM)]


def test_reading_a_collection_notes_only_what_the_flag_admits(notes) -> None:
    admin = _person(admin=True)
    home = HomeCollection(id=uuid4(), owner_id=uuid4(), organization_id=CONTOSO, shared=True)
    elsewhere = HomeCollection(id=uuid4(), owner_id=uuid4(), organization_id=FABRIKAM, shared=True)

    assert can_read_collection(home, admin) and can_read_collection(elsewhere, admin)
    assert not can_read_collection(elsewhere, _person())

    assert _powers(notes) == [("home.read.all", FABRIKAM)]


def test_someone_elses_platform_job_notes_read_or_cancel(notes) -> None:
    admin = _person(admin=True)
    job = PlatformJob(id=uuid4(), requested_by_user_id=str(uuid4()), organization_id=FABRIKAM)
    own = PlatformJob(id=uuid4(), requested_by_user_id=str(admin.user_id), organization_id=FABRIKAM)

    assert can_read_platform_job(job, admin) and can_read_platform_job(job, admin, cancel=True)
    assert can_read_platform_job(own, admin) and not can_read_platform_job(job, _person())

    assert _powers(notes) == [("platformjobs.read.all", FABRIKAM), ("platformjobs.readwrite.all", FABRIKAM)]


def test_an_admin_owners_update_needs_the_flag_only_for_fields_an_owner_could_not_set() -> None:
    owner = uuid4()
    agent = Agent(id=uuid4(), access_level=AgentAccessLevel.PRIVATE, owner_user_id=owner, organization_id=CONTOSO)

    assert not needs_admin_to_update(agent, AgentUpdate(name="Renamed"))
    assert needs_admin_to_update(agent, AgentUpdate(max_iterations=5))
    assert needs_admin_to_update(agent, AgentUpdate(access_level=AgentAccessLevel.AUTHENTICATED))
    assert needs_admin_to_update(agent, AgentUpdate(role_ids=[]))
    assert needs_admin_to_update(agent, AgentUpdate(organization_id=FABRIKAM))


def test_an_admin_create_needs_the_flag_only_beyond_a_private_home_agent() -> None:
    private = AgentCreate(name="Mine", system_prompt="Hi", access_level=AgentAccessLevel.PRIVATE, organization_id=CONTOSO)
    shared = AgentCreate(name="Ours", system_prompt="Hi", access_level=AgentAccessLevel.AUTHENTICATED)

    assert not needs_admin_to_create(private, caller_org_id=CONTOSO)
    assert needs_admin_to_create(shared, caller_org_id=CONTOSO)


def test_the_grant_ceiling_notes_only_what_an_operator_could_not_assign(notes) -> None:
    role = uuid4()

    assert may_change_role_assignment(
        actor_is_platform_admin=True, role_id=role, role_permissions=frozenset(), target_permissions=()
    )
    assert may_change_role_assignment(
        actor_is_platform_admin=True, role_id=role, role_permissions=frozenset({"users.read"}), target_permissions=()
    )

    assert _powers(notes) == [("privilegedaccess.readwrite", None)]


def test_a_privileged_target_notes_privileged_access(notes) -> None:
    caller = admin_caller()

    require_unprotected(caller, target_is_privileged=False)
    require_unprotected(caller, target_is_privileged=True)

    assert _powers(notes) == [("privilegedaccess.readwrite", None)]


def test_an_attribution_override_notes_table_attribution(notes) -> None:
    admin = _person(admin=True)

    assert resolve_attribution(admin, None, None) == (str(admin.user_id), str(admin.user_id))
    resolve_attribution(admin, "someone", None)
    with pytest.raises(TableWriteForbidden):
        resolve_attribution(_person(), "someone", None)

    assert _powers(notes) == [("tableattribution.readwrite", CONTOSO)]


def test_repository_scope_notes_read_or_write_for_bypass_only(notes) -> None:
    provider = SimpleNamespace(is_platform_admin=False, is_provider_org=True)
    customer = SimpleNamespace(is_platform_admin=False, is_provider_org=False)

    assert _check_read_scope(provider) is None and _check_write_scope(provider, "workflows/x.py") is None
    assert _check_read_scope(customer) is not None

    assert _powers(notes) == [("repository.read", None), ("repository.readwrite", None)]


async def test_websocket_subscriptions_note_the_object_resolved_later(notes) -> None:
    admin, member = _person(admin=True), _person(provider=True)
    execution, run = uuid4(), uuid4()

    assert await can_access_execution(admin, str(execution))
    assert await can_access_agent_run(member, str(run))
    assert await can_access_execution(admin, "not-an-id")
    assert await can_access_service(admin, str(uuid4()))

    assert [(note.facts["permission"], note.facts.get("owned"), note.facts.get("object_id")) for note in notes] == [
        ("executions.read.all", "execution", str(execution)),
        ("agentruns.read.all", "agent_run", str(run)),
        ("platform.read", None, None),
    ]
    assert notes[0].facts["actor"] == str(admin.user_id)


def test_an_owned_object_carries_who_acted() -> None:
    token = access_checks.collect_person(uuid4())
    try:
        workspace, actor = uuid4(), uuid4()
        access_checks.note_power(
            "artifacts.readwrite.all", access_checks.Owned("artifact_workspace", workspace, actor), subject="w"
        )
        collector = access_checks.current()
        assert collector is not None
        [note] = collector.notes
        assert note.target is None
        assert note.facts == {
            "permission": "artifacts.readwrite.all",
            "subject": "w",
            "owned": "artifact_workspace",
            "object_id": str(workspace),
            "actor": str(actor),
        }
    finally:
        access_checks.stop_collecting(token)



async def test_websocket_checks_are_judged_in_the_background(monkeypatch) -> None:
    import asyncio

    from src.routers import websocket

    release = asyncio.Event()
    judged: list[str] = []

    async def slow_flush(collector, *, operation, route):
        await release.wait()
        judged.append(operation)

    monkeypatch.setattr(websocket, "flush_detached", slow_flush)
    token = access_checks.collect_person(uuid4())
    try:
        access_checks.note_power("platform.read", None, subject="channel:agent-runs")
        websocket._judge_access_checks("/ws/connect")
        assert judged == [] and len(websocket._judging) == 1
        [task] = websocket._judging
        release.set()
        judged_task = await task
        assert judged_task is None
    finally:
        access_checks.stop_collecting(token)

    assert judged == ["WS /ws/connect"] and websocket._judging == set()
