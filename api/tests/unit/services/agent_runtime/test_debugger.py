"""Authorized debugger read projections: isolation, redaction, pagination."""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJournalEntry,
    AgentToolInvocation,
)
from src.models.orm.organizations import Organization
from src.services.agent_runtime import debugger
from src.services.agent_runtime import types as rt

asyncio_mark = pytest.mark.asyncio


def _user(org_id: UUID | None = None, *, superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="debugger@example.com",
        organization_id=org_id,
        is_superuser=superuser,
    )


async def _make_org(session) -> UUID:
    org = Organization(
        id=uuid4(), name=f"dbg-{uuid4().hex[:8]}", created_by="debugger-test"
    )
    session.add(org)
    await session.commit()
    return org.id


async def _create_run(session, **overrides):
    overrides.setdefault("trigger_type", "api")
    overrides.setdefault("status", "running")
    run = AgentRun(**overrides)
    session.add(run)
    await session.commit()
    return run


async def _add_journal(session, run_id: UUID, sequence: int, kind: str, data=None):
    session.add(
        AgentRunJournalEntry(
            run_id=run_id, sequence=sequence, kind=kind, data=data or {}
        )
    )
    await session.commit()


async def _cleanup_runs(async_session_factory, *run_ids: UUID):
    async with async_session_factory() as session:
        for model in (AgentRunJournalEntry, AgentRunCheckpoint):
            await session.execute(
                delete(model).where(model.run_id.in_(run_ids))
            )
        await session.execute(
            delete(AgentToolInvocation).where(
                AgentToolInvocation.run_id.in_(run_ids)
            )
        )
        await session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await session.execute(
            delete(Organization).where(Organization.name.like("dbg-%"))
        )
        await session.commit()


class TestTreeIsolation:
    @asyncio_mark
    async def test_cross_tenant_tree_is_not_found(self, async_session_factory):
        async with async_session_factory() as session:
            org_a = await _make_org(session)
            root = await _create_run(session, org_id=org_a)
            root.root_run_id = root.id
            await session.commit()
            root_id = root.id
        try:
            async with async_session_factory() as session:
                with pytest.raises(debugger.DebuggerNotFoundError):
                    await debugger.get_run_tree(session, root_id, _user(uuid4()))
        finally:
            await _cleanup_runs(async_session_factory, root_id)

    @asyncio_mark
    async def test_tree_contains_failed_and_cancelled_children(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            root = await _create_run(session, org_id=org_id, status="waiting_child")
            root.root_run_id = root.id
            await session.commit()
            failed = await _create_run(
                session,
                org_id=org_id,
                status="failed",
                trigger_type="delegation",
                parent_run_id=root.id,
                root_run_id=root.id,
            )
            cancelled = await _create_run(
                session,
                org_id=org_id,
                status="cancelled",
                trigger_type="delegation",
                parent_run_id=root.id,
                root_run_id=root.id,
            )
            ids = (root.id, failed.id, cancelled.id)
        try:
            async with async_session_factory() as session:
                tree = await debugger.get_run_tree(session, root.id, _user(org_id))
            assert tree.root_run_id == root.id
            assert tree.total_runs == 3
            statuses = {child.status for child in tree.root.children}
            assert statuses == {"failed", "cancelled"}
        finally:
            await _cleanup_runs(async_session_factory, *ids)

    @asyncio_mark
    async def test_corrupt_parent_cycle_returns_diagnostic(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            root = await _create_run(session, org_id=org_id, status="running")
            root.root_run_id = root.id
            await session.commit()
            child = await _create_run(
                session,
                org_id=org_id,
                status="running",
                trigger_type="delegation",
                parent_run_id=root.id,
                root_run_id=root.id,
            )
            # Corrupt the root to point back at its child: R -> A -> R.
            root.parent_run_id = child.id
            await session.commit()
            ids = (root.id, child.id)
        try:
            async with async_session_factory() as session:
                tree = await debugger.get_run_tree(session, root.id, _user(org_id))
            assert tree.truncated is True

            def _diagnostics(node):
                found = [node] if node.diagnostic else []
                for kid in node.children:
                    found.extend(_diagnostics(kid))
                return found

            diagnostics = _diagnostics(tree.root)
            assert diagnostics, "expected a bounded cycle diagnostic node"
            assert "cycle" in diagnostics[0].diagnostic
        finally:
            await _cleanup_runs(async_session_factory, *ids)


class TestTimeline:
    @asyncio_mark
    async def test_pagination_has_no_gaps_or_duplicates(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            run_id = run.id
            for seq in range(1, 8):
                await _add_journal(
                    session,
                    run_id,
                    seq,
                    rt.JOURNAL_MODEL_RESPONSE,
                    {"model": "m", "attempt": 1},
                )
        try:
            seen: list[int] = []
            cursor: str | None = None
            async with async_session_factory() as session:
                while True:
                    page = await debugger.get_timeline(
                        session, run_id, _user(org_id), limit=2, cursor=cursor
                    )
                    seen.extend(entry.sequence for entry in page.entries)
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                    # New session per page proves the cursor is self-sufficient.
                    session.expunge_all()
            assert seen == [1, 2, 3, 4, 5, 6, 7]
        finally:
            await _cleanup_runs(async_session_factory, run_id)

    @asyncio_mark
    async def test_kind_filter_and_bad_cursor(self, async_session_factory):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            run_id = run.id
            await _add_journal(session, run_id, 1, rt.JOURNAL_MODEL_REQUEST, {})
            await _add_journal(
                session, run_id, 2, rt.JOURNAL_TOOL_CALL, {"tool_name": "t"}
            )
        try:
            async with async_session_factory() as session:
                page = await debugger.get_timeline(
                    session,
                    run_id,
                    _user(org_id),
                    kind=rt.JOURNAL_TOOL_CALL,
                )
                assert [e.sequence for e in page.entries] == [2]
                with pytest.raises(ValueError):
                    await debugger.get_timeline(
                        session, run_id, _user(org_id), cursor="bogus"
                    )
                with pytest.raises(ValueError):
                    await debugger.get_timeline(
                        session, run_id, _user(org_id), kind="nope"
                    )
        finally:
            await _cleanup_runs(async_session_factory, run_id)

    @asyncio_mark
    async def test_secret_redaction_and_allowlist(self, async_session_factory):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            run_id = run.id
            await _add_journal(
                session,
                run_id,
                1,
                rt.JOURNAL_TOOL_CALL,
                {
                    "tool_name": "search",
                    "arguments": {
                        "api_key": "super-secret-value",
                        "query": "status",
                    },
                    "caller_context": {"ticket": "T-1"},
                    "unknown_blob": {"nested": [1, 2, 3]},
                },
            )
        try:
            async with async_session_factory() as session:
                page = await debugger.get_timeline(session, run_id, _user(org_id))
            detail = page.entries[0].detail
            assert detail["tool_name"] == "search"
            assert detail["arguments"]["api_key"] == "[REDACTED]"
            assert "super-secret-value" not in str(detail)
            assert detail["arguments"]["query"] == "status"
            assert "caller_context" not in detail
            assert "unknown_blob" not in detail
        finally:
            await _cleanup_runs(async_session_factory, run_id)

    @asyncio_mark
    async def test_uncertain_tool_state_is_projected(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            run_id = run.id
            await _add_journal(
                session,
                run_id,
                1,
                rt.JOURNAL_TOOL_CALL,
                {"tool_name": "billing", "provider_tool_call_id": "call-1"},
            )
            session.add(
                AgentToolInvocation(
                    operation_id="agent-tool:op-1",
                    run_id=run_id,
                    provider_tool_call_id="call-1",
                    tool_name="billing",
                    state="uncertain",
                    idempotency_key="idem-1",
                    reconciliation={"checked": "ledger"},
                )
            )
            await session.commit()
        try:
            async with async_session_factory() as session:
                page = await debugger.get_timeline(session, run_id, _user(org_id))
            detail = page.entries[0].detail
            assert detail["invocation_state"] == "uncertain"
            assert detail["uncertain"] is True
            assert detail["reconciliation"] == {"checked": "ledger"}
            assert page.entries[0].operation_id == "agent-tool:op-1"
        finally:
            await _cleanup_runs(async_session_factory, run_id)


class TestSnapshot:
    @asyncio_mark
    async def test_snapshot_hides_secrets_and_context(
        self, async_session_factory
    ):
        prompt = "you are a helper with password hunter2 inside"
        snapshot = {
            "format_version": 1,
            "agent_updated_at": "2026-09-18T00:00:00+00:00",
            "system_prompt": prompt,
            "model": {
                "profile_id": None,
                "provider": "anthropic",
                "model": "claude-x",
                "llm_max_tokens": 1000,
            },
            "tools": [{"name": "search", "parameters": {"type": "object"}}],
            "delegated_agents": [],
            "system_tools": ["sleep_until"],
            "limits": {"max_iterations": 10},
        }
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(
                session,
                org_id=org_id,
                status="running",
                execution_snapshot=snapshot,
                caller_context={"ticket_id": "T-99"},
                lease_token="lease-secret-token",
                lease_owner="worker-1",
                correlation={"ticket_id": "T-99"},
            )
            run_id = run.id
        try:
            async with async_session_factory() as session:
                view = await debugger.get_snapshot(session, run_id, _user(org_id))
            payload = view.model_dump(mode="json")
            assert payload["system_prompt_sha256"] == hashlib.sha256(
                prompt.encode()
            ).hexdigest()
            assert payload["model"]["model"] == "claude-x"
            assert payload["tool_names"] == ["search"]
            assert payload["lease"]["owner"] == "worker-1"
            assert payload["correlation"] == {"ticket_id": "T-99"}
            flat = str(payload)
            assert "hunter2" not in flat
            assert "lease-secret-token" not in flat
            assert "caller_context" not in flat
            assert "system_prompt" not in payload or isinstance(
                payload.get("system_prompt"), type(None)
            )
        finally:
            await _cleanup_runs(async_session_factory, run_id)


class TestCheckpoints:
    @asyncio_mark
    async def test_checkpoint_pagination(self, async_session_factory):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            run_id = run.id
            for seq in range(1, 5):
                session.add(
                    AgentRunCheckpoint(
                        run_id=run_id,
                        sequence=seq,
                        format_version=1,
                        state={"messages": [{"a": 1}] * seq},
                        attempt=1,
                    )
                )
            await session.commit()
        try:
            async with async_session_factory() as session:
                first = await debugger.get_checkpoints(
                    session, run_id, _user(org_id), limit=3
                )
                assert [c.sequence for c in first.checkpoints] == [1, 2, 3]
                assert first.checkpoints[2].message_count == 3
                assert first.next_cursor is not None
                second = await debugger.get_checkpoints(
                    session, run_id, _user(org_id), cursor=first.next_cursor
                )
                assert [c.sequence for c in second.checkpoints] == [4]
                assert second.next_cursor is None
            # State payloads stay server-side: only bounded metadata projected.
            dumped = first.checkpoints[2].model_dump()
            assert set(dumped) == {
                "run_id",
                "sequence",
                "format_version",
                "attempt",
                "created_at",
                "message_count",
                "has_pending_tool_calls",
                "has_pending_join",
                "has_pending_timer",
            }
            assert dumped["message_count"] == 3
        finally:
            await _cleanup_runs(async_session_factory, run_id)

    @asyncio_mark
    async def test_cross_tenant_checkpoints_not_found(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                with pytest.raises(debugger.DebuggerNotFoundError):
                    await debugger.get_checkpoints(
                        session, run_id, _user(uuid4())
                    )
                with pytest.raises(debugger.DebuggerNotFoundError):
                    await debugger.get_snapshot(session, run_id, _user(uuid4()))
                with pytest.raises(debugger.DebuggerNotFoundError):
                    await debugger.get_timeline(session, run_id, _user(uuid4()))
        finally:
            await _cleanup_runs(async_session_factory, run_id)
