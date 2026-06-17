"""Unit tests for lossless chat compaction (Chat V2 M5, §4).

Covers the pure logic — per-model threshold, fold-boundary selection (incl.
tool-output protection), the summary working-context block — and the async
compaction path's invariant that the DB messages are never modified while the
working context shrinks.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.enums import MessageRole
from src.services.chat_compaction import (
    COMPACTION_THRESHOLD_FRACTION,
    FALLBACK_CONTEXT_WINDOW,
    KEEP_RECENT_MESSAGES,
    TOOL_OUTPUT_PROTECT_TOKENS,
    build_summary_block,
    compaction_threshold_tokens,
    compute_fold_boundary,
    estimate_tokens,
    render_messages_for_summary,
)
from src.services.llm import LLMMessage


def _msg(seq: int, role: MessageRole, content: str = "", **kw) -> SimpleNamespace:
    """Lightweight stand-in for a Message ORM row (attribute access only)."""
    return SimpleNamespace(
        sequence=seq,
        role=role,
        content=content,
        tool_calls=kw.get("tool_calls"),
        tool_name=kw.get("tool_name"),
        tool_input=kw.get("tool_input"),
        parent_message_id=kw.get("parent_message_id"),
        id=kw.get("id", uuid4()),
    )


# --------------------------------------------------------------------------- #
# Threshold (§4.2)
# --------------------------------------------------------------------------- #


class TestThreshold:
    def test_threshold_is_85_percent_of_window(self):
        assert compaction_threshold_tokens(200_000) == int(
            200_000 * COMPACTION_THRESHOLD_FRACTION
        )
        assert compaction_threshold_tokens(200_000) == 170_000

    def test_threshold_scales_per_model(self):
        # A smaller model compacts sooner.
        small = compaction_threshold_tokens(32_000)
        big = compaction_threshold_tokens(1_000_000)
        assert small < big
        assert small == int(32_000 * 0.85)

    def test_unknown_window_uses_fallback(self):
        assert compaction_threshold_tokens(None) == int(
            FALLBACK_CONTEXT_WINDOW * COMPACTION_THRESHOLD_FRACTION
        )
        assert compaction_threshold_tokens(0) == int(
            FALLBACK_CONTEXT_WINDOW * COMPACTION_THRESHOLD_FRACTION
        )


# --------------------------------------------------------------------------- #
# Fold boundary selection (§4.1, §4.4)
# --------------------------------------------------------------------------- #


class TestFoldBoundary:
    def test_too_few_messages_returns_none(self):
        branch = [_msg(i, MessageRole.USER, "hi") for i in range(KEEP_RECENT_MESSAGES)]
        assert compute_fold_boundary(branch, already_through=None) is None

    def test_folds_older_turns_keeps_recent_verbatim(self):
        # 20 alternating user/assistant messages, no tools.
        branch = []
        for i in range(20):
            role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
            branch.append(_msg(i, role, f"m{i}"))
        result = compute_fold_boundary(branch, already_through=None)
        assert result is not None
        through_seq, folded = result
        # The last KEEP_RECENT_MESSAGES must NOT be folded.
        assert through_seq <= 20 - KEEP_RECENT_MESSAGES
        assert all(m.sequence <= through_seq for m in folded)
        # Cut lands on a user-turn boundary (folded ends right before a user msg).
        assert folded[-1].sequence == through_seq

    def test_recent_tool_output_protected(self):
        # Big recent tool output must stay out of the fold (§4.4).
        branch = []
        for i in range(16):
            branch.append(_msg(i, MessageRole.USER, f"u{i}"))
        # A huge tool output near the end (> protect budget).
        big = "x" * (TOOL_OUTPUT_PROTECT_TOKENS * 4 + 4000)
        branch.append(
            _msg(16, MessageRole.TOOL, big, tool_call_id="c1", tool_name="t")
        )
        branch.append(_msg(17, MessageRole.USER, "final"))
        result = compute_fold_boundary(branch, already_through=None)
        assert result is not None
        through_seq, folded = result
        # The protected tool output (seq 16) must not be in the folded span.
        assert all(m.sequence != 16 for m in folded)
        assert through_seq < 16

    def test_already_covered_returns_none(self):
        branch = []
        for i in range(20):
            role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
            branch.append(_msg(i, role, f"m{i}"))
        # If the existing checkpoint already covers everything eligible, no-op.
        result = compute_fold_boundary(branch, already_through=18)
        assert result is None


# --------------------------------------------------------------------------- #
# Summary block + transcript rendering (§4.5)
# --------------------------------------------------------------------------- #


class TestSummaryBlock:
    def test_no_checkpoint_returns_none(self):
        conv = SimpleNamespace(
            compaction_summary=None,
            compaction_through_sequence=None,
            compaction_original_tokens=None,
        )
        assert build_summary_block(conv) is None

    def test_renders_history_summary_block(self):
        conv = SimpleNamespace(
            compaction_summary="User asked about onboarding. Assistant gave a checklist.",
            compaction_through_sequence=12,
            compaction_original_tokens=28_000,
        )
        block = build_summary_block(conv)
        assert isinstance(block, LLMMessage)
        assert block.role == "user"
        assert "[Conversation history summary]" in (block.content or "")
        assert "28,000 tokens" in (block.content or "")
        assert "onboarding" in (block.content or "")

    def test_transcript_includes_roles_and_tools(self):
        msgs = [
            _msg(0, MessageRole.USER, "do the thing"),
            _msg(
                1,
                MessageRole.ASSISTANT,
                "on it",
                tool_calls=[{"name": "search", "arguments": {"q": "x"}}],
            ),
            _msg(2, MessageRole.TOOL, "found 3 results", tool_name="search"),
        ]
        text = render_messages_for_summary(msgs)
        assert "USER: do the thing" in text
        assert "ASSISTANT: on it" in text
        assert "TOOL_CALL: search" in text
        assert "TOOL_RESULT (search): found 3 results" in text


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens([]) == 0

    def test_text(self):
        msgs = [LLMMessage(role="user", content="x" * 40)]
        assert estimate_tokens(msgs) == 10


# --------------------------------------------------------------------------- #
# Async compaction invariants (§4.1: DB never modified)
# --------------------------------------------------------------------------- #


class _FakeSession:
    """Minimal async session: db.get(Conversation, id) + db.add()."""

    def __init__(self, conversation, branch):
        self._conv = conversation
        self._branch = branch
        self.added: list = []

    async def get(self, _model, _id):
        return self._conv

    def add(self, row):
        self.added.append(row)

    async def execute(self, *_a, **_k):  # pragma: no cover - leaf fallback only
        class _R:
            def scalar_one_or_none(self):
                return None

        return _R()


@pytest.mark.asyncio
async def test_compact_now_persists_checkpoint_without_mutating_messages(monkeypatch):
    from src.services import chat_compaction

    conv = SimpleNamespace(
        id=uuid4(),
        active_leaf_message_id=None,
        compaction_summary=None,
        compaction_through_sequence=None,
        compaction_original_tokens=None,
        updated_at=None,
    )
    branch = []
    for i in range(20):
        role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
        branch.append(_msg(i, role, f"message number {i} " * 5))
    # Snapshot original content to prove the rows are untouched.
    original_contents = [m.content for m in branch]

    session = _FakeSession(conv, branch)

    async def _fake_load_branch(_db, _conv):
        return branch

    async def _fake_summarize(_db, folded):
        return ("SUMMARY TEXT", "anthropic", "claude-haiku-4-5", 100, 50)

    monkeypatch.setattr(chat_compaction, "_load_active_branch", _fake_load_branch)
    monkeypatch.setattr(chat_compaction, "_summarize", _fake_summarize)

    org_id = uuid4()
    result = await chat_compaction.compact_now(
        session, conv.id, organization_id=org_id
    )

    assert result.compacted is True
    assert result.turns_compacted > 0
    # Checkpoint persisted on the conversation.
    assert conv.compaction_summary == "SUMMARY TEXT"
    assert conv.compaction_through_sequence is not None
    # The DB message rows themselves are NOT modified (lossless).
    assert [m.content for m in branch] == original_contents
    # An AIUsage row was recorded for cost accounting.
    assert len(session.added) == 1
    usage = session.added[0]
    assert usage.conversation_id == conv.id
    assert usage.organization_id == org_id
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50


@pytest.mark.asyncio
async def test_compact_now_noop_when_nothing_to_fold(monkeypatch):
    from src.services import chat_compaction

    conv = SimpleNamespace(
        id=uuid4(),
        active_leaf_message_id=None,
        compaction_summary=None,
        compaction_through_sequence=None,
        compaction_original_tokens=None,
        updated_at=None,
    )
    branch = [_msg(i, MessageRole.USER, "short") for i in range(3)]
    session = _FakeSession(conv, branch)

    async def _fake_load_branch(_db, _conv):
        return branch

    monkeypatch.setattr(chat_compaction, "_load_active_branch", _fake_load_branch)
    # Summarizer must NOT be called when there's nothing to fold.
    monkeypatch.setattr(
        chat_compaction, "_summarize", AsyncMock(side_effect=AssertionError)
    )

    result = await chat_compaction.compact_now(session, conv.id, organization_id=None)
    assert result.compacted is False
    assert conv.compaction_summary is None
    assert session.added == []
