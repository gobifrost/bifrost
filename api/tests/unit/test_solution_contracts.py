"""Contract-level tests for Solution DTOs.

scope is DERIVED from organization_id (NULL == global), not stored on the ORM —
so a global install must serialize scope='global', not the field default
(Codex P2 fix).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.models.contracts.solutions import (
    Solution as SolutionDTO,
    SolutionSdkUpdateBatchRequest,
    SolutionSdkUpdateBatchResponse,
    WorkspaceBundleDecision,
    WorkspaceBundleImportRequest,
    WorkspaceBundleItem,
    WorkspaceBundlePreview,
)


def _row(org_id):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug="s",
        name="S",
        organization_id=org_id,
        allow_outbound_access=False,
        git_connected=False,
        git_repo_url=None,
    )


def test_global_install_serializes_scope_global() -> None:
    dto = SolutionDTO.model_validate(_row(None))
    assert dto.scope == "global"
    assert dto.model_dump()["scope"] == "global"


def test_org_install_serializes_scope_org() -> None:
    dto = SolutionDTO.model_validate(_row(uuid.uuid4()))
    assert dto.scope == "org"
    assert dto.model_dump()["scope"] == "org"


def test_solution_sdk_aggregate_fields_default_not_applicable() -> None:
    dto = SolutionDTO.model_validate(_row(uuid.uuid4()))

    assert dto.sdk_status == "not_applicable"
    assert dto.sdk_actionable_count == 0


def test_solution_sdk_update_batch_request_requires_explicit_solution_ids() -> None:
    solution_id = uuid.uuid4()

    request = SolutionSdkUpdateBatchRequest(solution_ids=[solution_id])

    assert request.solution_ids == [solution_id]
    with pytest.raises(ValidationError):
        SolutionSdkUpdateBatchRequest(solution_ids=[])


def test_solution_sdk_update_batch_response_contains_per_app_results() -> None:
    solution_id = uuid.uuid4()
    accepted_app_id = uuid.uuid4()
    skipped_app_id = uuid.uuid4()
    job_id = uuid.uuid4()

    response = SolutionSdkUpdateBatchResponse(
        accepted=[
            {
                "application_id": accepted_app_id,
                "solution_id": solution_id,
                "job_id": job_id,
                "status": "queued",
                "reused": False,
                "notification_id": None,
            }
        ],
        skipped=[
            {
                "application_id": skipped_app_id,
                "reason": "current",
            }
        ],
    )

    assert response.accepted[0].application_id == accepted_app_id
    assert response.accepted[0].solution_id == solution_id
    assert response.accepted[0].job_id == job_id
    assert response.skipped[0].application_id == skipped_app_id
    assert response.skipped[0].reason == "current"


def test_workspace_bundle_preview_counts_only_conflicts() -> None:
    preview = WorkspaceBundlePreview(
        preview_token="token",
        package_name="Customer operations",
        package_sha256="a" * 64,
        items=[
            WorkspaceBundleItem(
                id="entity:app:dashboard", kind="app", name="Dashboard",
                classification="conflict", match_key="slug", target_id=uuid.uuid4(),
            ),
            WorkspaceBundleItem(
                id="file:modules/new.py", kind="file", name="modules/new.py",
                classification="create",
            ),
        ],
    )

    assert preview.conflict_count == 1


def test_workspace_bundle_import_rejects_duplicate_decisions() -> None:
    with pytest.raises(ValidationError, match="decisions must be unique"):
        WorkspaceBundleImportRequest(
            preview_token="token",
            decisions=[
                WorkspaceBundleDecision(item_id="entity:app:dashboard", action="keep"),
                WorkspaceBundleDecision(item_id="entity:app:dashboard", action="replace"),
            ],
        )


class _FakeScalarResult:
    def __init__(self, rows: list[object], scalar: object | None = None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self) -> list[object]:
        return self._rows

    def scalar_one_or_none(self) -> object | None:
        return self._scalar


class _FakeDb:
    def __init__(self, solutions: list[object]):
        self.solutions = solutions
        self.statements: list[object] = []
        self.commits = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _FakeScalarResult(self.solutions)
        return _FakeScalarResult([])

    async def commit(self) -> None:
        self.commits += 1


def _compiled_statement(statement: object) -> tuple[str, dict[str, object]]:
    compiled = statement.compile(compile_kwargs={"render_postcompile": True})
    return str(compiled), dict(compiled.params)


@pytest.mark.asyncio
async def test_solution_sdk_update_batch_uses_bounded_set_based_queries() -> None:
    from src.routers.solutions import batch_update_solution_app_sdks

    solutions = [
        SimpleNamespace(id=uuid.uuid4(), status="active")
        for _ in range(3)
    ]
    db = _FakeDb(solutions)
    request = SolutionSdkUpdateBatchRequest(
        solution_ids=[solution.id for solution in solutions]
    )
    ctx = SimpleNamespace(db=db)
    user = SimpleNamespace()

    response = await batch_update_solution_app_sdks(request, ctx, user)

    assert isinstance(response, SolutionSdkUpdateBatchResponse)
    assert response.accepted == []
    assert response.skipped == []
    assert db.commits == 1
    assert len(db.statements) == 3
    app_loads = [
        (sql, params)
        for sql, params in (_compiled_statement(statement) for statement in db.statements)
        if "FROM applications" in sql
    ]
    assert len(app_loads) == 1
    app_load_sql, app_load_params = app_loads[0]
    assert "applications.solution_id IN" in app_load_sql
    assert "applications.solution_id =" not in app_load_sql
    assert set(app_load_params.values()) == set(request.solution_ids)
