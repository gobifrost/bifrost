"""At-least-once Builder queue delivery safety."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.consumers import solution_builder_turn as turn_module
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


class _AuthContext:
    def __init__(self) -> None:
        self.require_calls: list[str] = []
        self.checked_capabilities: list[str] = []
        self.requester = SimpleNamespace(
            user_id=uuid4(),
            email="builder@example.com",
            organization_id=uuid4(),
            name="Builder",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_external=False,
        )
        self.selected_boundary = SimpleNamespace(kind="organization")
        self.role_ids: tuple[object, ...] = ()

    def has_capability(self, capability: str) -> bool:
        self.checked_capabilities.append(capability)
        return capability in {
            "builder.execute",
            "solutions.readwrite",
            "solutions.build.execute",
            "solutions.deploy.execute",
            "repository.readwrite",
            "platform.superuser",
        }

    def has_delegated_capability(self, capability: str) -> bool:
        return capability == "builder.read"

    def require(self, capability: str) -> None:
        self.require_calls.append(capability)
        if not self.has_capability(capability):
            raise AssertionError(capability)

    def require_resource_boundary(self, organization_id):
        self.selected_boundary = SimpleNamespace(kind="organization", organization_id=organization_id)


class _FinalizeDb:
    def __init__(self) -> None:
        self.finalized = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


class _FakeArtifactStorage:
    def __init__(self) -> None:
        self.write_from_path = AsyncMock(return_value=("a" * 64, None))


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


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["global_repo", "organization"])
async def test_authorize_runtime_keeps_solution_lifecycle_caps_off_direct_targets(
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    auth = _AuthContext()
    db = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    db.scalar = AsyncMock(return_value=False)
    job_id = uuid4()
    conversation_id = uuid4()
    solution_id = uuid4()
    turn = SimpleNamespace(
        id=job_id,
        session_id=uuid4(),
        requested_by=auth.requester.user_id,
        status="queued",
    )
    session = SimpleNamespace(id=turn.session_id, solution_id=solution_id, conversation_id=conversation_id)
    solution = SimpleNamespace(
        id=solution_id,
        name="Solution",
        organization_id=auth.requester.organization_id,
        owner_user_id=auth.requester.user_id,
    )
    project = SimpleNamespace(solution_id=solution_id, target_kind=target_kind)
    conversation = SimpleNamespace(id=conversation_id, user=auth.requester)

    async def get(model, key):
        del key
        if model is PlatformJob:
            return SimpleNamespace(
                id=job_id,
                job_type="solution.builder.turn",
                attempt=1,
                status="running",
            )
        if model is SolutionBuilderTurn:
            return turn
        if model.__name__ == "SolutionBuilderSession":
            return session
        if model.__name__ == "Solution":
            return solution
        if model.__name__ == "SolutionBuilderProject":
            return project
        if model.__name__ == "User":
            return auth.requester
        raise AssertionError(f"unexpected model {model}")

    class _Rows:
        def scalar_one_or_none(self):
            return conversation

    db.get.side_effect = get
    db.execute.return_value = _Rows()
    authorize = AsyncMock(
        return_value=SimpleNamespace(
            solution=solution,
            project=project,
            principal=auth.requester,
            authorization=auth,
        )
    )
    monkeypatch.setattr(turn_module, "authorize_builder_project", authorize)
    monkeypatch.setattr(
        turn_module,
        "build_builder_runtime_profile",
        lambda solution, target_kind="solution", authorization=None: SimpleNamespace(
            id=uuid4(),
            name=f"{solution.name} Builder",
            description="Administrator global workspace proposal agent",
            system_prompt="prompt",
            bundle_path=None,
            organization_id=solution.organization_id,
            solution_id=solution.id,
            owner_user_id=solution.owner_user_id,
            system_tools=("list_files",),
            max_iterations=80,
            max_token_budget=2_000_000,
        ),
    )
    consumer = object.__new__(SolutionBuilderTurnConsumer)
    consumer._session_factory = lambda: _FakeDb(job=PlatformJob(id=job_id), turn=turn)

    runtime = await consumer._authorize_runtime(db, job_id=job_id, dispatch_attempt=1)

    assert runtime is not None
    assert authorize.await_args.kwargs["required_capabilities"] == ("builder.execute",)


@pytest.mark.asyncio
async def test_finalize_success_reauthorizes_before_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    consumer = object.__new__(SolutionBuilderTurnConsumer)
    consumer._session_factory = lambda: _FinalizeDb()
    consumer._settings = SimpleNamespace(builder_output_limit_bytes=10_000)
    runtime = SimpleNamespace(
        turn=SimpleNamespace(id=uuid4()),
        solution_id=uuid4(),
        input_sha256="d" * 64,
    )
    auth_calls: list[str] = []
    revalidated = AsyncMock(return_value=SimpleNamespace())

    async def authorize_runtime(db, *, job_id, dispatch_attempt, input_sha256=None):
        del db, job_id, dispatch_attempt, input_sha256
        auth_calls.append("reauthorized")
        return revalidated.return_value

    monkeypatch.setattr(consumer, "_authorize_runtime", authorize_runtime)
    persist = AsyncMock(return_value="sha256")
    monkeypatch.setattr(turn_module, "persist_workspace_archive", persist)
    finalize = AsyncMock()
    monkeypatch.setattr(
        turn_module,
        "BuilderAgentTurnService",
        lambda db: SimpleNamespace(finalize_agent_turn=finalize),
    )

    await consumer._finalize_success(
        runtime,
        tmp_path,
        1,
        {
            "final_text": "done",
            "tool_call_count": 0,
            "model_request_count": 0,
            "token_count_input": 0,
            "token_count_output": 0,
            "harness_diagnostics": {},
        },
    )

    persist.assert_awaited_once_with(
        workspace=tmp_path,
        turn_id=runtime.turn.id,
        dispatch_attempt=1,
        max_bytes=10_000,
    )

    assert auth_calls == ["reauthorized"]
    finalize.assert_awaited_once()
    assert finalize.await_args.kwargs["output_sha256"] == "sha256"
