"""Focused unit tests for the SDK agent runs shared service.

Covers ``shared.sdk_agent_runs`` (enqueue success/paused/inactive
Solution plus name-lookup precedence, and get-run
visible/hidden/missing/usage/steps) DB-backed via the ``db_session``
fixture. HTTP handler behavior (202 vs 200 mapping, error translation)
is preserved by the existing e2e coverage (``test_agent_run_enqueue``,
``test_pause_semantics``, ``test_agent_run_children``); the router
delegates to these same functions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from shared.run_lineage import person_lineage

from shared.sdk_agent_runs import (
    SdkAgentRunError,
    enqueue_sdk_agent_run,
    get_sdk_agent_run,
)
from src.core.principal import UserPrincipal
from src.models.contracts.agent_runs import AgentRunEnqueueResponse, PausedResponse
from src.services.authorization.explain import RunAsTarget
from src.services.authorization.impersonation import RunAsError


def _principal(org_id=None, **kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk-agent-runs@test.local"),
        organization_id=org_id,
        name=kwargs.get("name", "SDK Agent Runs"),
        is_superuser=kwargs.get("is_superuser", False),
    )


async def _seed_org(db_session, **kwargs):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-agent-runs-org-{uuid4().hex[:8]}",
        is_active=kwargs.get("is_active", True),
        is_provider=kwargs.get("is_provider", False),
        created_by="sdk-agent-runs-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_solution(db_session, *, status="active"):
    from src.models.orm.solutions import Solution as SolutionModel

    row = SolutionModel(
        slug=f"sdk-agent-runs-sol-{uuid4().hex[:8]}",
        name="SDK Agent Runs Solution",
        status=status,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_agent(db_session, name, **kwargs):
    from src.models.enums import AgentAccessLevel
    from src.models.orm.agents import Agent as AgentModel

    row = AgentModel(
        name=name,
        system_prompt="Test agent prompt.",
        is_active=kwargs.get("is_active", True),
        organization_id=kwargs.get("organization_id"),
        solution_id=kwargs.get("solution_id"),
        access_level=kwargs.get("access_level", AgentAccessLevel.ROLE_BASED),
        owner_user_id=kwargs.get("owner_user_id"),
        created_by="sdk-agent-runs-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_run(db_session, agent_id, **kwargs):
    from src.models.orm.agent_runs import AgentRun as AgentRunModel

    row = AgentRunModel(
        agent_id=agent_id,
        trigger_type=kwargs.get("trigger_type", "api"),
        status=kwargs.get("status", "completed"),
        org_id=kwargs.get("org_id"),
        caller_user_id=kwargs.get("caller_user_id"),
        parent_run_id=kwargs.get("parent_run_id"),
        input=kwargs.get("input"),
        run_as_user_id=kwargs.get("run_as_user_id"),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_step(db_session, run_id, step_number, **kwargs):
    from src.models.orm.agent_runs import AgentRunStep as AgentRunStepModel

    row = AgentRunStepModel(
        run_id=run_id,
        step_number=step_number,
        type=kwargs.get("type", "tool_call"),
        content=kwargs.get("content", {"tool": "lookup"}),
        tokens_used=kwargs.get("tokens_used", 10),
        duration_ms=kwargs.get("duration_ms", 5),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_usage(db_session, run_id, **kwargs):
    from src.models.orm.ai_usage import AIUsage as AIUsageModel

    row = AIUsageModel(
        agent_run_id=run_id,
        provider=kwargs.get("provider", "openai"),
        model=kwargs.get("model", "gpt-4o"),
        input_tokens=kwargs.get("input_tokens", 100),
        output_tokens=kwargs.get("output_tokens", 50),
        cache_read_tokens=kwargs.get("cache_read_tokens", 0),
        cache_write_tokens=kwargs.get("cache_write_tokens", 0),
        provider_cost=kwargs.get("provider_cost", Decimal("0.001")),
        cost=kwargs.get("cost", Decimal("0.002")),
        duration_ms=kwargs.get("duration_ms", 120),
        timestamp=kwargs.get(
            "timestamp", datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        ),
        sequence=kwargs.get("sequence", 1),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, org_id, **kwargs):
    from src.models.orm.users import User as UserModel

    row = UserModel(
        email=f"sdk-agent-runs-{uuid4().hex[:8]}@contoso.com",
        name=kwargs.get("name", "Contoso Technician"),
        organization_id=org_id,
        identity_kind=kwargs.get("identity_kind"),
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _run_as_target(user) -> RunAsTarget:
    return RunAsTarget(
        user_id=user.id,
        organization_id=user.organization_id,
        is_active=True,
        is_system=False,
        identity_kind=None,
        is_superuser=False,
        is_external=False,
        is_provider_org=False,
        email=user.email,
        name=user.name,
        privileged=False,
    )


def _patch_authorize_run_as(*, returns: RunAsTarget | None = None, raises: RunAsError | None = None):
    """Stand in for ``authorize_run_as`` with its real signature, recording each call."""
    calls: list[tuple[UserPrincipal, UUID]] = []

    async def authorize_run_as(
        db: AsyncSession, principal: UserPrincipal, run_as_user_id: UUID
    ) -> RunAsTarget | None:
        calls.append((principal, run_as_user_id))
        if raises is not None:
            raise raises
        return returns

    return patch("src.services.authorization.impersonation.authorize_run_as", authorize_run_as), calls


def _patch_enqueue(receipt_id: str | None = None):
    return patch(
        "src.services.execution.agent_run_service.enqueue_agent_run",
        new=AsyncMock(return_value=receipt_id or str(uuid4())),
    )


def _redis_stream_context(entries):
    """Mock ``get_redis()`` async context manager yielding stream entries."""
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=entries)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=redis)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.mark.asyncio
class TestEnqueueSdkAgentRun:
    async def test_success_enqueues_with_actor_attribution(self, db_session):
        org = await _seed_org(db_session)
        # AUTHENTICATED so a non-bypass principal (no role grants seeded)
        # passes the access check resolve_executable_agent now enforces —
        # this test is about actor attribution, not access-level gating.
        from src.models.enums import AgentAccessLevel

        agent = await _seed_agent(
            db_session, "Ticket Agent", access_level=AgentAccessLevel.AUTHENTICATED
        )
        principal = _principal(org.id, name="Caller Name")
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                principal,
                agent_name="Ticket Agent",
                input_data={"ticket_id": 42},
                output_schema={"type": "object"},
            )

        assert isinstance(result, AgentRunEnqueueResponse)
        assert str(result.run_id) == receipt_id
        assert result.status == "queued"
        mock_enqueue.assert_awaited_once_with(
            agent_id=str(agent.id),
            trigger_type="api",
            input_data={"ticket_id": 42},
            output_schema={"type": "object"},
            org_id=str(org.id),
            caller_user_id=str(principal.user_id),
            caller_email=principal.email,
            caller_name="Caller Name",
            sync=False,
            lineage=person_lineage(principal.user_id),
            run_as_user_id=None,
        )
        assert result.run_as_user_id is None

    async def test_name_lookup_is_case_insensitive(self, db_session):
        await _seed_agent(db_session, "Ticket Agent")
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                _principal(is_superuser=True),
                agent_name="ticket agent",
            )

        assert isinstance(result, AgentRunEnqueueResponse)
        mock_enqueue.assert_awaited_once()

    async def test_unknown_agent_is_404(self, db_session):
        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            with pytest.raises(SdkAgentRunError) as exc_info:
                await enqueue_sdk_agent_run(
                    db_session,
                    _principal(is_superuser=True),
                    agent_name="No Such Agent",
                )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Agent 'No Such Agent' not found"
        mock_enqueue.assert_not_awaited()

    async def test_paused_agent_returns_paused_response(self, db_session):
        agent = await _seed_agent(db_session, "Paused Agent", is_active=False)

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                _principal(is_superuser=True),
                agent_name="Paused Agent",
            )

        assert isinstance(result, PausedResponse)
        assert result.message == (
            "Agent 'Paused Agent' is paused. Request not processed."
        )
        assert result.agent_id == agent.id
        mock_enqueue.assert_not_awaited()

    async def test_inactive_solution_is_409(self, db_session):
        solution = await _seed_solution(db_session, status="inactive")
        await _seed_agent(
            db_session, "Solution Agent", solution_id=solution.id
        )

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            with pytest.raises(SdkAgentRunError) as exc_info:
                await enqueue_sdk_agent_run(
                    db_session,
                    _principal(is_superuser=True),
                    agent_name="Solution Agent",
                )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == (
            "Agent 'Solution Agent' belongs to an inactive solution. "
            "Reinstall the solution to execute this agent."
        )
        mock_enqueue.assert_not_awaited()

    async def test_active_solution_enqueues(self, db_session):
        solution = await _seed_solution(db_session, status="active")
        await _seed_agent(
            db_session, "Live Solution Agent", solution_id=solution.id
        )
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session,
                _principal(is_superuser=True),
                agent_name="Live Solution Agent",
            )

        assert isinstance(result, AgentRunEnqueueResponse)
        mock_enqueue.assert_awaited_once()


@pytest.mark.asyncio
class TestEnqueueRunAs:
    async def test_run_as_records_the_acting_user_and_keeps_the_caller(self, db_session):
        from src.models.enums import AgentAccessLevel

        contoso = await _seed_org(db_session)
        target = await _seed_user(db_session, contoso.id)
        agent = await _seed_agent(
            db_session,
            "Contoso Agent",
            organization_id=contoso.id,
            access_level=AgentAccessLevel.AUTHENTICATED,
        )
        principal = _principal(contoso.id, name="Contoso Initiator")
        receipt_id = uuid4()
        expected = AgentRunEnqueueResponse(run_id=receipt_id, run_as_user_id=target.id)
        authorize, calls = _patch_authorize_run_as(returns=_run_as_target(target))

        with authorize, _patch_enqueue(str(receipt_id)) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session, principal, agent_name="Contoso Agent", run_as=target.id
            )

        assert result == expected
        assert calls == [(principal, target.id)]
        mock_enqueue.assert_awaited_once_with(
            agent_id=str(agent.id),
            trigger_type="api",
            input_data=None,
            output_schema=None,
            org_id=str(contoso.id),
            caller_user_id=str(principal.user_id),
            caller_email=principal.email,
            caller_name="Contoso Initiator",
            sync=False,
            lineage=person_lineage(principal.user_id),
            run_as_user_id=target.id,
        )

    async def test_a_global_agent_runs_in_the_acting_users_organization(self, db_session):
        contoso = await _seed_org(db_session)
        fabrikam = await _seed_org(db_session)
        target = await _seed_user(db_session, fabrikam.id)
        await _seed_agent(db_session, "Global Agent")
        authorize, _calls = _patch_authorize_run_as(returns=_run_as_target(target))

        with authorize, _patch_enqueue() as mock_enqueue:
            await enqueue_sdk_agent_run(
                db_session,
                _principal(contoso.id, is_superuser=True),
                agent_name="Global Agent",
                run_as=target.id,
            )

        kwargs = mock_enqueue.await_args.kwargs
        expected = (str(fabrikam.id), target.id)
        assert (kwargs["org_id"], kwargs["run_as_user_id"]) == expected

    async def test_an_org_scoped_agent_refuses_a_user_from_another_organization(self, db_session):
        contoso = await _seed_org(db_session)
        fabrikam = await _seed_org(db_session)
        target = await _seed_user(db_session, fabrikam.id)
        await _seed_agent(db_session, "Contoso Only Agent", organization_id=contoso.id)
        authorize, _calls = _patch_authorize_run_as(returns=_run_as_target(target))

        with authorize, _patch_enqueue() as mock_enqueue, pytest.raises(SdkAgentRunError) as exc_info:
            await enqueue_sdk_agent_run(
                db_session,
                _principal(contoso.id, is_superuser=True),
                agent_name="Contoso Only Agent",
                run_as=target.id,
            )

        assert (exc_info.value.status_code, exc_info.value.detail) == (
            400,
            "Run As user must belong to the agent's organization",
        )
        mock_enqueue.assert_not_awaited()

    @pytest.mark.parametrize(
        "error",
        [
            RunAsError(403, "You don't have permission to run as this user"),
            RunAsError(404, "Run As user 'x' not found"),
            RunAsError(400, "Run As user 'x' is inactive"),
        ],
        ids=["refused", "unknown", "inactive"],
    )
    async def test_a_refused_run_as_keeps_its_status_and_queues_nothing(self, db_session, error):
        await _seed_agent(db_session, "Refusing Agent")
        principal = _principal(is_superuser=True)
        other = uuid4()
        authorize, calls = _patch_authorize_run_as(raises=error)

        with authorize, _patch_enqueue() as mock_enqueue, pytest.raises(SdkAgentRunError) as exc_info:
            await enqueue_sdk_agent_run(
                db_session, principal, agent_name="Refusing Agent", run_as=other
            )

        assert (exc_info.value.status_code, exc_info.value.detail) == (error.status_code, error.detail)
        assert calls == [(principal, other)]
        mock_enqueue.assert_not_awaited()

    async def test_a_person_without_impersonate_users_is_refused(self, db_session):
        from src.models.enums import AgentAccessLevel

        contoso = await _seed_org(db_session)
        target = await _seed_user(db_session, contoso.id)
        await _seed_agent(
            db_session, "Open Agent", access_level=AgentAccessLevel.AUTHENTICATED
        )

        with _patch_enqueue() as mock_enqueue, pytest.raises(SdkAgentRunError) as exc_info:
            await enqueue_sdk_agent_run(
                db_session, _principal(contoso.id), agent_name="Open Agent", run_as=target.id
            )

        assert exc_info.value.status_code == 403
        mock_enqueue.assert_not_awaited()

    async def test_a_person_naming_themselves_launches_exactly_as_without_run_as(self, db_session):
        from shared import access_checks

        provider = await _seed_org(db_session, is_provider=True)
        contoso = await _seed_org(db_session)
        await _seed_agent(db_session, "Contoso Self Agent", organization_id=contoso.id)
        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@provider.example",
            organization_id=provider.id,
            name="Provider Admin",
            is_superuser=True,
            is_provider_org=True,
        )
        receipt_id = uuid4()
        launches = []
        for run_as in (None, admin.user_id):
            token = access_checks.collect_person(admin.user_id)
            try:
                with _patch_enqueue(str(receipt_id)) as mock_enqueue:
                    result = await enqueue_sdk_agent_run(
                        db_session, admin, agent_name="Contoso Self Agent", run_as=run_as
                    )
                collector = access_checks.current()
                notes = list(collector.notes) if collector else None
            finally:
                access_checks.stop_collecting(token)
            launches.append((result, mock_enqueue.await_args.kwargs, notes))

        (plain_result, plain_kwargs, plain_notes), (self_result, self_kwargs, self_notes) = launches
        noted_kinds = [note.kind for note in self_notes or []]
        expected_actor = (str(provider.id), None)
        assert self_kwargs == plain_kwargs
        assert (self_kwargs["org_id"], self_kwargs["run_as_user_id"]) == expected_actor
        assert self_result == plain_result
        assert self_notes == plain_notes
        assert "run_as" not in noted_kinds

    async def test_a_workflow_naming_its_run_user_acts_as_the_run_user(self, db_session):
        from src.core.constants import SYSTEM_USER_ID

        contoso = await _seed_org(db_session)
        person = await _seed_user(db_session, contoso.id)
        await _seed_agent(db_session, "Workflow Agent", organization_id=contoso.id)
        engine = UserPrincipal(
            user_id=UUID(SYSTEM_USER_ID),
            email="engine@internal",
            organization_id=contoso.id,
            is_superuser=True,
            engine_execution_id=str(uuid4()),
            run_user_id=person.id,
        )

        receipt_id = uuid4()
        expected_receipt = AgentRunEnqueueResponse(run_id=receipt_id, run_as_user_id=person.id)
        expected_launch = (str(contoso.id), SYSTEM_USER_ID, person.id)

        with _patch_enqueue(str(receipt_id)) as mock_enqueue:
            result = await enqueue_sdk_agent_run(
                db_session, engine, agent_name="Workflow Agent", run_as=person.id
            )

        kwargs = mock_enqueue.await_args.kwargs
        assert result == expected_receipt
        assert (kwargs["org_id"], kwargs["caller_user_id"], kwargs["run_as_user_id"]) == expected_launch


@pytest.mark.asyncio
class TestRerunKeepsRunAs:
    async def _rerun(self, db_session, principal, run):
        from src.routers.agent_runs import rerun_agent_run

        return await rerun_agent_run(run.id, db_session, principal)

    async def test_a_rerun_acts_as_the_same_user_decided_again(self, db_session):
        contoso = await _seed_org(db_session)
        target = await _seed_user(db_session, contoso.id)
        agent = await _seed_agent(db_session, "Rerun Agent", organization_id=contoso.id)
        principal = _principal(contoso.id, is_superuser=True)
        run = await _seed_run(
            db_session,
            agent.id,
            org_id=contoso.id,
            caller_user_id=str(principal.user_id),
            run_as_user_id=target.id,
        )
        authorize, calls = _patch_authorize_run_as(returns=_run_as_target(target))

        with authorize, patch(
            "src.routers.agent_runs.enqueue_agent_run", new=AsyncMock(return_value=str(uuid4()))
        ) as mock_enqueue:
            await self._rerun(db_session, principal, run)

        kwargs = mock_enqueue.await_args.kwargs
        expected = (str(contoso.id), str(principal.user_id), target.id)
        assert calls == [(principal, target.id)]
        assert (kwargs["org_id"], kwargs["caller_user_id"], kwargs["run_as_user_id"]) == expected

    async def test_a_rerun_refuses_an_acting_user_now_in_another_organization(self, db_session):
        contoso = await _seed_org(db_session)
        fabrikam = await _seed_org(db_session)
        moved = await _seed_user(db_session, fabrikam.id)
        agent = await _seed_agent(db_session, "Moved User Agent", organization_id=contoso.id)
        principal = _principal(contoso.id, is_superuser=True)
        run = await _seed_run(db_session, agent.id, org_id=contoso.id, run_as_user_id=moved.id)
        authorize, _calls = _patch_authorize_run_as(returns=_run_as_target(moved))

        with authorize, patch(
            "src.routers.agent_runs.enqueue_agent_run", new=AsyncMock()
        ) as mock_enqueue, pytest.raises(HTTPException) as exc_info:
            await self._rerun(db_session, principal, run)

        assert (exc_info.value.status_code, exc_info.value.detail) == (
            400,
            "Run As user must belong to the agent's organization",
        )
        mock_enqueue.assert_not_awaited()

    async def test_a_global_agent_rerun_follows_the_acting_users_current_organization(self, db_session):
        contoso = await _seed_org(db_session)
        fabrikam = await _seed_org(db_session)
        moved = await _seed_user(db_session, fabrikam.id)
        agent = await _seed_agent(db_session, "Global Rerun Agent")
        principal = _principal(contoso.id, is_superuser=True)
        run = await _seed_run(db_session, agent.id, org_id=contoso.id, run_as_user_id=moved.id)
        authorize, _calls = _patch_authorize_run_as(returns=_run_as_target(moved))

        with authorize, patch(
            "src.routers.agent_runs.enqueue_agent_run", new=AsyncMock(return_value=str(uuid4()))
        ) as mock_enqueue:
            await self._rerun(db_session, principal, run)

        kwargs = mock_enqueue.await_args.kwargs
        expected = (str(fabrikam.id), moved.id)
        assert (kwargs["org_id"], kwargs["run_as_user_id"]) == expected

    async def test_a_refused_rerun_keeps_the_helpers_status(self, db_session):
        contoso = await _seed_org(db_session)
        target = await _seed_user(db_session, contoso.id)
        agent = await _seed_agent(db_session, "Refused Rerun Agent", organization_id=contoso.id)
        principal = _principal(contoso.id, is_superuser=True)
        run = await _seed_run(db_session, agent.id, org_id=contoso.id, run_as_user_id=target.id)
        authorize, _calls = _patch_authorize_run_as(
            raises=RunAsError(403, "You don't have permission to run as this user")
        )

        with authorize, patch(
            "src.routers.agent_runs.enqueue_agent_run", new=AsyncMock()
        ) as mock_enqueue, pytest.raises(HTTPException) as exc_info:
            await self._rerun(db_session, principal, run)

        assert (exc_info.value.status_code, exc_info.value.detail) == (
            403,
            "You don't have permission to run as this user",
        )
        mock_enqueue.assert_not_awaited()

    async def test_a_rerun_without_run_as_decides_nothing(self, db_session):
        contoso = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Plain Rerun Agent", organization_id=contoso.id)
        principal = _principal(contoso.id, is_superuser=True)
        run = await _seed_run(db_session, agent.id, org_id=contoso.id)
        authorize, calls = _patch_authorize_run_as()

        with authorize, patch(
            "src.routers.agent_runs.enqueue_agent_run", new=AsyncMock(return_value=str(uuid4()))
        ) as mock_enqueue:
            await self._rerun(db_session, principal, run)

        assert calls == []
        assert mock_enqueue.await_args.kwargs["run_as_user_id"] is None


@pytest.mark.asyncio
class TestExecuteAgentRun:
    async def _execute(self, db_session, principal, request, result_data):
        from shared import access_checks
        from src.routers.agent_runs import execute_agent_run

        token = access_checks.collect_person(principal.user_id)
        try:
            with patch(
                "src.routers.agent_runs.enqueue_agent_run", new=AsyncMock(return_value=str(uuid4()))
            ) as mock_enqueue, patch(
                "src.routers.agent_runs.wait_for_agent_run_result", new=AsyncMock(return_value=result_data)
            ):
                response = await execute_agent_run(request, db_session, principal)
            collector = access_checks.current()
            notes = list(collector.notes) if collector else []
        finally:
            access_checks.stop_collecting(token)
        return response, mock_enqueue.await_args.kwargs, notes

    async def test_without_run_as_the_response_and_notes_are_unchanged(self, db_session):
        from shared.access_checks import Note
        from src.models.contracts.agent_runs import AgentRunCreateRequest
        from src.models.enums import AgentAccessLevel

        contoso = await _seed_org(db_session)
        agent = await _seed_agent(
            db_session,
            "Sync Agent",
            organization_id=contoso.id,
            access_level=AgentAccessLevel.AUTHENTICATED,
        )
        principal = _principal(contoso.id, name="Contoso Caller")
        result_data = {"status": "completed", "output": {"answer": 42}}
        expected_notes = [
            Note("permission", contoso.id, {"permission": "agents.execute", "subject": f"agent:{agent.id}"})
        ]
        authorize, calls = _patch_authorize_run_as()

        with authorize:
            response, kwargs, notes = await self._execute(
                db_session, principal, AgentRunCreateRequest(agent_name="Sync Agent"), result_data
            )

        expected_kwargs = {
            "agent_id": str(agent.id),
            "trigger_type": "api",
            "input_data": None,
            "output_schema": None,
            "org_id": str(contoso.id),
            "caller_user_id": str(principal.user_id),
            "caller_email": principal.email,
            "caller_name": "Contoso Caller",
            "sync": True,
            "lineage": person_lineage(principal.user_id),
            "run_as_user_id": None,
        }
        assert response == {"status": "completed", "output": {"answer": 42}}
        assert kwargs == expected_kwargs
        assert notes == expected_notes
        assert calls == []

    async def test_run_as_is_recorded_and_echoed(self, db_session):
        from src.models.contracts.agent_runs import AgentRunCreateRequest

        contoso = await _seed_org(db_session)
        target = await _seed_user(db_session, contoso.id)
        await _seed_agent(db_session, "Sync Run As Agent", organization_id=contoso.id)
        principal = _principal(contoso.id, is_superuser=True)
        request = AgentRunCreateRequest(agent_name="Sync Run As Agent", run_as=target.id)
        expected = {"status": "completed", "run_as_user_id": str(target.id)}
        authorize, _calls = _patch_authorize_run_as(returns=_run_as_target(target))

        with authorize:
            response, kwargs, _notes = await self._execute(
                db_session, principal, request, {"status": "completed"}
            )

        expected_actor = (str(contoso.id), target.id)
        assert response == expected
        assert (kwargs["org_id"], kwargs["run_as_user_id"]) == expected_actor


@pytest.mark.asyncio
class TestGetSdkAgentRun:
    async def test_missing_run_is_404(self, db_session):
        missing = uuid4()
        with pytest.raises(SdkAgentRunError) as exc_info:
            await get_sdk_agent_run(
                db_session, _principal(is_superuser=True), run_id=missing
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == f"Agent run {missing} not found"

    async def test_completed_run_returns_db_steps_children_and_usage(
        self, db_session
    ):
        agent = await _seed_agent(db_session, "History Agent")
        child_agent = await _seed_agent(db_session, "Child Agent")
        run = await _seed_run(db_session, agent.id, status="completed")
        await _seed_step(db_session, run.id, 1)
        await _seed_step(db_session, run.id, 2)
        child = await _seed_run(
            db_session,
            child_agent.id,
            status="completed",
            trigger_type="delegation",
            parent_run_id=run.id,
        )
        await _seed_usage(db_session, run.id, input_tokens=100, output_tokens=50)
        await _seed_usage(db_session, run.id, input_tokens=200, output_tokens=25)

        detail = await get_sdk_agent_run(
            db_session, _principal(is_superuser=True), run_id=run.id
        )

        assert detail.id == run.id
        assert detail.agent_name == "History Agent"
        assert detail.status == "completed"
        assert [s.step_number for s in detail.steps] == [1, 2]
        assert detail.child_run_ids == [child.id]
        assert [c.agent_name for c in detail.child_runs] == ["Child Agent"]
        assert detail.ai_usage is not None and len(detail.ai_usage) == 2
        assert detail.ai_totals is not None
        assert detail.ai_totals.total_input_tokens == 300
        assert detail.ai_totals.total_output_tokens == 75
        assert detail.ai_totals.call_count == 2

    async def test_hidden_run_is_404_for_other_org(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Scoped Agent")
        run = await _seed_run(db_session, agent.id, org_id=org_a.id)

        with pytest.raises(SdkAgentRunError) as exc_info:
            await get_sdk_agent_run(
                db_session, _principal(org_b.id), run_id=run.id
            )

        assert exc_info.value.status_code == 404

    async def test_org_user_sees_own_run(self, db_session):
        """A non-bypass caller sees a run only if THEY started it — org
        membership alone is not enough (that was the pre-fix behavior)."""
        org = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Own Agent")
        principal = _principal(org.id)
        run = await _seed_run(
            db_session, agent.id, org_id=org.id, caller_user_id=str(principal.user_id)
        )

        detail = await get_sdk_agent_run(db_session, principal, run_id=run.id)

        assert detail.id == run.id

    async def test_org_user_cannot_see_another_users_run_in_same_org(self, db_session):
        org = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Shared Org Agent")
        run = await _seed_run(
            db_session, agent.id, org_id=org.id, caller_user_id=str(uuid4())
        )

        with pytest.raises(SdkAgentRunError) as exc_info:
            await get_sdk_agent_run(db_session, _principal(org.id), run_id=run.id)

        assert exc_info.value.status_code == 404

    async def test_in_progress_run_reads_steps_from_redis(self, db_session):
        agent = await _seed_agent(db_session, "Running Agent")
        run = await _seed_run(db_session, agent.id, status="running")
        step_id = uuid4()

        entries = [
            (
                "1-0",
                {
                    "id": str(step_id),
                    "run_id": str(run.id),
                    "step_number": "1",
                    "type": "tool_call",
                    "content": json.dumps({"tool": "lookup"}),
                    "tokens_used": "10",
                    "duration_ms": "5",
                    "created_at": "2026-09-01T12:00:00+00:00",
                },
            )
        ]

        with patch(
            "shared.sdk_agent_runs.get_redis",
            return_value=_redis_stream_context(entries),
        ):
            detail = await get_sdk_agent_run(
                db_session, _principal(is_superuser=True), run_id=run.id
            )

        assert len(detail.steps) == 1
        assert detail.steps[0].id == step_id
        assert detail.steps[0].content == {"tool": "lookup"}
        assert detail.steps[0].tokens_used == 10

    async def test_in_progress_redis_failure_falls_back_to_db(self, db_session):
        agent = await _seed_agent(db_session, "Fallback Agent")
        run = await _seed_run(db_session, agent.id, status="queued")
        await _seed_step(db_session, run.id, 1)

        with patch(
            "shared.sdk_agent_runs.get_redis",
            side_effect=RuntimeError("redis down"),
        ):
            detail = await get_sdk_agent_run(
                db_session, _principal(is_superuser=True), run_id=run.id
            )

        assert [s.step_number for s in detail.steps] == [1]
