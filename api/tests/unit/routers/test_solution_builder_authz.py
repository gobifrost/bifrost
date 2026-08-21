from __future__ import annotations

from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from types import SimpleNamespace
from pathlib import Path
import sys
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.solution_builder import (
    PrivateSolutionCreate,
    RunTurnRequest,
    UndoRequest,
)
from src.services.authorization import AuthorizationBoundary


_MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "routers" / "solution_builder.py"
)
_SPEC = spec_from_file_location("solution_builder_router_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
solution_builder = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = solution_builder
_SPEC.loader.exec_module(solution_builder)

BuilderRequestContext = solution_builder.BuilderRequestContext
list_sessions = solution_builder.list_sessions
list_solutions = solution_builder.list_solutions
list_turns = solution_builder.list_turns
run_turn = solution_builder.run_turn
undo_to_revision = solution_builder.undo_to_revision


@dataclass
class FakeAuthorization:
    capabilities: set[str]

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.requester = SimpleNamespace(
            user_id=uuid4(),
            email="builder@example.com",
            organization_id=uuid4(),
            is_external=False,
        )
        self.selected_boundary = AuthorizationBoundary.organization(
            self.requester.organization_id
        )
        self.role_ids = ()
        self.grant_sources = ()
        self.effective_capabilities = frozenset(self.capabilities)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        self.calls.append(capability)
        if capability not in self.capabilities:
            raise HTTPException(status_code=403, detail=f"Missing capability: {capability}")

    def require_resource_boundary(self, boundary: UUID | None) -> None:  # pragma: no cover - not reached
        self.calls.append(f"boundary:{boundary}")


def _ctx(*, capabilities: set[str]) -> BuilderRequestContext:
    auth = FakeAuthorization(capabilities=capabilities)
    return BuilderRequestContext(authorization=auth, db=SimpleNamespace())


def _baseline_caps() -> set[str]:
    return {"builder.read", "builder.execute", "solutions.read", "solutions.readwrite"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callable_obj", "kwargs"),
    [
        (
            undo_to_revision,
            {
                "solution_id": uuid4(),
                "body": UndoRequest(to_revision_id=uuid4(), session_id=uuid4()),
            },
        ),
        (
            run_turn,
            {
                "solution_id": uuid4(),
                "body": RunTurnRequest(session_id=uuid4(), message="hello"),
            },
        ),
    ],
)
async def test_builder_source_mutations_require_solution_build_execute(
    monkeypatch,
    callable_obj,
    kwargs,
):
    async def load_solution_target(*_args, **_kwargs):
        return SimpleNamespace(organization_id=uuid4()), SimpleNamespace(
            target_kind="solution"
        )

    monkeypatch.setattr(
        solution_builder,
        "_load_or_404",
        load_solution_target,
    )
    ctx = _ctx(capabilities=_baseline_caps())

    with pytest.raises(HTTPException) as exc:
        await callable_obj(ctx=ctx, **kwargs)

    assert exc.value.status_code == 403
    assert ctx.authorization.calls == ["solutions.build.execute"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callable_obj", "kwargs"),
    [
        (
            undo_to_revision,
            {
                "solution_id": uuid4(),
                "body": UndoRequest(to_revision_id=uuid4(), session_id=uuid4()),
            },
        ),
        (
            run_turn,
            {
                "solution_id": uuid4(),
                "body": RunTurnRequest(session_id=uuid4(), message="hello"),
            },
        ),
    ],
)
async def test_builder_source_mutations_require_solution_deploy_execute(
    monkeypatch,
    callable_obj,
    kwargs,
):
    async def load_solution_target(*_args, **_kwargs):
        return SimpleNamespace(organization_id=uuid4()), SimpleNamespace(
            target_kind="solution"
        )

    monkeypatch.setattr(
        solution_builder,
        "_load_or_404",
        load_solution_target,
    )
    ctx = _ctx(capabilities=_baseline_caps() | {"solutions.build.execute"})

    with pytest.raises(HTTPException) as exc:
        await callable_obj(ctx=ctx, **kwargs)

    assert exc.value.status_code == 403
    assert ctx.authorization.calls == [
        "solutions.build.execute",
        "solutions.deploy.execute",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callable_obj", "service_name", "result_key"),
    [
        (list_sessions, "list_builder_sessions", "sessions"),
        (list_turns, "list_builder_turns", "turns"),
    ],
)
async def test_builder_history_requires_view_access_not_build_execution(
    monkeypatch,
    callable_obj,
    service_name,
    result_key,
):
    async def allow_view(*_args, **_kwargs):
        return SimpleNamespace(), SimpleNamespace()

    async def empty_history(*_args, **_kwargs):
        return []

    monkeypatch.setattr(solution_builder, "_load_or_404", allow_view)
    monkeypatch.setattr(solution_builder, service_name, empty_history)
    ctx = _ctx(capabilities=_baseline_caps())

    result = await callable_obj(solution_id=uuid4(), ctx=ctx)

    assert getattr(result, result_key) == []
    assert ctx.authorization.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ({"builder.read", "solutions.read", "builder.execute", "repository.readwrite"}, True),
        ({"builder.read", "solutions.read", "builder.execute"}, False),
    ],
)
async def test_list_solutions_exposes_global_workspace_flag_from_platform_boundary(
    monkeypatch,
    capabilities,
    expected,
):
    async def empty_private_solutions(*_args, **_kwargs):
        return SimpleNamespace(records=[], total=0)

    async def ready(*_args, **_kwargs):
        return True, SimpleNamespace(ready=True, blockers=[])

    async def discover(*_args, **_kwargs):
        return SimpleNamespace(
            organizations=(),
            has_builder_access=True,
            can_view_all=False,
            can_open_global_workspace=expected,
            is_platform_admin=expected,
        )

    monkeypatch.setattr(solution_builder, "list_private_solutions", empty_private_solutions)
    monkeypatch.setattr(solution_builder, "get_builder_readiness", ready)
    monkeypatch.setattr(
        solution_builder,
        "discover_builder_authorization_targets",
        discover,
    )
    ctx = _ctx(capabilities=capabilities)

    result = await list_solutions(
        user=ctx.user,
        db=ctx.db,
        view="mine",
        organization_id=None,
        owner_user_id=None,
        search=None,
        limit=50,
        offset=0,
    )

    assert result.can_open_global_workspace is expected


@pytest.mark.asyncio
async def test_list_solutions_returns_organization_targets_with_builder_read_only(
    monkeypatch,
):
    organization_id = uuid4()

    async def one_private_solution(*_args, **_kwargs):
        solution = SimpleNamespace(
            id=uuid4(),
            slug="alpha",
            name="Alpha",
            visibility="private",
            owner_user_id=None,
            organization_id=organization_id,
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        project = SimpleNamespace(
            target_kind="organization",
            promotion_status="none",
        )
        return SimpleNamespace(
            records=[
                SimpleNamespace(
                    solution=solution,
                    project=project,
                    owner_name=None,
                    owner_email=None,
                    organization_name=None,
                    collaborator_access=None,
                )
            ],
            total=1,
        )

    async def ready(*_args, **_kwargs):
        return True, SimpleNamespace(ready=True, blockers=[])

    async def discover(*_args, **_kwargs):
        return SimpleNamespace(
            organizations=(
                SimpleNamespace(
                    id=organization_id,
                    can_read=True,
                    capabilities=frozenset({"builder.read"}),
                ),
            ),
            has_builder_access=True,
            can_view_all=False,
            can_open_global_workspace=False,
            is_platform_admin=False,
        )

    monkeypatch.setattr(solution_builder, "list_private_solutions", one_private_solution)
    monkeypatch.setattr(solution_builder, "get_builder_readiness", ready)
    monkeypatch.setattr(
        solution_builder,
        "discover_builder_authorization_targets",
        discover,
    )
    ctx = _ctx(capabilities={"builder.read"})

    result = await list_solutions(
        user=ctx.user,
        db=ctx.db,
        view="mine",
        organization_id=None,
        owner_user_id=None,
        search=None,
        limit=50,
        offset=0,
    )

    assert result.solutions[0].target_kind == "organization"
    assert ctx.authorization.calls == []


@pytest.mark.asyncio
async def test_create_solution_accepts_organization_targets_without_solution_readwrite(
    monkeypatch,
):
    captured: dict[str, str] = {}

    async def fake_create_private_solution(*_args, **kwargs):
        captured["target_kind"] = kwargs["target_kind"]
        solution = SimpleNamespace()
        project = SimpleNamespace(target_kind=kwargs["target_kind"])
        return solution, project

    def fake_to_dto(_solution, project, **_kwargs):
        return SimpleNamespace(target_kind=project.target_kind)

    monkeypatch.setattr(solution_builder, "create_private_solution", fake_create_private_solution)
    monkeypatch.setattr(solution_builder, "to_dto", fake_to_dto)
    ctx = _ctx(capabilities={"builder.execute", "agents.readwrite"})

    result = await solution_builder.create_solution(
        body=PrivateSolutionCreate(
            slug="alpha",
            name="Alpha",
            target_kind="organization",
        ),
        ctx=ctx,
    )

    assert captured["target_kind"] == "organization"
    assert result.target_kind == "organization"
    assert ctx.authorization.calls == ["builder.execute"]
