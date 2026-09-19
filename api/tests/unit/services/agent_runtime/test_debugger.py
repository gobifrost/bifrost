"""Authorized debugger read projections: isolation, redaction, pagination."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
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
from src.services.agent_runtime.checkpoint_codec import encode_messages

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


async def _add_journal(
    session,
    run_id: UUID,
    sequence: int,
    kind: str,
    data=None,
    *,
    created_at: datetime | None = None,
):
    session.add(
        AgentRunJournalEntry(
            run_id=run_id,
            sequence=sequence,
            kind=kind,
            data=data or {},
            **({"created_at": created_at} if created_at is not None else {}),
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

    @asyncio_mark
    async def test_tree_query_is_bounded_at_maximum_nodes(
        self, async_session_factory, monkeypatch
    ):
        monkeypatch.setattr(debugger, "MAX_TREE_NODES", 2)
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            root = await _create_run(session, org_id=org_id)
            root.root_run_id = root.id
            await session.commit()
            children = [
                await _create_run(
                    session,
                    org_id=org_id,
                    trigger_type="delegation",
                    parent_run_id=root.id,
                    root_run_id=root.id,
                )
                for _ in range(2)
            ]
            ids = (root.id, *(child.id for child in children))
        try:
            async with async_session_factory() as session:
                tree = await debugger.get_run_tree(session, root.id, _user(org_id))
            assert tree.total_runs == 2
            assert tree.truncated is True
            assert len(tree.root.children) == 1
        finally:
            await _cleanup_runs(async_session_factory, *ids)

    @asyncio_mark
    async def test_tree_represents_disconnected_cycle_rows_once(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            root = await _create_run(session, org_id=org_id)
            root.root_run_id = root.id
            await session.commit()
            first = await _create_run(
                session,
                org_id=org_id,
                trigger_type="delegation",
                root_run_id=root.id,
            )
            second = await _create_run(
                session,
                org_id=org_id,
                trigger_type="delegation",
                root_run_id=root.id,
                parent_run_id=first.id,
            )
            first.parent_run_id = second.id
            await session.commit()
            ids = (root.id, first.id, second.id)
        try:
            async with async_session_factory() as session:
                tree = await debugger.get_run_tree(session, root.id, _user(org_id))

            def flatten(node):
                return [node.run_id, *(run_id for child in node.children for run_id in flatten(child))]

            assert sorted(flatten(tree.root)) == sorted(ids)
            assert len(flatten(tree.root)) == len(set(flatten(tree.root)))
            assert tree.truncated is True
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
    async def test_descendant_pagination_uses_sql_tuple_ordering(
        self, async_session_factory
    ):
        started = datetime(2026, 9, 19, tzinfo=timezone.utc)
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            root = await _create_run(session, org_id=org_id)
            root.root_run_id = root.id
            await session.commit()
            child = await _create_run(
                session,
                org_id=org_id,
                trigger_type="delegation",
                parent_run_id=root.id,
                root_run_id=root.id,
            )
            await _add_journal(
                session,
                root.id,
                1,
                rt.JOURNAL_MODEL_REQUEST,
                {},
                created_at=started,
            )
            await _add_journal(
                session,
                child.id,
                1,
                rt.JOURNAL_MODEL_REQUEST,
                {},
                created_at=started + timedelta(seconds=1),
            )
            await _add_journal(
                session,
                root.id,
                2,
                rt.JOURNAL_MODEL_RESPONSE,
                {},
                created_at=started + timedelta(seconds=2),
            )
            ids = (root.id, child.id)
        try:
            async with async_session_factory() as session:
                first = await debugger.get_timeline(
                    session,
                    root.id,
                    _user(org_id),
                    include_descendants=True,
                    limit=2,
                )
                second = await debugger.get_timeline(
                    session,
                    root.id,
                    _user(org_id),
                    include_descendants=True,
                    cursor=first.next_cursor,
                )
                with pytest.raises(ValueError):
                    await debugger.get_timeline(
                        session,
                        root.id,
                        _user(org_id),
                        include_descendants=False,
                        cursor=first.next_cursor,
                    )
                with pytest.raises(ValueError):
                    await debugger.get_timeline(
                        session,
                        root.id,
                        _user(org_id),
                        include_descendants=True,
                        kind=rt.JOURNAL_MODEL_REQUEST,
                        cursor=first.next_cursor,
                    )
            assert [(entry.run_id, entry.sequence) for entry in first.entries] == [
                (root.id, 1),
                (child.id, 1),
            ]
            assert [(entry.run_id, entry.sequence) for entry in second.entries] == [
                (root.id, 2)
            ]
        finally:
            await _cleanup_runs(async_session_factory, *ids)

    @asyncio_mark
    async def test_descendant_scope_excludes_ancestors_and_siblings(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            root = await _create_run(session, org_id=org_id)
            root.root_run_id = root.id
            await session.commit()
            parent = await _create_run(
                session,
                org_id=org_id,
                trigger_type="delegation",
                parent_run_id=root.id,
                root_run_id=root.id,
            )
            requested = await _create_run(
                session,
                org_id=org_id,
                trigger_type="delegation",
                parent_run_id=parent.id,
                root_run_id=root.id,
            )
            child = await _create_run(
                session,
                org_id=org_id,
                trigger_type="delegation",
                parent_run_id=requested.id,
                root_run_id=root.id,
            )
            sibling = await _create_run(
                session,
                org_id=org_id,
                trigger_type="delegation",
                parent_run_id=parent.id,
                root_run_id=root.id,
            )
            for sequence, run in enumerate(
                (root, parent, requested, child, sibling), start=1
            ):
                await _add_journal(
                    session, run.id, 1, rt.JOURNAL_MODEL_REQUEST, {"model": str(sequence)}
                )
            ids = (root.id, parent.id, requested.id, child.id, sibling.id)
        try:
            async with async_session_factory() as session:
                page = await debugger.get_timeline(
                    session,
                    requested.id,
                    _user(org_id),
                    include_descendants=True,
                )
            assert {entry.run_id for entry in page.entries} == {
                requested.id,
                child.id,
            }
        finally:
            await _cleanup_runs(async_session_factory, *ids)

    @asyncio_mark
    async def test_attempt_filter_derives_attempt_from_claim_boundary(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            await _add_journal(
                session,
                run.id,
                1,
                rt.JOURNAL_RESUME,
                {"attempt": 2, "owner": "worker"},
            )
            await _add_journal(
                session,
                run.id,
                2,
                rt.JOURNAL_TOOL_CALL,
                {"tool_name": "lookup"},
            )
            await _add_journal(
                session,
                run.id,
                3,
                rt.JOURNAL_RESUME,
                {"reason": "child completed"},
            )
            await _add_journal(
                session,
                run.id,
                4,
                rt.JOURNAL_COMPLETION,
                {"status": "completed"},
            )
            run_id = run.id
        try:
            async with async_session_factory() as session:
                page = await debugger.get_timeline(
                    session, run_id, _user(org_id), attempt=2
                )
            assert [entry.sequence for entry in page.entries] == [1, 2, 3, 4]
            assert [entry.attempt for entry in page.entries] == [2, 2, 2, 2]
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
            await _add_journal(
                session,
                run_id,
                2,
                rt.JOURNAL_MODEL_RESPONSE,
                {"tool_calls": ["call"] * 101, "unallowed": "not summarized"},
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
            assert page.entries[1].summary == "model response with 100 tool call(s)"
        finally:
            await _cleanup_runs(async_session_factory, run_id)

    @asyncio_mark
    async def test_hidden_child_metadata_is_not_in_detail_or_summary(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            visible_org = await _make_org(session)
            hidden_org = await _make_org(session)
            root = await _create_run(session, org_id=visible_org)
            root.root_run_id = root.id
            await session.commit()
            hidden_child = await _create_run(
                session,
                org_id=hidden_org,
                trigger_type="delegation",
                parent_run_id=root.id,
                root_run_id=root.id,
            )
            await _add_journal(
                session,
                root.id,
                1,
                rt.JOURNAL_DELEGATION,
                {
                    "child_run_id": str(hidden_child.id),
                    "target_agent_id": str(uuid4()),
                    "target_agent_name": "Hidden child agent",
                    "task": "Do not disclose this delegated task",
                },
            )
            ids = (root.id, hidden_child.id)
        try:
            async with async_session_factory() as session:
                page = await debugger.get_timeline(
                    session, root.id, _user(visible_org)
                )
            entry = page.entries[0]
            payload = entry.model_dump_json()
            assert entry.child_run_id is None
            assert "child_run_id" not in entry.detail
            assert "target_agent_name" not in entry.detail
            assert "task" not in entry.detail
            assert str(hidden_child.id) not in payload
            assert "Hidden child agent" not in payload
            assert "Do not disclose this delegated task" not in payload
        finally:
            await _cleanup_runs(async_session_factory, *ids)

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
        snapshot_agent_id = uuid4()
        snapshot = {
            "format_version": 1,
            "agent_id": str(snapshot_agent_id),
            "agent_name": "Pinned helper",
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
                correlation={
                    "ticket_id": "T-99",
                    "api_key": "correlation-secret",
                    "nested": {"authorization": "nested-secret"},
                },
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
            assert payload["agent_id"] == str(snapshot_agent_id)
            assert payload["agent_name"] == "Pinned helper"
            assert payload["correlation"] == {
                "ticket_id": "T-99",
                "api_key": "[REDACTED]",
                "nested": {"authorization": "[REDACTED]"},
            }
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
                        state=encode_messages(
                            [ModelResponse(parts=[TextPart(content="checkpoint")])] * seq
                        ),
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
    async def test_checkpoint_pending_hints_use_serialized_message_codec(
        self, async_session_factory
    ):
        pending_state = encode_messages(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="delegate_agents",
                            args={},
                            tool_call_id="join-call",
                        ),
                        ToolCallPart(
                            tool_name="sleep_until",
                            args={},
                            tool_call_id="timer-call",
                        ),
                        ToolCallPart(
                            tool_name="lookup",
                            args={},
                            tool_call_id="completed-call",
                        ),
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="lookup",
                            content="done",
                            tool_call_id="completed-call",
                        )
                    ]
                ),
            ]
        )
        complete_state = encode_messages(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="lookup",
                            args={},
                            tool_call_id="complete-call",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="lookup",
                            content="done",
                            tool_call_id="complete-call",
                        )
                    ]
                ),
            ]
        )
        async with async_session_factory() as session:
            org_id = await _make_org(session)
            run = await _create_run(session, org_id=org_id)
            session.add_all(
                [
                    AgentRunCheckpoint(
                        run_id=run.id,
                        sequence=1,
                        format_version=1,
                        state=pending_state,
                        attempt=1,
                    ),
                    AgentRunCheckpoint(
                        run_id=run.id,
                        sequence=2,
                        format_version=1,
                        state=complete_state,
                        attempt=1,
                    ),
                    AgentRunCheckpoint(
                        run_id=run.id,
                        sequence=3,
                        format_version=1,
                        state={"format_version": 1, "messages": [{"bad": True}]},
                        attempt=1,
                    ),
                ]
            )
            await session.commit()
            run_id = run.id
        try:
            async with async_session_factory() as session:
                page = await debugger.get_checkpoints(
                    session, run_id, _user(org_id)
                )
            pending, complete, unavailable = page.checkpoints
            assert (
                pending.has_pending_tool_calls,
                pending.has_pending_join,
                pending.has_pending_timer,
            ) == (True, True, True)
            assert (
                complete.has_pending_tool_calls,
                complete.has_pending_join,
                complete.has_pending_timer,
            ) == (False, False, False)
            assert (
                unavailable.has_pending_tool_calls,
                unavailable.has_pending_join,
                unavailable.has_pending_timer,
            ) == (None, None, None)
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
