"""Schema gate decision logic and wait loop, against fake and real revision sources."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from shared.schema_gate import (
    SchemaGateTimeout,
    _known_ancestors,
    _script_directory,
    is_at_or_ahead,
    wait_for_schema,
)

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


def test_real_history_treats_a_newer_revision_as_ahead() -> None:
    """An older image must start against a database a newer image migrated.

    Alembic reports an unknown revision with ``ResolutionError``, which is not
    a ``CommandError``; the fakes above cannot catch that difference.
    """
    script = _script_directory()
    ancestors_of = _known_ancestors(script)

    assert ancestors_of("20991231_from_a_newer_image") is None
    assert is_at_or_ahead(
        {"20991231_from_a_newer_image"}, script.get_heads(), ancestors_of
    )


@pytest.mark.asyncio
async def test_init_skips_migrations_on_a_database_a_newer_image_migrated(monkeypatch) -> None:
    """A rollback starts an older image's init against a newer database; it
    must not run ``alembic upgrade``, which cannot find the newer revision."""
    from scripts import init_container

    async def newer(engine, **_kwargs):  # type: ignore[no-untyped-def]
        return frozenset({"20991231_from_a_newer_release"})

    def must_not_migrate() -> bool:
        raise AssertionError("alembic upgrade ran against a newer database")

    monkeypatch.setattr("shared.schema_gate.revisions_from_a_newer_image", newer)
    monkeypatch.setattr(init_container, "run_migrations", must_not_migrate)
    monkeypatch.setattr(init_container, "warm_requirements_cache", AsyncMock(return_value=True))

    assert await init_container.main() == 0


@pytest.mark.asyncio
async def test_a_revision_missing_from_this_image_is_from_a_newer_one() -> None:
    from shared.schema_gate import revisions_from_a_newer_image

    class _Engine:
        def connect(self):  # type: ignore[no-untyped-def]
            return _Connection()

    class _Connection:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return False

        async def execute(self, _statement):  # type: ignore[no-untyped-def]
            return [("c",), ("z",)]

    newer = await revisions_from_a_newer_image(_Engine(), ancestors_of=_ancestors_of)  # type: ignore[arg-type]
    assert newer == {"z"}
