"""Schema gate decision logic and wait loop, against a fake revision source."""

from __future__ import annotations

import logging

import pytest

from shared.schema_gate import SchemaGateTimeout, is_at_or_ahead, wait_for_schema

# Linear history: a -> b -> c (c is head)
_ANCESTORS = {
    "a": frozenset({"a"}),
    "b": frozenset({"a", "b"}),
    "c": frozenset({"a", "b", "c"}),
}


def _ancestors_of(revision: str) -> frozenset[str] | None:
    return _ANCESTORS.get(revision)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_at_head_is_current() -> None:
    assert is_at_or_ahead({"c"}, {"c"}, _ancestors_of)


def test_behind_is_not_current() -> None:
    assert not is_at_or_ahead({"b"}, {"c"}, _ancestors_of)
    assert not is_at_or_ahead(set(), {"c"}, _ancestors_of)


def test_unknown_revision_counts_as_ahead() -> None:
    assert is_at_or_ahead({"newer"}, {"c"}, _ancestors_of)


@pytest.mark.asyncio
async def test_returns_immediately_when_current() -> None:
    clock = _FakeClock()

    async def read_current() -> set[str]:
        return {"c"}

    await wait_for_schema(
        None,  # type: ignore[arg-type]
        heads={"c"},
        read_current=read_current,
        ancestors_of=_ancestors_of,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_returns_immediately_when_ahead() -> None:
    clock = _FakeClock()

    async def read_current() -> set[str]:
        return {"newer"}

    await wait_for_schema(
        None,  # type: ignore[arg-type]
        heads={"c"},
        read_current=read_current,
        ancestors_of=_ancestors_of,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_waits_and_logs_until_database_advances(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _FakeClock()
    states = iter([{"a"}, {"b"}, {"c"}])

    async def read_current() -> set[str]:
        return next(states)

    with caplog.at_level(logging.WARNING, logger="shared.schema_gate"):
        await wait_for_schema(
            None,  # type: ignore[arg-type]
            heads={"c"},
            read_current=read_current,
            ancestors_of=_ancestors_of,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            poll_interval=5.0,
        )

    assert clock.sleeps == [5.0, 5.0]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("waiting for migrations" in m for m in warnings)
    assert "['a']" in warnings[0] and "['c']" in warnings[0]


@pytest.mark.asyncio
async def test_raises_when_behind_past_max_wait() -> None:
    clock = _FakeClock()

    async def read_current() -> set[str]:
        return {"a"}

    with pytest.raises(SchemaGateTimeout):
        await wait_for_schema(
            None,  # type: ignore[arg-type]
            heads={"c"},
            read_current=read_current,
            ancestors_of=_ancestors_of,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            poll_interval=5.0,
            max_wait=20.0,
        )

    assert clock.sleeps == [5.0] * 4
