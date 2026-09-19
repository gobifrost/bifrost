"""Durable timers: model-requested wake-ups that consume no worker.

``sleep_until`` accepts an absolute wake time or a bounded duration plus a
reason, checkpoints the tool call, sets ``wake_at``, and parks the run in
``sleeping`` through deferred-tool suspension. Promotion is fenced on
database time: the first due scan wakes the run, journals exactly one fired
entry, and republishes once. Sleeping time never consumes active runtime.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.services.agent_runtime import run_store
from src.services.agent_runtime import types as rt

logger = logging.getLogger(__name__)

TIMER_KIND = "timer"
"""CallDeferred metadata kind for timer suspension."""

TIMER_FIRED_TEXT = "Timer fired."
"""Deterministic tool result delivered when the wake time arrives."""

MAX_TIMER_SECONDS = 30 * 24 * 3600
"""Upper bound for a single sleep (30 days)."""

MAX_TIMER_REASON_CHARS = 500


class TimerError(Exception):
    """A timer request cannot suspend (bad time, bound, or race); fail loudly."""


def parse_timer_args(arguments: dict[str, Any], *, now: datetime) -> datetime:
    """Resolve one wake time from absolute/relative args with bounds."""
    reason = arguments.get("reason", "")
    if not isinstance(reason, str) or not reason.strip():
        raise TimerError("sleep_until requires a bounded reason.")
    if len(reason) > MAX_TIMER_REASON_CHARS:
        raise TimerError(
            f"Timer reason exceeds {MAX_TIMER_REASON_CHARS} characters."
        )
    wake_at_raw = arguments.get("wake_at")
    seconds_raw = arguments.get("seconds")
    if wake_at_raw is not None and seconds_raw is not None:
        raise TimerError("sleep_until accepts wake_at or seconds, not both.")
    if wake_at_raw is not None:
        try:
            wake_at = datetime.fromisoformat(str(wake_at_raw))
        except ValueError as exc:
            raise TimerError(
                f"wake_at {wake_at_raw!r} is not ISO-8601."
            ) from exc
        if wake_at.tzinfo is None:
            wake_at = wake_at.replace(tzinfo=timezone.utc)
    elif seconds_raw is not None:
        try:
            seconds = int(seconds_raw)
        except (TypeError, ValueError) as exc:
            raise TimerError("seconds must be an integer.") from exc
        if seconds < 1:
            raise TimerError("seconds must be at least 1.")
        if seconds > MAX_TIMER_SECONDS:
            raise TimerError(
                f"Timer of {seconds}s exceeds the {MAX_TIMER_SECONDS}s bound."
            )
        wake_at = now + timedelta(seconds=seconds)
    else:
        raise TimerError("sleep_until requires wake_at or seconds.")
    if wake_at <= now:
        raise TimerError("wake_at must be in the future.")
    return wake_at


def timer_call_metadata(
    tool_call_id: str, wake_at: datetime, reason: str
) -> dict[str, Any]:
    """Metadata carried on the CallDeferred suspension."""
    return {
        "kind": TIMER_KIND,
        "tool_call_id": tool_call_id,
        "wake_at": wake_at.isoformat(),
        "reason": reason[:MAX_TIMER_REASON_CHARS],
    }


async def find_timer_intent(
    session: AsyncSession, run_id: UUID, tool_call_id: str
) -> AgentRunJournalEntry | None:
    """Return the journaled timer intent for one deferred call, if any."""
    entries = (
        await session.execute(
            select(AgentRunJournalEntry)
            .where(
                AgentRunJournalEntry.run_id == run_id,
                AgentRunJournalEntry.kind == rt.JOURNAL_TIMER,
            )
            .order_by(AgentRunJournalEntry.sequence)
        )
    ).scalars().all()
    for entry in entries:
        if (entry.data or {}).get("tool_call_id") == tool_call_id:
            return entry
    return None


async def suspend_for_timer(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    lease_token: str,
    tool_call_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Persist ``wake_at``, journal the intent, and park as ``sleeping``.

    Re-entrant: an existing intent for the same tool call is reused. A wake
    time already due (clock skew or a re-suspend after promotion) wakes the
    run immediately instead of stalling.
    """
    now = datetime.now(timezone.utc)
    wake_at = parse_timer_args(arguments, now=now)
    reason = str(arguments.get("reason", ""))[:MAX_TIMER_REASON_CHARS]

    async with session_factory() as session:
        locked = (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.id == run_id)
                .with_for_update(of=AgentRun)
            )
        ).scalar_one_or_none()
        if locked is None:
            raise TimerError(f"AgentRun {run_id} not found.")
        run_store.require_lease(locked, lease_token)
        if await find_timer_intent(session, run_id, tool_call_id) is None:
            entry = AgentRunJournalEntry(
                run_id=run_id,
                sequence=await run_store.next_journal_sequence(session, run_id),
                kind=rt.JOURNAL_TIMER,
                data={
                    "tool_call_id": tool_call_id,
                    "wake_at": wake_at.isoformat(),
                    "reason": reason,
                },
                checkpoint_sequence=None,
            )
            session.add(entry)
            await session.flush()
        await session.commit()

    async with session_factory() as session:
        await run_store.transition_waiting(
            session,
            run_id,
            lease_token,
            rt.SLEEPING,
            wake_at=wake_at,
            journal_data={"tool_call_id": tool_call_id, "reason": reason},
        )

    woken = False
    if wake_at <= datetime.now(timezone.utc):
        async with session_factory() as session:
            try:
                await run_store.wake_run(
                    session, run_id, reason="timer already due at suspend"
                )
            except rt.InvalidTransitionError:
                pass
            else:
                woken = True
        if woken:
            from src.jobs.rabbitmq import publish_message

            try:
                await publish_message("agent-runs", {"run_id": str(run_id)})
            except Exception:
                logger.warning(
                    "Timer re-nudge failed for %s", run_id, exc_info=True
                )
    return {
        "status": "suspended",
        "wake_at": wake_at.isoformat(),
        "woken": woken,
    }


async def promote_due_timers(
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime,
) -> list[UUID]:
    """Wake every due sleeping run exactly once with one fired entry.

    Database-time fenced update: only the first promotion transitions the
    row, so overlapping scheduler scans are no-ops for already-woken runs.
    Returns woken run IDs for republishing.
    """
    woken: list[UUID] = []
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.status == rt.SLEEPING,
                    AgentRun.wake_at.is_not(None),
                    AgentRun.wake_at <= now,
                )
            )
        ).scalars().all()
        candidates = [row.id for row in rows]
    for run_id in candidates:
        async with session_factory() as session:
            locked = (
                await session.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.id == run_id,
                        AgentRun.status == rt.SLEEPING,
                    )
                    .with_for_update(skip_locked=True, of=AgentRun)
                )
            ).scalar_one_or_none()
            if locked is None:
                continue
            if locked.wake_at is None or locked.wake_at > now:
                continue
            try:
                await run_store.wake_run(
                    session, run_id, reason="sleeping timer due"
                )
            except rt.InvalidTransitionError:
                continue
            intent = await find_timer_intent_for_promotion(session, run_id)
            entry = AgentRunJournalEntry(
                run_id=run_id,
                sequence=await run_store.next_journal_sequence(session, run_id),
                kind=rt.JOURNAL_TIMER,
                data={
                    "tool_call_id": intent,
                    "fired_at": now.isoformat(),
                    "result": TIMER_FIRED_TEXT,
                },
                checkpoint_sequence=None,
            )
            session.add(entry)
            await session.commit()
            woken.append(run_id)
    return woken


async def find_timer_intent_for_promotion(
    session: AsyncSession, run_id: UUID
) -> str | None:
    """Latest timer intent still awaiting its fired entry, if any."""
    entries = (
        await session.execute(
            select(AgentRunJournalEntry)
            .where(
                AgentRunJournalEntry.run_id == run_id,
                AgentRunJournalEntry.kind == rt.JOURNAL_TIMER,
            )
            .order_by(AgentRunJournalEntry.sequence)
        )
    ).scalars().all()
    intents: list[str] = []
    fired: set[str] = set()
    for entry in entries:
        data = entry.data or {}
        tool_call_id = data.get("tool_call_id")
        if tool_call_id and tool_call_id not in intents:
            intents.append(str(tool_call_id))
        if tool_call_id and data.get("result") == TIMER_FIRED_TEXT:
            fired.add(str(tool_call_id))
    for tool_call_id in reversed(intents):
        if tool_call_id not in fired:
            return tool_call_id
    return intents[-1] if intents else None


async def collect_timer_results(
    session: AsyncSession, run_id: UUID
) -> tuple[dict[str, str], set[str]]:
    """Map fired timer calls to their result text; pending call IDs second.

    A woken-then-claimed parent always has a fired entry; an intent without
    one means a bad wake and must fail loudly upstream.
    """
    entries = (
        await session.execute(
            select(AgentRunJournalEntry)
            .where(
                AgentRunJournalEntry.run_id == run_id,
                AgentRunJournalEntry.kind == rt.JOURNAL_TIMER,
            )
            .order_by(AgentRunJournalEntry.sequence)
        )
    ).scalars().all()
    intents: dict[str, dict[str, Any]] = {}
    fired: set[str] = set()
    for entry in entries:
        data = entry.data or {}
        tool_call_id = data.get("tool_call_id")
        if tool_call_id and tool_call_id not in intents:
            intents[str(tool_call_id)] = data
        if tool_call_id and data.get("result") == TIMER_FIRED_TEXT:
            fired.add(str(tool_call_id))
    results: dict[str, str] = {}
    pending: set[str] = set()
    for tool_call_id in intents:
        if tool_call_id in fired:
            results[tool_call_id] = TIMER_FIRED_TEXT
        else:
            pending.add(tool_call_id)
    return results, pending
