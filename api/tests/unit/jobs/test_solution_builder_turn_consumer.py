"""At-least-once Builder queue delivery safety."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.jobs.consumers.solution_builder_turn import SolutionBuilderTurnConsumer
from src.models.orm import PlatformJob
from src.models.orm.solution_builder import SolutionBuilderTurn


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        del ex
        assert nx is True
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def register_script(self, script: str):
        async def call(*, keys: list[str], args: list[object]) -> int:
            key = keys[0]
            token = str(args[0])
            if self.values.get(key) != token:
                return 0
            if "DEL" in script:
                del self.values[key]
            return 1

        return call


class _FakeDb:
    def __init__(self, job: object, turn: object) -> None:
        self.job = job
        self.turn = turn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def get(self, model, key):
        del key
        if model is PlatformJob:
            return self.job
        if model is SolutionBuilderTurn:
            return self.turn
        raise AssertionError(f"unexpected model {model}")


@pytest.mark.asyncio
async def test_execution_claim_serializes_duplicate_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()

    @asynccontextmanager
    async def get_redis():
        yield redis

    monkeypatch.setattr("src.core.cache.redis_client.get_redis", get_redis)
    monkeypatch.setattr(
        "src.jobs.consumers.solution_builder_turn._CANCEL_CHECK_SECONDS",
        0.01,
    )
    job = SimpleNamespace(attempt=1, status="waiting")
    turn = SimpleNamespace(status="running")
    consumer = object.__new__(SolutionBuilderTurnConsumer)
    consumer._session_factory = lambda: _FakeDb(job, turn)
    job_id = uuid4()

    first = consumer._execution_claim(job_id, 1)
    assert await first.__aenter__() is True

    second = consumer._execution_claim(job_id, 1)
    second_enter = asyncio.create_task(second.__aenter__())
    await asyncio.sleep(0.03)
    assert not second_enter.done()

    await first.__aexit__(None, None, None)
    assert await asyncio.wait_for(second_enter, timeout=0.2) is True
    await second.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_execution_claim_drops_stale_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()

    @asynccontextmanager
    async def get_redis():
        yield redis

    monkeypatch.setattr("src.core.cache.redis_client.get_redis", get_redis)
    monkeypatch.setattr(
        "src.jobs.consumers.solution_builder_turn._CANCEL_CHECK_SECONDS",
        0.01,
    )
    job = SimpleNamespace(attempt=1, status="waiting")
    turn = SimpleNamespace(status="running")
    consumer = object.__new__(SolutionBuilderTurnConsumer)
    consumer._session_factory = lambda: _FakeDb(job, turn)
    job_id = uuid4()

    first = consumer._execution_claim(job_id, 1)
    assert await first.__aenter__() is True
    job.status = "succeeded"

    duplicate = consumer._execution_claim(job_id, 1)
    assert await duplicate.__aenter__() is False
    await duplicate.__aexit__(None, None, None)
    await first.__aexit__(None, None, None)
