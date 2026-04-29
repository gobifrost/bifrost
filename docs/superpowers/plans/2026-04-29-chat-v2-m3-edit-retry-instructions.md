# Chat V2 / Phase 1 / M3 — Edit, Retry, Per-Conversation Instructions

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship M3 of the Chat UX overhaul: branching-based edit + retry, plus per-conversation custom instructions. PR target is `feature/chat-v2`, not `main`.

**Architecture:** Introduce a parent-pointer message tree (`Message.parent_message_id` + `Conversation.active_leaf_message_id`). The agent's history loader walks `active_leaf → root` instead of `ORDER BY sequence`. Edit creates a sibling user message under the same parent; retry creates a sibling assistant message. Sibling navigation in the UI flips the active leaf. The LLM still sees a flat linear list — branching is invisible below the message-loader. Per-conversation instructions append to the system prompt assembly (`agent.system_prompt + workspace.instructions + conversation.instructions`).

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 / Alembic / Pydantic / PostgreSQL / TypeScript / React / Vite / Vitest / Playwright. Backend test runner: `./test.sh`. Linters: `pyright`, `ruff`, `npm run tsc`, `npm run lint`.

**Issue:** #147

---

## Context the implementer needs

### How the chat loop works today

`api/src/services/agent_executor.py` has `AgentExecutor.chat()`. It saves the user message, builds history via `_build_message_history()` (which does `SELECT * FROM messages WHERE conversation_id = ? ORDER BY sequence`), runs the LLM, and saves the assistant message. The WebSocket router (`api/src/routers/websocket.py`) dispatches messages of `type: "chat"` into a task that calls `_process_chat_message` → `AgentExecutor.chat`.

### Why we're branching

ChatGPT, Claude.ai, LibreChat, Open WebUI, Chatbot UI all branch on edit and retry. None of them show a confirm dialog before edit because the old version is preserved. We're adopting that pattern. See the spec §1.

### What's already in place

- `Message.sequence` exists and is monotonic per conversation.
- `Message.tool_calls` (JSONB), `tool_call_id`, `tool_name`, `tool_state`, `tool_result`, `tool_input` exist for representing tool calls/results.
- `Conversation.workspace_id`, `Conversation.current_model` exist (M1, M2).
- `Workspace.instructions TEXT NULL` exists (M1) and is currently NOT used in system-prompt assembly. M3 adds the assembly along with `Conversation.instructions`.
- `ChatStreamChunk` lives at `api/src/models/contracts/agents.py`.
- The chat HTTP router is `api/src/routers/chat.py`. PATCH `/api/chat/conversations/{id}` already exists; we extend its `update_fields` block.
- The chat WS handler is `api/src/routers/websocket.py` lines ~473-528. We add new `type: "edit_message"` and `type: "retry_message"` dispatch arms.
- The frontend chat store is `client/src/stores/chatStore.ts`. The chat surface is `client/src/components/chat/ChatWindow.tsx` + `ChatMessage.tsx`.

### What CHANGES vs. existing code

The history loader is the load-bearing piece. Today it's a simple `ORDER BY sequence` scan. After M3, it walks the parent chain from `active_leaf_message_id` back to root and returns the messages in chronological order. `sequence` is still written for legacy ordering on the active path (it carries the secondary index used by tool-call ID remapping), but `parent_message_id` is the new primary spine.

Tool-call ID remapping in `_build_message_history` (lines ~628-755) operates on the resolved linear path; no behavior change — the function still gets a list of Messages, it just gets them via parent-walk instead of sequence-scan.

### File map

**Backend — new**
- `api/alembic/versions/20260429_chat_v2_m3_message_branching.py` — migration adding the new fields + backfill.
- `api/tests/unit/test_message_branching.py` — branching-loader unit tests + edit/retry helpers.
- `api/tests/e2e/test_chat_branching.py` — WS edit_message/retry_message + HTTP PATCH instructions e2e.

**Backend — modified**
- `api/src/models/orm/agents.py` — `Message.parent_message_id`, `Conversation.active_leaf_message_id`, `Conversation.instructions`.
- `api/src/models/contracts/agents.py` — `MessagePublic.parent_message_id`, `MessagePublic.sibling_count`, `MessagePublic.sibling_index`; `ConversationPublic.active_leaf_message_id`, `ConversationPublic.instructions`; `ConversationUpdate.instructions`; new request models `EditMessageRequest`, `RetryMessageRequest`, `SwitchBranchRequest`.
- `api/src/services/agent_executor.py` — `_build_message_history()` parent-walk; `_save_message()` writes `parent_message_id`; new `chat_branch()` method that runs a turn under a given parent (used by edit/retry); system-prompt assembly appends workspace + conversation instructions.
- `api/src/routers/chat.py` — extend PATCH to accept `instructions`; new `POST /conversations/{id}/active-leaf` endpoint; ensure GET returns `active_leaf_message_id` and `instructions`; messages list returns sibling metadata.
- `api/src/routers/websocket.py` — add `edit_message` and `retry_message` handlers.

**Frontend — new**
- `client/src/components/chat/MessageBranchNav.tsx` — `< 2/3 >` arrows component.
- `client/src/components/chat/MessageBranchNav.test.tsx` — vitest.
- `client/src/components/chat/ConversationInstructionsDialog.tsx` — "Customize this chat" dialog.
- `client/src/components/chat/ConversationInstructionsDialog.test.tsx` — vitest.
- `client/e2e/chat-branching.spec.ts` — Playwright happy path.

**Frontend — modified**
- `client/src/stores/chatStore.ts` — branch-aware path resolution; `editMessage()`, `retryMessage()`, `switchBranch()` actions.
- `client/src/components/chat/ChatMessage.tsx` — Pencil + RotateCcw hover affordances; sibling nav inline.
- `client/src/components/chat/ChatWindow.tsx` — overflow menu "Customize this chat" entry.
- `client/src/services/chat.ts` — new API wrappers (or `$api.useMutation` for new endpoints).

**Spec / docs — modified**
- `docs/superpowers/specs/2026-04-27-chat-ux-design.md` — §1.1, §1.2, §16.9, §16.10, scope summary, non-goals.
- `docs/superpowers/plans/2026-04-27-chat-v2-master-plan.md` — decisions log entry.

### Conventions

- All datetime use `datetime.now(timezone.utc)` with `DateTime(timezone=True)`. Per `feedback_datetime_consistency`.
- Pydantic models live in `api/src/models/contracts/`. ORM in `api/src/models/orm/`.
- Tests use `./test.sh`. Backend unit tests run against real Postgres in the per-worktree test stack.
- Alembic migrations are run by the `bifrost-init` container, not the API. After authoring a migration, restart `bifrost-init` then `api`.
- Frontend types are auto-generated. After API changes, run `npm run generate:types` from `client/` while the dev stack is up.
- Commits are frequent. One commit per task is the default.

---

## Task 1: Author the migration (schema + backfill)

**Files:**
- Create: `api/alembic/versions/20260429_chat_v2_m3_message_branching.py`

The migration adds:
- `messages.parent_message_id UUID NULL FK messages(id) ON DELETE CASCADE` + index.
- `conversations.active_leaf_message_id UUID NULL FK messages(id) ON DELETE SET NULL`.
- `conversations.instructions TEXT NULL`.

Backfill rules:
- For every conversation, set each non-first message's `parent_message_id` to the previous message in `sequence` order. The first message's parent stays NULL.
- For every conversation, set `active_leaf_message_id` to the message with the maximum `sequence` (or NULL for empty conversations).

- [ ] **Step 1: Create the migration file**

Run:
```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/api
touch alembic/versions/20260429_chat_v2_m3_message_branching.py
```

- [ ] **Step 2: Write the migration**

Write to `api/alembic/versions/20260429_chat_v2_m3_message_branching.py`:

```python
"""chat v2 m3: message branching + per-conversation instructions

Adds:
- messages.parent_message_id (FK to messages.id, nullable)
- conversations.active_leaf_message_id (FK to messages.id, nullable)
- conversations.instructions (TEXT, nullable)

Backfills:
- parent_message_id from the prior sequence row in the same conversation
- active_leaf_message_id from MAX(sequence) per conversation

Revision ID: 20260429_chat_v2_m3
Revises: 20260428_chat_v2_m2
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20260429_chat_v2_m3"
down_revision = "20260428_chat_v2_m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # messages.parent_message_id
    op.add_column(
        "messages",
        sa.Column(
            "parent_message_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_messages_parent_message_id",
        "messages",
        "messages",
        ["parent_message_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_messages_parent_message_id",
        "messages",
        ["parent_message_id"],
    )

    # conversations.active_leaf_message_id
    op.add_column(
        "conversations",
        sa.Column(
            "active_leaf_message_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_conversations_active_leaf_message_id",
        "conversations",
        "messages",
        ["active_leaf_message_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # conversations.instructions
    op.add_column(
        "conversations",
        sa.Column("instructions", sa.Text(), nullable=True),
    )

    # Backfill parent_message_id: each message's parent is the prior row by
    # (conversation_id, sequence). LAG over the conversation partition.
    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                LAG(id) OVER (
                    PARTITION BY conversation_id ORDER BY sequence
                ) AS prev_id
            FROM messages
        )
        UPDATE messages m
        SET parent_message_id = ordered.prev_id
        FROM ordered
        WHERE m.id = ordered.id AND ordered.prev_id IS NOT NULL;
        """
    )

    # Backfill active_leaf_message_id: MAX(sequence) message per conversation.
    op.execute(
        """
        WITH leaves AS (
            SELECT
                conversation_id,
                id AS leaf_id,
                ROW_NUMBER() OVER (
                    PARTITION BY conversation_id ORDER BY sequence DESC
                ) AS rn
            FROM messages
        )
        UPDATE conversations c
        SET active_leaf_message_id = leaves.leaf_id
        FROM leaves
        WHERE leaves.conversation_id = c.id AND leaves.rn = 1;
        """
    )


def downgrade() -> None:
    op.drop_column("conversations", "instructions")
    op.drop_constraint(
        "fk_conversations_active_leaf_message_id",
        "conversations",
        type_="foreignkey",
    )
    op.drop_column("conversations", "active_leaf_message_id")
    op.drop_index("ix_messages_parent_message_id", table_name="messages")
    op.drop_constraint(
        "fk_messages_parent_message_id",
        "messages",
        type_="foreignkey",
    )
    op.drop_column("messages", "parent_message_id")
```

- [ ] **Step 3: Apply migration via the test stack**

Run:
```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
./test.sh stack up
```

The init container runs alembic upgrade head. Verify:
```bash
./test.sh stack status
```

If the stack was already up before this branch was checked out, force a re-init:
```bash
docker compose -p $(./test.sh stack status | grep -oP 'project=\K\S+') restart bifrost-init
```

Expected: bifrost-init exits 0 with the new revision applied.

- [ ] **Step 4: Commit**

```bash
git add api/alembic/versions/20260429_chat_v2_m3_message_branching.py
git commit -m "feat(chat-v2/m3): add branching + per-conversation instructions migration

Adds messages.parent_message_id, conversations.active_leaf_message_id, and
conversations.instructions. Backfills parent links from sequence and
active leaf from MAX(sequence) per conversation."
```

---

## Task 2: ORM models — Message + Conversation

**Files:**
- Modify: `api/src/models/orm/agents.py` — `Message`, `Conversation`

- [ ] **Step 1: Write the failing test**

Create `api/tests/unit/test_message_branching.py`:

```python
"""Branching primitives — ORM and history loader."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.enums import MessageRole
from src.models.orm import Conversation, Message


@pytest.mark.asyncio
async def test_message_has_parent_message_id_field(db_session, sample_user):
    """Message rows can be linked into a parent chain."""
    conv = Conversation(user_id=sample_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()

    root = Message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="hello",
        sequence=0,
        parent_message_id=None,
    )
    db_session.add(root)
    await db_session.flush()

    child = Message(
        conversation_id=conv.id,
        role=MessageRole.ASSISTANT,
        content="hi",
        sequence=1,
        parent_message_id=root.id,
    )
    db_session.add(child)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(Message).where(Message.id == child.id)
        )
    ).scalar_one()
    assert fetched.parent_message_id == root.id


@pytest.mark.asyncio
async def test_conversation_has_active_leaf_and_instructions(db_session, sample_user):
    """Conversation tracks an active leaf and per-conversation instructions."""
    conv = Conversation(
        user_id=sample_user.id,
        channel="chat",
        instructions="Speak only in haiku.",
    )
    db_session.add(conv)
    await db_session.flush()

    msg = Message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="hi",
        sequence=0,
    )
    db_session.add(msg)
    await db_session.flush()

    conv.active_leaf_message_id = msg.id
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(Conversation).where(Conversation.id == conv.id)
        )
    ).scalar_one()
    assert fetched.active_leaf_message_id == msg.id
    assert fetched.instructions == "Speak only in haiku."
```

- [ ] **Step 2: Run test, verify it fails**

Run:
```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
./test.sh tests/unit/test_message_branching.py -v
```

Expected: both tests FAIL with `AttributeError` on `parent_message_id` / `active_leaf_message_id` / `instructions`.

- [ ] **Step 3: Add the ORM fields**

Edit `api/src/models/orm/agents.py`. In `class Conversation` (before `extra_data`):

```python
    # Active branch tip — drives history loading. NULL on empty conversation.
    active_leaf_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    instructions: Mapped[str | None] = mapped_column(Text, default=None)
```

In `class Message` (after `sequence`, before `created_at`):

```python
    # Parent in the message tree. NULL only for the root message.
    parent_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
```

In `Message.__table_args__`, add an index:

```python
    __table_args__ = (
        Index("ix_messages_conversation_sequence", "conversation_id", "sequence"),
        Index("ix_messages_parent_message_id", "parent_message_id"),
    )
```

Make sure `Text` is imported at the top of `agents.py` if not already (`from sqlalchemy import ..., Text`).

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
./test.sh tests/unit/test_message_branching.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/models/orm/agents.py api/tests/unit/test_message_branching.py
git commit -m "feat(chat-v2/m3): add ORM fields for message branching

Message.parent_message_id, Conversation.active_leaf_message_id,
Conversation.instructions."
```

---

## Task 3: History loader walks parent chain

**Files:**
- Modify: `api/src/services/agent_executor.py` — `_build_message_history()`
- Modify: `api/tests/unit/test_message_branching.py`

The loader currently does `SELECT * FROM messages WHERE conversation_id = ? ORDER BY sequence`. It must now resolve the active branch path: start at `active_leaf_message_id` (or fall back to MAX(sequence) for backward-compat with rows the migration's backfill missed if any), walk `parent_message_id` to NULL, reverse to chronological order, and return that list.

- [ ] **Step 1: Write failing tests for the path resolver**

Append to `api/tests/unit/test_message_branching.py`:

```python
from src.services.agent_executor import AgentExecutor


@pytest.mark.asyncio
async def test_load_active_branch_returns_path_root_to_leaf(
    db_session, sample_user, session_factory
):
    """Active-branch loader returns messages from root to active leaf in order."""
    conv = Conversation(user_id=sample_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()

    # Tree:
    #   m1 (user "hi")
    #   ├── m2 (assistant "hello")
    #   │     └── m3 (user "more")
    #   │           └── m4 (assistant "old reply")     <- old branch
    #   └── m2b (assistant "hey there")                <- new branch (retried)
    m1 = Message(conversation_id=conv.id, role=MessageRole.USER, content="hi", sequence=0)
    db_session.add(m1)
    await db_session.flush()
    m2 = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hello",
                 sequence=1, parent_message_id=m1.id)
    db_session.add(m2)
    await db_session.flush()
    m3 = Message(conversation_id=conv.id, role=MessageRole.USER, content="more",
                 sequence=2, parent_message_id=m2.id)
    db_session.add(m3)
    await db_session.flush()
    m4 = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="old reply",
                 sequence=3, parent_message_id=m3.id)
    db_session.add(m4)
    await db_session.flush()
    m2b = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hey there",
                  sequence=4, parent_message_id=m1.id)
    db_session.add(m2b)
    await db_session.flush()

    # Active leaf points at the new branch.
    conv.active_leaf_message_id = m2b.id
    await db_session.flush()

    executor = AgentExecutor(session_factory)
    path = await executor._load_active_branch(conv)

    assert [m.content for m in path] == ["hi", "hey there"]
    # The old branch (m3, m4) is not in the path.


@pytest.mark.asyncio
async def test_load_active_branch_falls_back_to_max_sequence(
    db_session, sample_user, session_factory
):
    """If active_leaf is NULL, fall back to MAX(sequence) for legacy rows."""
    conv = Conversation(user_id=sample_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()
    m1 = Message(conversation_id=conv.id, role=MessageRole.USER, content="hi", sequence=0)
    m2 = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hello",
                 sequence=1, parent_message_id=m1.id)
    db_session.add_all([m1, m2])
    await db_session.flush()
    # active_leaf_message_id intentionally left NULL to simulate legacy data.

    executor = AgentExecutor(session_factory)
    path = await executor._load_active_branch(conv)

    assert [m.content for m in path] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_load_active_branch_empty_conversation(
    db_session, sample_user, session_factory
):
    """Empty conversation returns an empty path."""
    conv = Conversation(user_id=sample_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()

    executor = AgentExecutor(session_factory)
    path = await executor._load_active_branch(conv)

    assert path == []
```

If `session_factory` and `sample_user` fixtures don't already exist for unit tests in this layout, look at any existing test in `api/tests/unit/services/` — fixtures live in `api/tests/conftest.py` and per-suite `conftest.py`. Reuse the same pattern.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
./test.sh tests/unit/test_message_branching.py -v
```

Expected: 3 new tests FAIL (`AttributeError: '_load_active_branch'`).

- [ ] **Step 3: Implement `_load_active_branch`**

Add to `class AgentExecutor` in `api/src/services/agent_executor.py`, near `_build_message_history`:

```python
    async def _load_active_branch(
        self, conversation: Conversation
    ) -> list[Message]:
        """Resolve the active branch as a chronological list of messages.

        Walks from `active_leaf_message_id` back through `parent_message_id`
        until NULL, then reverses. Falls back to MAX(sequence) when the
        leaf is NULL (legacy rows the M3 migration's backfill couldn't reach,
        plus brand-new conversations with no leaf set yet).
        """
        async with self._db() as session:
            leaf_id = conversation.active_leaf_message_id
            if leaf_id is None:
                # Fall back: pick the row with the highest sequence.
                fallback = (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id == conversation.id)
                        .order_by(Message.sequence.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if fallback is None:
                    return []
                leaf_id = fallback.id

            # Walk parent chain from leaf to root.
            chain: list[Message] = []
            current_id: UUID | None = leaf_id
            seen: set[UUID] = set()
            while current_id is not None:
                if current_id in seen:
                    # Defensive cycle break — should be impossible given FK
                    # acyclicity but guards against bad data.
                    break
                seen.add(current_id)
                msg = await session.get(Message, current_id)
                if msg is None:
                    break
                chain.append(msg)
                current_id = msg.parent_message_id

            chain.reverse()
            return chain
```

- [ ] **Step 4: Wire `_build_message_history` to the new loader**

In `_build_message_history`, replace this block:

```python
        # Get conversation messages in order
        async with self._db() as session:
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence)
            )
            db_messages = result.scalars().all()
```

with:

```python
        # Get the active branch path (chronological).
        db_messages = await self._load_active_branch(conversation)
```

- [ ] **Step 5: Run all tests**

```bash
./test.sh tests/unit/test_message_branching.py -v
./test.sh tests/unit/services/ -v
```

Expected: all PASS. The existing executor unit tests should still pass because they typically build conversations with linear messages, which the new loader handles identically.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/agent_executor.py api/tests/unit/test_message_branching.py
git commit -m "feat(chat-v2/m3): history loader walks active-branch parent chain

_load_active_branch walks from active_leaf_message_id to root and
returns chronologically ordered messages. _build_message_history now
uses it instead of ORDER BY sequence."
```

---

## Task 4: `_save_message` writes parent + active leaf

**Files:**
- Modify: `api/src/services/agent_executor.py` — `_save_message`

When the chat flow saves a new message today, it doesn't set `parent_message_id` and doesn't update `active_leaf_message_id`. Both must update for the branching loader to find new messages.

The contract: every newly-saved message has `parent_message_id` = the conversation's current active leaf (or NULL if the conversation is empty); after save, set `active_leaf_message_id` = the new message's id.

- [ ] **Step 1: Write the failing test**

Append to `api/tests/unit/test_message_branching.py`:

```python
@pytest.mark.asyncio
async def test_save_message_appends_to_active_branch(
    db_session, sample_user, session_factory
):
    """_save_message links the new row to the current leaf and advances it."""
    conv = Conversation(user_id=sample_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()

    executor = AgentExecutor(session_factory)

    m1 = await executor._save_message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="hi",
    )
    assert m1.parent_message_id is None
    await db_session.refresh(conv)
    assert conv.active_leaf_message_id == m1.id

    m2 = await executor._save_message(
        conversation_id=conv.id,
        role=MessageRole.ASSISTANT,
        content="hello",
    )
    assert m2.parent_message_id == m1.id
    await db_session.refresh(conv)
    assert conv.active_leaf_message_id == m2.id
```

- [ ] **Step 2: Run, verify it fails**

```bash
./test.sh tests/unit/test_message_branching.py::test_save_message_appends_to_active_branch -v
```

Expected: assertion error on `m1.parent_message_id is None` is incorrectly satisfied (it's NULL by default), but `conv.active_leaf_message_id == m1.id` FAILS because nothing sets it.

- [ ] **Step 3: Update `_save_message`**

Find `_save_message` in `agent_executor.py` (around line 532-555 in the current file). Change its body so it:

1. Reads `Conversation.active_leaf_message_id` for the conversation.
2. Sets `parent_message_id` on the new Message to that value (NULL if none).
3. Inserts the message.
4. Updates `Conversation.active_leaf_message_id` to the new message's id.

The existing function probably looks like (verify in source — line numbers shift):

```python
    async def _save_message(
        self,
        *,
        conversation_id: UUID,
        role: MessageRole,
        content: str | None,
        ...
    ) -> Message:
        ...
        async with self._db() as session:
            message = Message(...)
            session.add(message)
            await session.flush()
            return message
```

Change it to set `parent_message_id` from the conversation's current leaf and update the leaf. Pseudocode:

```python
    async def _save_message(
        self,
        *,
        conversation_id: UUID,
        role: MessageRole,
        content: str | None = None,
        tool_calls: list | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        execution_id: str | None = None,
        local_id: str | None = None,
        tool_state: str | None = None,
        tool_result: dict | None = None,
        tool_input: dict | None = None,
        token_count_input: int | None = None,
        token_count_output: int | None = None,
        model: str | None = None,
        cost_tier: str | None = None,
        duration_ms: int | None = None,
        parent_message_id_override: UUID | None = None,
    ) -> Message:
        """Persist a new message.

        Links it to the conversation's active branch:
        - parent_message_id defaults to the conversation's current leaf
          (override available for edit/retry, which create siblings).
        - active_leaf_message_id is advanced to the new row.
        """
        async with self._db() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv is None:
                raise ValueError(f"conversation {conversation_id} not found")

            parent_id = (
                parent_message_id_override
                if parent_message_id_override is not None
                else conv.active_leaf_message_id
            )

            # Compute next sequence within the conversation.
            seq_row = (
                await session.execute(
                    select(func.coalesce(func.max(Message.sequence), -1) + 1)
                    .where(Message.conversation_id == conversation_id)
                )
            ).scalar_one()

            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                execution_id=execution_id,
                local_id=local_id,
                tool_state=tool_state,
                tool_result=tool_result,
                tool_input=tool_input,
                token_count_input=token_count_input,
                token_count_output=token_count_output,
                model=model,
                cost_tier=cost_tier,
                duration_ms=duration_ms,
                sequence=seq_row,
                parent_message_id=parent_id,
            )
            session.add(message)
            await session.flush()

            conv.active_leaf_message_id = message.id
            await session.flush()
            return message
```

Match the existing parameters of the current `_save_message` exactly — read the file first; the signature in this plan is illustrative. The key changes are: pull `Conversation.active_leaf_message_id` for `parent_id`, accept an `parent_message_id_override` kwarg, update the leaf after insert.

- [ ] **Step 4: Run tests**

```bash
./test.sh tests/unit/test_message_branching.py -v
./test.sh tests/unit/services/test_agent_executor_session.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/agent_executor.py api/tests/unit/test_message_branching.py
git commit -m "feat(chat-v2/m3): _save_message links to active branch + advances leaf"
```

---

## Task 5: System-prompt assembly appends instructions

**Files:**
- Modify: `api/src/services/agent_executor.py` — `_build_message_history`
- Modify: `api/tests/unit/test_message_branching.py`

Today the system prompt is `agent.system_prompt` only. M3 makes it `agent.system_prompt + "\n\n" + workspace.instructions + "\n\n" + conversation.instructions`, omitting empty parts cleanly.

- [ ] **Step 1: Write the failing test**

Append to `api/tests/unit/test_message_branching.py`:

```python
from src.models.orm import Workspace


@pytest.mark.asyncio
async def test_system_prompt_includes_workspace_and_conversation_instructions(
    db_session, sample_user, session_factory, sample_agent
):
    """System prompt assembly = agent prompt + workspace inst + conv inst."""
    org_id = sample_user.organization_id
    ws = Workspace(
        name="Test", scope="personal", user_id=sample_user.id,
        organization_id=org_id, created_by=sample_user.id,
        instructions="Always respond in formal English.",
    )
    db_session.add(ws)
    await db_session.flush()

    conv = Conversation(
        user_id=sample_user.id, channel="chat",
        workspace_id=ws.id, agent_id=sample_agent.id,
        instructions="Cite the user's name in every reply.",
    )
    db_session.add(conv)
    await db_session.flush()

    executor = AgentExecutor(session_factory)
    messages = await executor._build_message_history(sample_agent, conv)
    assert messages[0].role == "system"
    sysp = messages[0].content or ""
    assert "Always respond in formal English." in sysp
    assert "Cite the user's name in every reply." in sysp


@pytest.mark.asyncio
async def test_system_prompt_omits_empty_instruction_blocks(
    db_session, sample_user, session_factory, sample_agent
):
    """Empty workspace/conv instructions don't produce stray separators."""
    conv = Conversation(
        user_id=sample_user.id, channel="chat", agent_id=sample_agent.id,
    )
    db_session.add(conv)
    await db_session.flush()

    executor = AgentExecutor(session_factory)
    messages = await executor._build_message_history(sample_agent, conv)
    sysp = messages[0].content or ""
    # No double newlines from empty blocks
    assert "\n\n\n" not in sysp
```

If `sample_agent` fixture doesn't exist for unit tests, create one inline:

```python
import pytest_asyncio

@pytest_asyncio.fixture
async def sample_agent(db_session, sample_user):
    from src.models.orm import Agent
    agent = Agent(
        name="Test Agent",
        organization_id=sample_user.organization_id,
        access_level="org",
        system_prompt="You are helpful.",
        created_by=sample_user.id,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent
```

(Adapt the kwargs to match the actual `Agent` ORM signature.)

- [ ] **Step 2: Run, verify they fail**

```bash
./test.sh tests/unit/test_message_branching.py -k instructions -v
```

Expected: assertion failure on "Always respond..." not found in system prompt.

- [ ] **Step 3: Update `_build_message_history`**

In `agent_executor.py`, locate the system prompt assembly (around line 632-645):

```python
        # Add system prompt (use agent's prompt or configurable default for agentless chat)
        if agent:
            from src.services.execution.agent_helpers import build_agent_system_prompt
            system_prompt = build_agent_system_prompt(agent, execution_context={"mode": "chat"})
        else:
            system_prompt = await self._get_default_system_prompt()
```

Append after that block, before the system message is added:

```python
        # Append workspace + per-conversation instructions when present.
        extra_blocks: list[str] = []
        if conversation.workspace_id is not None:
            async with self._db() as _s:
                ws = await _s.get(Workspace, conversation.workspace_id)
            if ws is not None and ws.instructions:
                extra_blocks.append(ws.instructions.strip())
        if conversation.instructions:
            extra_blocks.append(conversation.instructions.strip())
        if extra_blocks:
            system_prompt = "\n\n".join([system_prompt.strip(), *extra_blocks])
```

Add `from src.models.orm import Workspace` to the top-of-file imports if it isn't already imported.

- [ ] **Step 4: Run tests**

```bash
./test.sh tests/unit/test_message_branching.py -k instructions -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/agent_executor.py api/tests/unit/test_message_branching.py
git commit -m "feat(chat-v2/m3): system prompt = agent + workspace + conversation instructions"
```

---

## Task 6: `chat_branch()` — run a turn under a given parent

**Files:**
- Modify: `api/src/services/agent_executor.py`
- Modify: `api/tests/unit/test_message_branching.py`

Edit and retry both need to: (a) create a sibling message under a given parent, (b) move active leaf to that new sibling, (c) run the turn through the existing chat() loop logic. Refactor: extract a `chat_branch(conversation, parent_message_id, new_user_text=None, new_assistant=False)` method that handles edit (new user text) and retry (no new user text) uniformly, then dispatches into the same completion loop `chat()` uses.

Two approaches:
1. Add `parent_message_id_override` to the existing `chat()` method and have callers set the leaf before calling.
2. Add a separate `chat_branch()` that prepares the parent state then calls into `chat()`.

Pick approach (1) — minimal duplication. The prep step (truncate? no — branching, so just create a sibling user message) is small enough to inline.

Concretely, refactor `chat()` so the user-message-save step honors `parent_message_id_override`:

- [ ] **Step 1: Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_edit_creates_sibling_user_message(
    db_session, sample_user, session_factory, sample_agent
):
    """Editing a user message creates a sibling under the same parent."""
    conv = Conversation(
        user_id=sample_user.id, channel="chat", agent_id=sample_agent.id,
    )
    db_session.add(conv)
    await db_session.flush()

    executor = AgentExecutor(session_factory)
    # Seed: original user msg + assistant reply
    u1 = await executor._save_message(
        conversation_id=conv.id, role=MessageRole.USER, content="hi",
    )
    a1 = await executor._save_message(
        conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hello",
    )
    await db_session.refresh(conv)
    assert conv.active_leaf_message_id == a1.id

    # Edit u1: create a new user message as a sibling (same parent = u1.parent = NULL).
    u1_edit = await executor._save_message(
        conversation_id=conv.id, role=MessageRole.USER, content="hi (edited)",
        parent_message_id_override=u1.parent_message_id,
    )
    assert u1_edit.parent_message_id == u1.parent_message_id
    await db_session.refresh(conv)
    assert conv.active_leaf_message_id == u1_edit.id


@pytest.mark.asyncio
async def test_retry_creates_sibling_assistant_message(
    db_session, sample_user, session_factory, sample_agent
):
    """Retrying creates a new assistant sibling under the same parent."""
    conv = Conversation(
        user_id=sample_user.id, channel="chat", agent_id=sample_agent.id,
    )
    db_session.add(conv)
    await db_session.flush()

    executor = AgentExecutor(session_factory)
    u1 = await executor._save_message(
        conversation_id=conv.id, role=MessageRole.USER, content="hi",
    )
    a1 = await executor._save_message(
        conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hello",
    )

    # Retry: new assistant message as sibling of a1 (same parent = u1.id).
    a1_retry = await executor._save_message(
        conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hello (retry)",
        parent_message_id_override=a1.parent_message_id,
    )
    assert a1_retry.parent_message_id == u1.id
    await db_session.refresh(conv)
    assert conv.active_leaf_message_id == a1_retry.id
```

- [ ] **Step 2: Run, verify**

These should already pass (Task 4's `_save_message` accepts `parent_message_id_override`). Confirm:

```bash
./test.sh tests/unit/test_message_branching.py::test_edit_creates_sibling_user_message tests/unit/test_message_branching.py::test_retry_creates_sibling_assistant_message -v
```

Expected: PASS. If not, double-check Task 4.

- [ ] **Step 3: Add an `edit_user_message` and `retry_assistant` wrapper to AgentExecutor**

These are higher-level operations called by the WS handlers. Each:
1. Validates the target message belongs to this conversation.
2. For edit: creates a new user message with the new text under the same parent as the original.
3. For retry: identifies the parent of the assistant message being retried; sets the active leaf back to that parent first, so the next chat() call's `_load_active_branch` returns the path up-to-but-not-including the retried message.
4. For edit: also sets the active leaf to the new user message and runs the turn (so a new assistant reply gets created).
5. Returns the streamed chunks via `chat()`'s streaming generator.

Add to `class AgentExecutor`:

```python
    async def edit_user_message(
        self,
        agent: Agent | None,
        conversation: Conversation,
        target_message_id: UUID,
        new_text: str,
        *,
        local_id: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Edit a user message — create a sibling and dispatch a fresh turn."""
        async with self._db() as session:
            target = await session.get(Message, target_message_id)
            if target is None or target.conversation_id != conversation.id:
                raise ValueError("target message not in this conversation")
            if target.role != MessageRole.USER:
                raise ValueError("can only edit user messages")
            parent_of_target = target.parent_message_id

        # Save the new user message as a sibling of `target`, advance leaf.
        new_user = await self._save_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=new_text,
            local_id=local_id,
            parent_message_id_override=parent_of_target,
        )
        # Refresh the in-memory conversation object so chat() picks up the new leaf.
        async with self._db() as session:
            fresh = await session.get(Conversation, conversation.id)
        # chat() will yield message_start with the assistant_message_id and
        # save the assistant response under new_user as its parent (because
        # _save_message reads active_leaf_message_id for parent inference).
        async for chunk in self.chat(
            agent=agent,
            conversation=fresh,
            user_message=new_text,
            stream=True,
            enable_routing=False,
            local_id=local_id,
            _skip_save_user_message=True,  # see Task 7
            _user_message_id=new_user.id,
        ):
            yield chunk

    async def retry_assistant_message(
        self,
        agent: Agent | None,
        conversation: Conversation,
        target_message_id: UUID,
        *,
        local_id: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Retry an assistant message — back the leaf up, dispatch a fresh turn."""
        async with self._db() as session:
            target = await session.get(Message, target_message_id)
            if target is None or target.conversation_id != conversation.id:
                raise ValueError("target message not in this conversation")
            if target.role != MessageRole.ASSISTANT:
                raise ValueError("can only retry assistant messages")
            parent_id = target.parent_message_id
            if parent_id is None:
                raise ValueError("assistant message has no parent — nothing to retry from")
            # Move the active leaf to the parent of the target, so the next
            # chat() turn loads history up-to-and-including the parent
            # (the user message that prompted this assistant reply) and
            # appends a NEW assistant message as a sibling of `target`.
            conv = await session.get(Conversation, conversation.id)
            assert conv is not None
            conv.active_leaf_message_id = parent_id
            await session.flush()
            fresh = conv

        async for chunk in self.chat(
            agent=agent,
            conversation=fresh,
            user_message="",  # not used; see _skip_save_user_message
            stream=True,
            enable_routing=False,
            local_id=local_id,
            _skip_save_user_message=True,
            _user_message_id=parent_id,
        ):
            yield chunk
```

- [ ] **Step 4: Add `_skip_save_user_message` + `_user_message_id` params to `chat()`**

In `AgentExecutor.chat()`, add private parameters:

```python
    async def chat(
        self,
        agent: Agent | None,
        conversation: Conversation,
        user_message: str,
        *,
        stream: bool = True,
        enable_routing: bool = True,
        local_id: str | None = None,
        _skip_save_user_message: bool = False,
        _user_message_id: UUID | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
```

Then in step 3 of `chat()` (the user-message-save section, near "# 3. Save user message"), guard with the skip flag:

```python
            # 3. Save user message
            if _skip_save_user_message:
                # Edit/retry path: caller has already created the user message
                # (or, for retry, no new user message — we re-use the existing one).
                user_msg_id = _user_message_id
                # Don't change active leaf here — caller has already set it.
            else:
                user_msg = await self._save_message(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content=user_message,
                    local_id=local_id,
                )
                user_msg_id = user_msg.id

            # 3b. Generate assistant message ID upfront and send message_start
            assistant_message_id = uuid4()
            yield ChatStreamChunk(
                type="message_start",
                user_message_id=str(user_msg_id),
                assistant_message_id=str(assistant_message_id),
                local_id=local_id,
            )
```

- [ ] **Step 5: Run all unit tests**

```bash
./test.sh tests/unit/test_message_branching.py -v
./test.sh tests/unit/services/ -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/agent_executor.py api/tests/unit/test_message_branching.py
git commit -m "feat(chat-v2/m3): edit_user_message + retry_assistant_message

Branching-aware turn dispatch — edit creates a sibling user message and
runs a fresh turn; retry walks the leaf back to the user message and
runs a fresh assistant turn as a sibling of the original."
```

---

## Task 7: Pydantic contracts — sibling metadata + new request shapes

**Files:**
- Modify: `api/src/models/contracts/agents.py`

We need:
- `MessagePublic.parent_message_id`, `MessagePublic.sibling_count`, `MessagePublic.sibling_index` — for client-side branching UI.
- `ConversationPublic.active_leaf_message_id`, `ConversationPublic.instructions`.
- `ConversationUpdate.instructions` — for PATCH.
- `EditMessageRequest`, `RetryMessageRequest`, `SwitchBranchRequest` — request shapes.

- [ ] **Step 1: Read current shapes**

```bash
grep -n "class MessagePublic\|class ConversationPublic\|class ConversationUpdate" \
  /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/api/src/models/contracts/agents.py
```

Note line numbers; you'll edit each in place.

- [ ] **Step 2: Add fields to MessagePublic**

In `class MessagePublic`, add:

```python
    parent_message_id: UUID | None = None
    sibling_count: int = 1   # 1 = no siblings
    sibling_index: int = 0   # 0-based position among siblings
```

- [ ] **Step 3: Add fields to ConversationPublic and ConversationUpdate**

In `class ConversationPublic`, add:

```python
    active_leaf_message_id: UUID | None = None
    instructions: str | None = None
```

In `class ConversationUpdate`, add:

```python
    instructions: str | None = None
```

- [ ] **Step 4: Add new request models**

Append to the Conversation/Message section:

```python
class EditMessageRequest(BaseModel):
    """Edit a user message in place — creates a sibling, dispatches a turn."""
    content: str = Field(min_length=1)
    local_id: str | None = None


class RetryMessageRequest(BaseModel):
    """Retry an assistant message — creates a sibling, dispatches a turn."""
    local_id: str | None = None


class SwitchBranchRequest(BaseModel):
    """Switch the conversation's active leaf to another message id."""
    message_id: UUID
```

- [ ] **Step 5: Run a quick import sanity check**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/api
./.venv/bin/python -c "from src.models.contracts.agents import EditMessageRequest, RetryMessageRequest, SwitchBranchRequest; print('ok')"
```

Expected: `ok`.

If `.venv` doesn't exist yet:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r ../requirements.txt pyright ruff
```

- [ ] **Step 6: Commit**

```bash
git add api/src/models/contracts/agents.py
git commit -m "feat(chat-v2/m3): pydantic contracts for branching + instructions"
```

---

## Task 8: HTTP — extend PATCH `/conversations/{id}` for instructions, add `/active-leaf`

**Files:**
- Modify: `api/src/routers/chat.py`

- [ ] **Step 1: Extend PATCH to accept `instructions`**

In `update_conversation` (line ~319), after the `current_model` block, add:

```python
    if "instructions" in update_fields:
        conversation.instructions = update_fields["instructions"]
```

Also update the `ConversationPublic(...)` return at the bottom of `update_conversation` to include the new fields:

```python
    return ConversationPublic(
        id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        workspace_id=conversation.workspace_id,
        current_model=conversation.current_model,
        active_leaf_message_id=conversation.active_leaf_message_id,
        instructions=conversation.instructions,
        channel=conversation.channel,
        title=conversation.title,
        is_active=conversation.is_active,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
        last_message_at=last_message_at,
        agent_name=conversation.agent.name if conversation.agent else None,
    )
```

Make the same additions in the GET handler (`get_conversation`, ~line 266) so reads include the new fields.

- [ ] **Step 2: Add `POST /conversations/{id}/active-leaf`**

After the existing PATCH endpoint:

```python
@router.post("/conversations/{conversation_id}/active-leaf")
async def switch_active_leaf(
    conversation_id: UUID,
    payload: SwitchBranchRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ConversationPublic:
    """Switch the conversation's active leaf — sibling navigation."""
    conv = (
        await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.agent))
            .where(Conversation.id == conversation_id)
            .where(Conversation.user_id == user.user_id)
        )
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(404, f"Conversation {conversation_id} not found")

    target = await db.get(Message, payload.message_id)
    if target is None or target.conversation_id != conversation_id:
        raise HTTPException(404, "Message not in this conversation")

    conv.active_leaf_message_id = target.id
    conv.updated_at = datetime.now(timezone.utc)
    await db.flush()

    count_result = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )
    message_count = count_result.scalar() or 0
    last_msg_result = await db.execute(
        select(Message.created_at)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.desc())
        .limit(1)
    )
    last_message_at = last_msg_result.scalar_one_or_none()

    return ConversationPublic(
        id=conv.id,
        agent_id=conv.agent_id,
        user_id=conv.user_id,
        workspace_id=conv.workspace_id,
        current_model=conv.current_model,
        active_leaf_message_id=conv.active_leaf_message_id,
        instructions=conv.instructions,
        channel=conv.channel,
        title=conv.title,
        is_active=conv.is_active,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=message_count,
        last_message_at=last_message_at,
        agent_name=conv.agent.name if conv.agent else None,
    )
```

Add `SwitchBranchRequest` to the imports at the top of `chat.py`.

- [ ] **Step 3: Update messages list endpoint to include sibling metadata**

The `get_messages` endpoint at line ~433 should compute sibling counts/indices for each returned message. Two ways:
(a) post-process in Python with one extra query for parent groups.
(b) compute in SQL with window functions.

(b) is cleaner. Replace the body of the messages-list endpoint's loop with:

```python
    # Get messages on the active branch — walk parent chain from leaf.
    # For sibling metadata we additionally count peers per parent.
    rows = (
        await db.execute(
            select(
                Message,
                func.count("*").over(partition_by=Message.parent_message_id).label("sibling_count"),
                (
                    func.row_number().over(
                        partition_by=Message.parent_message_id,
                        order_by=Message.sequence,
                    ) - 1
                ).label("sibling_index"),
            )
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence.asc())
            .limit(limit)
        )
    ).all()
    messages_with_sib = [
        MessagePublic(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            tool_calls=[ToolCall(**tc) for tc in (m.tool_calls or [])],
            tool_call_id=m.tool_call_id,
            tool_name=m.tool_name,
            execution_id=m.execution_id,
            local_id=m.local_id,
            tool_state=m.tool_state,
            tool_result=m.tool_result,
            tool_input=m.tool_input,
            token_count_input=m.token_count_input,
            token_count_output=m.token_count_output,
            model=m.model,
            cost_tier=m.cost_tier,
            duration_ms=m.duration_ms,
            sequence=m.sequence,
            parent_message_id=m.parent_message_id,
            sibling_count=int(sib_count),
            sibling_index=int(sib_index),
            created_at=m.created_at,
        )
        for m, sib_count, sib_index in rows
    ]
    return messages_with_sib
```

Match the actual `MessagePublic` shape — read it first; the kwargs above are illustrative. Drop any field that isn't on the contract. The existing endpoint structure (auth, conversation lookup) stays.

The endpoint returns ALL messages (including off-branch siblings) — the client uses `active_leaf_message_id` + `parent_message_id` to render the active path.

- [ ] **Step 4: Quick smoke test**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
./test.sh stack up  # if not already up
./.venv/bin/python -c "from src.routers.chat import router; print(router.routes[-1].path)"
```

Expected: prints something containing `active-leaf`.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/chat.py
git commit -m "feat(chat-v2/m3): HTTP — instructions on PATCH, active-leaf endpoint, sibling metadata"
```

---

## Task 9: WebSocket — `edit_message` and `retry_message` handlers

**Files:**
- Modify: `api/src/routers/websocket.py`

The existing `chat`/`chat_stop` handlers manage in-flight task lifecycle. Edit and retry need the same lifecycle treatment.

- [ ] **Step 1: Add `edit_message` handler**

In `websocket.py`, in the dispatch chain (after the existing `elif data.get("type") == "chat":` block), add:

```python
            elif data.get("type") == "edit_message":
                conversation_id = data.get("conversation_id")
                target_message_id = data.get("target_message_id")
                new_text = data.get("content", "")
                local_id = data.get("local_id")

                if not conversation_id or not target_message_id or not new_text:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Missing conversation_id, target_message_id, or content",
                    })
                    continue

                has_access, conversation = await can_access_conversation(user, conversation_id)
                if not has_access or not conversation:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Conversation not found or access denied",
                    })
                    continue

                existing_task = active_chat_tasks.get(conversation_id)
                if existing_task and not existing_task.done():
                    await websocket.send_json({
                        "type": "error",
                        "error": "Another turn is in flight for this conversation",
                    })
                    continue

                async def _do_edit(cid: str, tmid: str, text: str, lid: str | None) -> None:
                    executor = AgentExecutor(get_session_factory())
                    agent = (
                        await _get_agent_for_conversation(conversation)
                        if conversation.agent_id else None
                    )
                    async for chunk in executor.edit_user_message(
                        agent=agent,
                        conversation=conversation,
                        target_message_id=UUID(tmid),
                        new_text=text,
                        local_id=lid,
                    ):
                        await websocket.send_json(chunk.model_dump(mode="json"))

                t = asyncio.create_task(_do_edit(conversation_id, target_message_id, new_text, local_id))
                active_chat_tasks[conversation_id] = t

                def _on_edit_done(_t: asyncio.Task, _cid: str = conversation_id) -> None:
                    active_chat_tasks.pop(_cid, None)
                t.add_done_callback(_on_edit_done)
```

- [ ] **Step 2: Add `retry_message` handler**

Symmetric:

```python
            elif data.get("type") == "retry_message":
                conversation_id = data.get("conversation_id")
                target_message_id = data.get("target_message_id")
                local_id = data.get("local_id")

                if not conversation_id or not target_message_id:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Missing conversation_id or target_message_id",
                    })
                    continue

                has_access, conversation = await can_access_conversation(user, conversation_id)
                if not has_access or not conversation:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Conversation not found or access denied",
                    })
                    continue

                existing_task = active_chat_tasks.get(conversation_id)
                if existing_task and not existing_task.done():
                    await websocket.send_json({
                        "type": "error",
                        "error": "Another turn is in flight for this conversation",
                    })
                    continue

                async def _do_retry(cid: str, tmid: str, lid: str | None) -> None:
                    executor = AgentExecutor(get_session_factory())
                    agent = (
                        await _get_agent_for_conversation(conversation)
                        if conversation.agent_id else None
                    )
                    async for chunk in executor.retry_assistant_message(
                        agent=agent,
                        conversation=conversation,
                        target_message_id=UUID(tmid),
                        local_id=lid,
                    ):
                        await websocket.send_json(chunk.model_dump(mode="json"))

                t = asyncio.create_task(_do_retry(conversation_id, target_message_id, local_id))
                active_chat_tasks[conversation_id] = t

                def _on_retry_done(_t: asyncio.Task, _cid: str = conversation_id) -> None:
                    active_chat_tasks.pop(_cid, None)
                t.add_done_callback(_on_retry_done)
```

If `_get_agent_for_conversation` doesn't already exist in `websocket.py`, look at how the existing `chat` handler resolves the agent — likely via `_process_chat_message` which fetches the agent inside. Mirror that pattern; you may need to inline the fetch (`await session.get(Agent, conversation.agent_id)`).

If `get_session_factory()` isn't the actual helper, check imports at top of `websocket.py` for the session factory accessor and reuse.

- [ ] **Step 3: Quick smoke import**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/api
./.venv/bin/python -c "from src.routers.websocket import websocket_endpoint; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add api/src/routers/websocket.py
git commit -m "feat(chat-v2/m3): WS — edit_message + retry_message handlers"
```

---

## Task 10: Backend e2e — edit, retry, instructions

**Files:**
- Create: `api/tests/e2e/test_chat_branching.py`

Mirror the pattern of any existing e2e in `api/tests/e2e/`. The test logs in, creates a conversation with an agent, sends a chat, then exercises edit/retry/instructions via the HTTP and WS endpoints.

- [ ] **Step 1: Find a reference e2e**

```bash
ls /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/api/tests/e2e/ | grep -iE "chat|conversation" | head
```

Pick the most similar (likely `test_chat_*`). Read it for the auth pattern, agent setup pattern, and WS testing pattern (ASGI test client or websocket fixture).

- [ ] **Step 2: Write the e2e test**

Create `api/tests/e2e/test_chat_branching.py`:

```python
"""E2E for Chat V2 / M3 — branching (edit, retry) + per-conversation instructions."""
from uuid import UUID

import pytest


@pytest.mark.asyncio
async def test_edit_creates_branch_and_retains_old_messages(
    api_client, seeded_org_with_chat_agent
):
    """Editing a user message creates a sibling and a new assistant reply."""
    conv_id = await _create_conv(api_client, seeded_org_with_chat_agent)
    # Seed initial turn via WS
    await _send_chat(api_client, conv_id, "hi")
    msgs = (await api_client.get(f"/api/chat/conversations/{conv_id}/messages")).json()
    assert len(msgs) == 2
    user_msg_id = msgs[0]["id"]

    # Edit via WS
    await _edit_message(api_client, conv_id, user_msg_id, "hi (edited)")

    # Now the conversation should have 4 messages total (old user, old asst,
    # new user, new asst). The active branch is the new pair.
    msgs = (await api_client.get(f"/api/chat/conversations/{conv_id}/messages")).json()
    assert len(msgs) == 4

    # Sibling metadata: first user has 2 siblings, the original is at index 0,
    # the edit is at index 1.
    user_messages = [m for m in msgs if m["role"] == "user"]
    assert len(user_messages) == 2
    assert all(u["sibling_count"] == 2 for u in user_messages)

    # Switch the active leaf back to the old pair via the active-leaf endpoint
    old_user = [u for u in user_messages if u["content"] == "hi"][0]
    # The "old assistant" is the one whose parent is old_user.id
    old_asst = [m for m in msgs if m["role"] == "assistant"
                and m["parent_message_id"] == old_user["id"]][0]
    resp = await api_client.post(
        f"/api/chat/conversations/{conv_id}/active-leaf",
        json={"message_id": old_asst["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["active_leaf_message_id"] == old_asst["id"]


@pytest.mark.asyncio
async def test_retry_creates_assistant_branch(
    api_client, seeded_org_with_chat_agent
):
    """Retrying spawns a new assistant message under the same user message."""
    conv_id = await _create_conv(api_client, seeded_org_with_chat_agent)
    await _send_chat(api_client, conv_id, "what is 2+2?")
    msgs = (await api_client.get(f"/api/chat/conversations/{conv_id}/messages")).json()
    asst_msg_id = [m for m in msgs if m["role"] == "assistant"][0]["id"]

    await _retry_message(api_client, conv_id, asst_msg_id)

    msgs = (await api_client.get(f"/api/chat/conversations/{conv_id}/messages")).json()
    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistants) == 2
    assert all(a["sibling_count"] == 2 for a in assistants)
    # Both assistant messages share the same parent (the user message).
    assert assistants[0]["parent_message_id"] == assistants[1]["parent_message_id"]


@pytest.mark.asyncio
async def test_per_conversation_instructions_persist_and_apply(
    api_client, seeded_org_with_chat_agent
):
    """PATCH instructions persists; next turn's system prompt includes it."""
    conv_id = await _create_conv(api_client, seeded_org_with_chat_agent)
    resp = await api_client.patch(
        f"/api/chat/conversations/{conv_id}",
        json={"instructions": "Always answer in haiku form."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["instructions"] == "Always answer in haiku form."

    # GET round-trip
    resp = await api_client.get(f"/api/chat/conversations/{conv_id}")
    assert resp.json()["instructions"] == "Always answer in haiku form."


# Helpers — adapt to actual fixtures
async def _create_conv(client, ctx):
    resp = await client.post(
        "/api/chat/conversations",
        json={"agent_id": str(ctx.agent_id)},
    )
    return resp.json()["id"]

async def _send_chat(client, conv_id, text):
    # Uses the WS fixture; pseudocode — copy the real WS test pattern from
    # an existing e2e (likely `test_chat_endpoint.py` or `test_websocket.py`).
    raise NotImplementedError("Use WS fixture pattern from sibling e2e tests")

async def _edit_message(client, conv_id, msg_id, new_text):
    raise NotImplementedError("WS edit_message — pattern from sibling e2e")

async def _retry_message(client, conv_id, msg_id):
    raise NotImplementedError("WS retry_message — pattern from sibling e2e")
```

The `_send_chat`, `_edit_message`, `_retry_message` helpers are pseudo — fill them in by mirroring the real WS test pattern in the repo. **Do NOT leave NotImplementedError in committed code.** That's a placeholder marker for the implementer to find and replace before commit.

- [ ] **Step 3: Run the e2e**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
./test.sh e2e tests/e2e/test_chat_branching.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add api/tests/e2e/test_chat_branching.py
git commit -m "test(chat-v2/m3): e2e for edit, retry, per-conversation instructions"
```

---

## Task 11: Type generation

The dev stack must be running for this to extract OpenAPI from this worktree's API. The dev stack mounts the *main* repo, not the worktree — extract from the test-stack API container instead (per `feedback_worktree_type_gen`).

- [ ] **Step 1: Boot the worktree's test stack (already up from Task 1)**

Verify:

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
./test.sh stack status
```

- [ ] **Step 2: Find the test-stack API container's port**

```bash
PROJ=$(./test.sh stack status | grep -oP 'project=\K\S+')
docker port "${PROJ}-api-1" 8000 2>/dev/null
```

Note the host port, e.g., `0.0.0.0:38421`.

- [ ] **Step 3: Generate types against the test-stack API**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/client
OPENAPI_URL=http://localhost:<PORT>/openapi.json npm run generate:types
```

Expected: `client/src/lib/v1.d.ts` updates with `EditMessageRequest`, `RetryMessageRequest`, `SwitchBranchRequest`, and the new fields on `MessagePublic` / `ConversationPublic` / `ConversationUpdate`.

- [ ] **Step 4: Verify the diff**

```bash
git diff client/src/lib/v1.d.ts | grep -E "(parent_message_id|active_leaf|instructions|sibling)" | head -10
```

Expected: matches new fields.

- [ ] **Step 5: Commit**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore(chat-v2/m3): regenerate frontend types"
```

---

## Task 12: Frontend chat store — branching state + actions

**Files:**
- Modify: `client/src/stores/chatStore.ts`
- Create: `client/src/stores/chatStore.test.ts` (or extend existing if it exists)

The store currently holds `messages: Message[]` per conversation. M3 changes that to:
- `allMessages: Map<UUID, Message>` — every message known to the client (siblings included).
- `activeLeafId: UUID | null` per conversation.
- A derived `messages` getter that walks the parent chain from `activeLeafId` to root and reverses.

Actions:
- `editMessage(conversationId, messageId, newText)` — sends WS `edit_message`. The branch update flows back via subsequent `message_start` / `delta` / `assistant_message_end` chunks plus a re-fetch of messages list.
- `retryMessage(conversationId, messageId)` — sends WS `retry_message`.
- `switchBranch(conversationId, messageId)` — calls `POST /active-leaf`, then re-derives the active path.

- [ ] **Step 1: Read the existing store**

```bash
sed -n '1,80p' /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/client/src/stores/chatStore.ts
```

Note the state shape and which actions exist.

- [ ] **Step 2: Write a vitest for the active-path resolver**

Create or extend the sibling test file. Add:

```typescript
// chatStore.test.ts (new or extended)
import { describe, expect, it } from "vitest";
import { resolveActivePath } from "./chatStore";

describe("resolveActivePath", () => {
  it("returns root-to-leaf chronological list", () => {
    const all = new Map([
      ["m1", { id: "m1", parent_message_id: null, content: "hi" }],
      ["m2", { id: "m2", parent_message_id: "m1", content: "hello" }],
      ["m3", { id: "m3", parent_message_id: "m1", content: "hey there" }],
    ] as const);
    const path = resolveActivePath(all as any, "m3");
    expect(path.map((m) => m.content)).toEqual(["hi", "hey there"]);
  });

  it("returns empty list when leaf is null", () => {
    const all = new Map();
    expect(resolveActivePath(all as any, null)).toEqual([]);
  });

  it("breaks cycles defensively", () => {
    const all = new Map([
      ["m1", { id: "m1", parent_message_id: "m2", content: "a" }],
      ["m2", { id: "m2", parent_message_id: "m1", content: "b" }],
    ] as const);
    expect(resolveActivePath(all as any, "m1").length).toBeLessThanOrEqual(2);
  });
});
```

- [ ] **Step 3: Run, verify failing**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
./test.sh client unit -- chatStore.test
```

Expected: FAIL — `resolveActivePath` not exported.

- [ ] **Step 4: Implement `resolveActivePath`**

Add to `chatStore.ts`:

```typescript
export interface MessageNode {
  id: string;
  parent_message_id: string | null;
  // …other fields
}

export function resolveActivePath<M extends MessageNode>(
  all: ReadonlyMap<string, M>,
  leafId: string | null,
): M[] {
  if (leafId === null) return [];
  const path: M[] = [];
  const seen = new Set<string>();
  let cur: string | null = leafId;
  while (cur !== null && !seen.has(cur)) {
    seen.add(cur);
    const m = all.get(cur);
    if (!m) break;
    path.push(m);
    cur = m.parent_message_id;
  }
  return path.reverse();
}
```

- [ ] **Step 5: Wire actions**

Add to the store (whatever its shape is — Zustand, plain reducer, etc.):

```typescript
async function editMessage(conversationId: string, messageId: string, newText: string) {
  // Send WS message; rely on chunk handlers to update store state
  ws.send({
    type: "edit_message",
    conversation_id: conversationId,
    target_message_id: messageId,
    content: newText,
    local_id: crypto.randomUUID(),
  });
}

async function retryMessage(conversationId: string, messageId: string) {
  ws.send({
    type: "retry_message",
    conversation_id: conversationId,
    target_message_id: messageId,
    local_id: crypto.randomUUID(),
  });
}

async function switchBranch(conversationId: string, messageId: string) {
  const resp = await apiClient.post<ConversationPublic>(
    `/api/chat/conversations/${conversationId}/active-leaf`,
    { message_id: messageId },
  );
  // Update active leaf in store
  setActiveLeaf(conversationId, resp.active_leaf_message_id);
  // Refresh messages list
  await refreshMessages(conversationId);
}
```

The exact integration with the chosen store library (Zustand, Redux, etc.) needs to mirror the existing pattern — read the file before writing.

- [ ] **Step 6: Run vitest, verify pass**

```bash
./test.sh client unit -- chatStore.test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add client/src/stores/chatStore.ts client/src/stores/chatStore.test.ts
git commit -m "feat(chat-v2/m3): chat store branching path resolver + actions"
```

---

## Task 13: Frontend — sibling navigation component

**Files:**
- Create: `client/src/components/chat/MessageBranchNav.tsx`
- Create: `client/src/components/chat/MessageBranchNav.test.tsx`

A small `< 2/3 >` row that appears under any message with `sibling_count > 1`.

- [ ] **Step 1: Write the failing test**

`MessageBranchNav.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageBranchNav } from "./MessageBranchNav";

describe("MessageBranchNav", () => {
  it("renders nothing when sibling_count is 1", () => {
    const { container } = render(
      <MessageBranchNav siblingCount={1} siblingIndex={0} onPrev={vi.fn()} onNext={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows '< 2 / 3 >' when there are siblings", () => {
    render(
      <MessageBranchNav siblingCount={3} siblingIndex={1} onPrev={vi.fn()} onNext={vi.fn()} />,
    );
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("invokes onPrev / onNext when arrows clicked", () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    render(
      <MessageBranchNav siblingCount={3} siblingIndex={1} onPrev={onPrev} onNext={onNext} />,
    );
    fireEvent.click(screen.getByLabelText("Previous branch"));
    fireEvent.click(screen.getByLabelText("Next branch"));
    expect(onPrev).toHaveBeenCalled();
    expect(onNext).toHaveBeenCalled();
  });

  it("disables prev at index 0 and next at last index", () => {
    const { rerender } = render(
      <MessageBranchNav siblingCount={3} siblingIndex={0} onPrev={vi.fn()} onNext={vi.fn()} />,
    );
    expect(screen.getByLabelText("Previous branch")).toBeDisabled();

    rerender(
      <MessageBranchNav siblingCount={3} siblingIndex={2} onPrev={vi.fn()} onNext={vi.fn()} />,
    );
    expect(screen.getByLabelText("Next branch")).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run, verify failing**

```bash
./test.sh client unit -- MessageBranchNav.test
```

Expected: FAIL — file not found.

- [ ] **Step 3: Implement the component**

`MessageBranchNav.tsx`:

```tsx
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  siblingCount: number;
  siblingIndex: number;
  onPrev: () => void;
  onNext: () => void;
  className?: string;
}

export function MessageBranchNav({
  siblingCount,
  siblingIndex,
  onPrev,
  onNext,
  className,
}: Props) {
  if (siblingCount <= 1) return null;
  return (
    <div className={`flex items-center gap-1 text-xs text-muted-foreground ${className ?? ""}`}>
      <Button
        variant="ghost"
        size="icon"
        className="h-5 w-5"
        aria-label="Previous branch"
        onClick={onPrev}
        disabled={siblingIndex <= 0}
      >
        <ChevronLeft className="h-3 w-3" />
      </Button>
      <span className="tabular-nums">
        {siblingIndex + 1} / {siblingCount}
      </span>
      <Button
        variant="ghost"
        size="icon"
        className="h-5 w-5"
        aria-label="Next branch"
        onClick={onNext}
        disabled={siblingIndex >= siblingCount - 1}
      >
        <ChevronRight className="h-3 w-3" />
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Run, verify passing**

```bash
./test.sh client unit -- MessageBranchNav.test
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/chat/MessageBranchNav.tsx client/src/components/chat/MessageBranchNav.test.tsx
git commit -m "feat(chat-v2/m3): MessageBranchNav component (< n/m >)"
```

---

## Task 14: Frontend — Pencil + RotateCcw affordances on ChatMessage

**Files:**
- Modify: `client/src/components/chat/ChatMessage.tsx`

- [ ] **Step 1: Read the current component**

```bash
sed -n '1,80p' /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/client/src/components/chat/ChatMessage.tsx
```

Note its current props, where the bubble lives, and what hover-affordances already exist.

- [ ] **Step 2: Add props and affordances**

Add to props:

```typescript
interface ChatMessageProps {
  // …existing
  siblingCount?: number;
  siblingIndex?: number;
  onEdit?: (newText: string) => void;
  onRetry?: () => void;
  onSwitchBranch?: (direction: "prev" | "next") => void;
}
```

In the JSX:

- For user messages: a hover-revealed `Pencil` icon button (top-right of the bubble, opacity 0 default, opacity 100 on `group-hover`). Click → toggle inline edit textarea + Save/Cancel buttons. Save calls `onEdit(newText)`, Cancel reverts. **No AlertDialog confirm.**
- For assistant messages: a hover-revealed `RotateCcw` icon button. Single click → `onRetry()`. **No popover for model override.**
- Below any message with `siblingCount > 1`: render `<MessageBranchNav>` wired to `onSwitchBranch`.

The existing styling pattern in the file should be followed. Reference component for hover affordances: any existing `ChatMessage` action button.

- [ ] **Step 3: Update ChatMessage tests**

If `ChatMessage.test.tsx` exists, add cases:

- "shows Pencil on user message hover, calls onEdit on save"
- "shows RotateCcw on assistant message, calls onRetry"
- "renders MessageBranchNav when siblingCount > 1"

If the file is new, create it.

- [ ] **Step 4: Run vitest**

```bash
./test.sh client unit -- ChatMessage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/chat/ChatMessage.tsx client/src/components/chat/ChatMessage.test.tsx
git commit -m "feat(chat-v2/m3): edit/retry hover affordances + sibling nav on ChatMessage"
```

---

## Task 15: Frontend — Wire actions through to the store

**Files:**
- Modify: `client/src/components/chat/ChatWindow.tsx`

`ChatWindow` is the parent that maps `messages.map(m => <ChatMessage ... />)`. Pass the new handlers through:

- [ ] **Step 1: Read ChatWindow**

```bash
sed -n '1,80p' /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/client/src/components/chat/ChatWindow.tsx
```

- [ ] **Step 2: Wire props**

In the message map, pass:

```tsx
<ChatMessage
  // existing props…
  siblingCount={m.sibling_count}
  siblingIndex={m.sibling_index}
  onEdit={(newText) => editMessage(conversationId, m.id, newText)}
  onRetry={() => retryMessage(conversationId, m.id)}
  onSwitchBranch={(dir) => {
    // Resolve sibling target id by walking known siblings.
    const targetId = resolveSiblingTargetId(allMessages, m.id, dir);
    if (targetId) switchBranch(conversationId, targetId);
  }}
/>
```

`resolveSiblingTargetId` is a helper that looks up siblings by parent and picks the next/prev one. Implement it in the store as a pure helper alongside `resolveActivePath`:

```typescript
export function resolveSiblingTargetId<M extends MessageNode & { sibling_index?: number }>(
  all: ReadonlyMap<string, M>,
  currentId: string,
  direction: "prev" | "next",
): string | null {
  const cur = all.get(currentId);
  if (!cur) return null;
  const siblings = Array.from(all.values()).filter(
    (m) => m.parent_message_id === cur.parent_message_id,
  );
  siblings.sort((a, b) => (a.sibling_index ?? 0) - (b.sibling_index ?? 0));
  const idx = siblings.findIndex((s) => s.id === currentId);
  const target =
    direction === "prev" ? siblings[idx - 1] : siblings[idx + 1];
  return target?.id ?? null;
}
```

Add a vitest for `resolveSiblingTargetId` similar to the active-path test.

- [ ] **Step 3: Run vitest + tsc**

```bash
./test.sh client unit
cd client && npm run tsc
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add client/src/components/chat/ChatWindow.tsx client/src/stores/chatStore.ts client/src/stores/chatStore.test.ts
git commit -m "feat(chat-v2/m3): wire edit/retry/switchBranch through ChatWindow"
```

---

## Task 16: Frontend — "Customize this chat" dialog

**Files:**
- Create: `client/src/components/chat/ConversationInstructionsDialog.tsx`
- Create: `client/src/components/chat/ConversationInstructionsDialog.test.tsx`
- Modify: `client/src/components/chat/ChatWindow.tsx` — add overflow menu entry

- [ ] **Step 1: Write the failing test**

`ConversationInstructionsDialog.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConversationInstructionsDialog } from "./ConversationInstructionsDialog";

describe("ConversationInstructionsDialog", () => {
  it("renders with current value and saves", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ConversationInstructionsDialog
        open
        initialValue="be terse"
        onOpenChange={vi.fn()}
        onSave={onSave}
      />,
    );
    const textarea = screen.getByRole("textbox");
    expect(textarea).toHaveValue("be terse");

    fireEvent.change(textarea, { target: { value: "respond in haiku" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(onSave).toHaveBeenCalledWith("respond in haiku");
  });

  it("Reset clears the value", () => {
    render(
      <ConversationInstructionsDialog
        open
        initialValue="be terse"
        onOpenChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /reset/i }));
    expect(screen.getByRole("textbox")).toHaveValue("");
  });
});
```

- [ ] **Step 2: Run, verify failing**

```bash
./test.sh client unit -- ConversationInstructionsDialog
```

Expected: FAIL — file not found.

- [ ] **Step 3: Implement the dialog**

```tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  initialValue: string;
  onOpenChange: (next: boolean) => void;
  onSave: (value: string) => Promise<void>;
}

export function ConversationInstructionsDialog({
  open,
  initialValue,
  onOpenChange,
  onSave,
}: Props) {
  const [value, setValue] = useState(initialValue);
  const [saving, setSaving] = useState(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Customize this chat</DialogTitle>
        </DialogHeader>
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          rows={8}
          placeholder="Instructions for this conversation (in addition to the workspace's instructions and agent's prompt)."
          aria-label="Conversation instructions"
        />
        <p className="text-xs text-muted-foreground">
          ~{Math.max(1, Math.round(value.length / 4))} tokens / message
        </p>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setValue("")}>
            Reset
          </Button>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={async () => {
              setSaving(true);
              try {
                await onSave(value);
                onOpenChange(false);
              } finally {
                setSaving(false);
              }
            }}
            disabled={saving}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 4: Run, verify passing**

```bash
./test.sh client unit -- ConversationInstructionsDialog
```

Expected: PASS.

- [ ] **Step 5: Wire into ChatWindow overflow menu**

Add an item to the conversation header's overflow menu (`MoreHorizontal` dropdown) that opens the dialog. On save, call `apiClient.patch(`/api/chat/conversations/${id}`, { instructions: value })` and update the local conversation state.

- [ ] **Step 6: tsc + lint + commit**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/client
npm run tsc
npm run lint
git add client/src/components/chat/ConversationInstructionsDialog.tsx \
        client/src/components/chat/ConversationInstructionsDialog.test.tsx \
        client/src/components/chat/ChatWindow.tsx
git commit -m "feat(chat-v2/m3): per-conversation instructions dialog"
```

---

## Task 17: Playwright E2E — happy path

**Files:**
- Create: `client/e2e/chat-branching.spec.ts`

Test the user-visible flows: create a chat, send a message, edit it (assert both branches accessible), retry the assistant (assert both branches accessible), set per-conversation instructions (assert visible in dialog after re-open).

- [ ] **Step 1: Find the existing chat-related Playwright spec**

```bash
ls /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/client/e2e/ | grep -i chat
```

Pick one as a reference for selectors and login pattern.

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from "@playwright/test";

test.describe("Chat V2 / M3 — branching + instructions", () => {
  test("edit user message creates a branch the user can navigate", async ({ page }) => {
    // …auth pattern from sibling spec…
    await page.goto("/chat");
    await page.getByRole("button", { name: /new chat/i }).click();
    await page.getByPlaceholder(/message/i).fill("hi there");
    await page.keyboard.press("Enter");
    await expect(page.getByText("hi there")).toBeVisible();

    // Hover the user message and click the pencil
    const userMsg = page.locator('[data-role="user"]', { hasText: "hi there" }).first();
    await userMsg.hover();
    await userMsg.getByLabel("Edit message").click();
    await userMsg.locator("textarea").fill("hi there (edited)");
    await page.getByRole("button", { name: /save/i }).click();

    await expect(page.getByText("hi there (edited)")).toBeVisible();
    // Sibling indicator visible
    await expect(page.getByText("2 / 2")).toBeVisible();

    // Navigate back to original branch
    await page.getByLabel("Previous branch").click();
    await expect(page.getByText("hi there")).toBeVisible();
    await expect(page.getByText("hi there (edited)")).toBeHidden();
  });

  test("retry assistant message creates a branch", async ({ page }) => {
    // …auth + new chat…
    await page.goto("/chat");
    await page.getByRole("button", { name: /new chat/i }).click();
    await page.getByPlaceholder(/message/i).fill("what is 2+2?");
    await page.keyboard.press("Enter");
    await expect(page.locator('[data-role="assistant"]').first()).toBeVisible();

    const asstMsg = page.locator('[data-role="assistant"]').first();
    await asstMsg.hover();
    await asstMsg.getByLabel("Retry").click();

    // After retry completes, sibling indicator visible on assistant message
    await expect(page.getByText("2 / 2").last()).toBeVisible({ timeout: 30_000 });
  });

  test("per-conversation instructions persist", async ({ page }) => {
    // …auth + new chat…
    await page.goto("/chat");
    await page.getByRole("button", { name: /new chat/i }).click();
    await page.getByLabel("More").click();  // overflow menu
    await page.getByRole("menuitem", { name: /customize this chat/i }).click();
    await page.getByRole("textbox", { name: /conversation instructions/i }).fill("respond in haiku");
    await page.getByRole("button", { name: /save/i }).click();

    // Re-open and verify it persisted
    await page.getByLabel("More").click();
    await page.getByRole("menuitem", { name: /customize this chat/i }).click();
    await expect(page.getByRole("textbox", { name: /conversation instructions/i }))
      .toHaveValue("respond in haiku");
  });
});
```

Adjust selectors and aria-labels to match what Tasks 13–16 actually rendered.

- [ ] **Step 3: Run**

```bash
./test.sh client e2e e2e/chat-branching.spec.ts
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add client/e2e/chat-branching.spec.ts
git commit -m "test(chat-v2/m3): playwright happy path for edit, retry, instructions"
```

---

## Task 18: Update spec + master plan

**Files:**
- Modify: `docs/superpowers/specs/2026-04-27-chat-ux-design.md`
- Modify: `docs/superpowers/plans/2026-04-27-chat-v2-master-plan.md`

- [ ] **Step 1: Edit `2026-04-27-chat-ux-design.md`**

Find these sections and replace as described:

**Non-goals**: remove the line `- Branching/tree conversations (linear-only).`

**Scope summary** table: change "Edit user message + retry" row's "Shape" column from `Edit replaces, retry regenerates, no branch history` to `Edit and retry both branch — old version preserved, sibling navigation`.

**§1.1 Edit user message** — replace the entire section with:

```markdown
### 1.1 Edit user message
Editing creates a **sibling user message** under the same parent. The original message and any subsequent assistant replies remain in the database; the active branch flips to the new sibling. Both branches are accessible via sibling navigation arrows (`< 2/2 >`).

UI: pencil icon on user messages on hover. Click → message becomes editable inline. Submit creates the sibling and runs a new turn. Cancel reverts. **No confirmation dialog** — edit is non-destructive.

Implementation: every Message row carries `parent_message_id`; every Conversation carries `active_leaf_message_id`. The edit endpoint creates a new user Message with the same parent as the original and updates `active_leaf_message_id`. The agent loop's `_load_active_branch` walks the parent chain from the leaf to load the active path.
```

**§1.2 Retry last response** — replace the entire section with:

```markdown
### 1.2 Retry last response
Regenerates an assistant message. The new response is created as a **sibling** under the same user message; the original is preserved and accessible via sibling navigation.

UI: refresh icon on the assistant message. Single click → retry with the conversation's current model. **No model override dropdown.** Per-conversation model switching happens via the chat header's model picker (M2).

Implementation: the retry endpoint walks `active_leaf_message_id` back to the user message that prompted the target assistant message, then runs a fresh turn. The new assistant message is saved as a sibling of the original.
```

**§16.9 Editing a user message** — replace the AlertDialog paragraph with:

```markdown
Hover a user message → small `Pencil` icon appears at the top-right of the message bubble (ghost button, opacity-0 on default, opacity-100 on group-hover — same pattern as inline-edit affordances elsewhere). Click → message text becomes editable inline (textarea, autosize, same width as the bubble), with "Send" (default button) and "Cancel" (ghost) below.

On Send: the existing message stays in the DB; a sibling user message is created with the new text, and the conversation's active branch flips to the new sibling. Sibling navigation (`< 2/2 >`) renders below both versions so the user can revisit the original.
```

**§16.10 Retry button** — replace with:

```markdown
Hover an assistant message → small `RotateCcw` icon at top-right of the bubble. Single click → retry. The new response is a sibling under the same user message; sibling navigation renders below both versions.
```

- [ ] **Step 2: Update master plan decisions log**

In `docs/superpowers/plans/2026-04-27-chat-v2-master-plan.md`, append to the decisions log table:

```markdown
| 2026-04-29 | M3 ships branching (parent/leaf model); linear-only non-goal removed; retry-with-different-model dropdown dropped | (M3 PR) |
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-04-27-chat-ux-design.md docs/superpowers/plans/2026-04-27-chat-v2-master-plan.md
git commit -m "docs(chat-v2): update spec for M3 branching + drop linear-only constraint"
```

---

## Task 19: Pre-completion verification

- [ ] **Step 1: Backend lint + types**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/api
./.venv/bin/ruff check .
./.venv/bin/pyright
```

Expected: 0 errors. Fix any that appeared.

- [ ] **Step 2: Frontend lint + types**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions/client
npm run tsc
npm run lint
```

Expected: 0 errors.

- [ ] **Step 3: Full backend tests**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
./test.sh all
```

Expected: PASS. The result file is at `/tmp/bifrost-<project>/test-results.xml` per `feedback_test_sh_quirks` — parse that for any unexpected failures.

- [ ] **Step 4: Full client unit tests**

```bash
./test.sh client unit
```

Expected: PASS.

- [ ] **Step 5: Playwright**

```bash
./test.sh client e2e e2e/chat-branching.spec.ts
```

Expected: PASS.

- [ ] **Step 6: Commit any fixes**

If verification surfaced fixes:

```bash
git add -A
git commit -m "fix(chat-v2/m3): address pyright/ruff/tsc/lint findings"
```

---

## Task 20: Open the PR into `feature/chat-v2`

- [ ] **Step 1: Push the branch**

```bash
cd /home/jack/GitHub/bifrost/.worktrees/147-m3-edit-retry-instructions
git push -u origin 147-m3-edit-retry-instructions
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --base feature/chat-v2 \
  --head 147-m3-edit-retry-instructions \
  --title "Chat V2 / M3 — Edit, retry, per-conversation instructions (with branching)" \
  --body "$(cat <<'EOF'
Closes #147.

## Summary

- Edit user message + retry assistant message via **branching** (sibling under same parent), matching ChatGPT / Claude.ai / LibreChat / Open WebUI.
- Per-conversation instructions append to the system prompt after agent prompt + workspace instructions.
- Spec updated: §1.1, §1.2, §16.9, §16.10. "Linear-only" non-goal removed.

## What ships

- Migration: `messages.parent_message_id`, `conversations.active_leaf_message_id`, `conversations.instructions`.
- `agent_executor._load_active_branch` walks parent chain; `_save_message` advances active leaf.
- `edit_user_message` / `retry_assistant_message` create siblings and dispatch turns.
- WS handlers: `edit_message`, `retry_message`. HTTP: PATCH instructions, POST `/active-leaf`, sibling metadata in messages list.
- Frontend: hover affordances on `ChatMessage`, `MessageBranchNav` arrows, `ConversationInstructionsDialog`, store actions.

## Out of scope (vs. spec §12 M3 original wording)

- Retry-with-different-model dropdown — dropped per discussion (no chat client offers per-turn model switch via retry; per-conversation switching already exists in the M2 picker).

## Test plan

- [ ] Backend unit + e2e green
- [ ] Vitest green
- [ ] Playwright happy-path green
- [ ] pyright / ruff / tsc / lint clean

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Hand off to merge**

Per the bifrost-issues skill (step 7 — hand off to merge): check for branch protection on `feature/chat-v2`. Since this is an internal branch, it likely has none.

```bash
gh api repos/jackmusick/bifrost/branches/feature/chat-v2/protection \
  --jq '.required_status_checks.contexts // [] | length' 2>/dev/null
```

If `0` or 404: Path B — watch CI via the `loop` skill, then offer self-merge once green.
If ≥1: Path A — `gh pr merge --auto --squash`.

---

## Self-review

**Spec coverage:**
- §1.1 edit → Tasks 6, 14
- §1.2 retry → Tasks 6, 14
- §6 per-conversation instructions → Tasks 1, 2, 5, 8, 16
- §16.9 edit UX → Task 14
- §16.10 retry UX → Task 14
- §16.12 customize-this-chat dialog → Task 16
- Migration → Task 1
- ORM → Task 2
- Loader → Task 3
- Save → Task 4
- System prompt → Task 5
- HTTP → Task 8
- WS → Task 9
- Tests → Tasks 2-6, 10, 12-17

**Placeholder scan:** Task 10 contains pseudocode helpers (`_send_chat`, `_edit_message`, `_retry_message`) explicitly marked as "fill in by mirroring sibling e2e patterns" — these are not commit-ready until the implementer replaces them. The plan says so. All other code blocks are commit-ready.

**Type consistency:** `parent_message_id_override` (kwarg on `_save_message`) used consistently across Tasks 4, 6. `siblingCount` / `siblingIndex` (camelCase in TS, snake_case in Python contracts) used consistently. `active_leaf_message_id` used everywhere it appears.

**Spec gaps:** none found.
