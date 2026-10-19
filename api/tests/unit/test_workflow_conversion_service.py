"""Review-fingerprint contracts for workflow permission conversion."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
from dataclasses import replace
import hashlib

import pytest

from shared.models import (
    WorkflowConversionApplyPayload,
    WorkflowConversionCutoverPayload,
    WorkflowConversionSelection,
)
from src.jobs.platform.base import PlatformJobFailure
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from shared import workflow_conversions as conversion
from shared.workflow_conversion_analysis import SourceFile, summarize_source


@pytest.mark.asyncio
async def test_persistent_parse_summary_is_reused_until_the_file_version_changes(
    db_session, monkeypatch
):
    from shared import workflow_conversion_analysis as analysis
    from unittest.mock import Mock

    parse = Mock(wraps=summarize_source)
    monkeypatch.setattr(analysis, "summarize_source", parse)
    source = (
        "from bifrost import integrations, tables\n\n"
        "class Helper:\n"
        "    def method(self, value):\n"
        "        return value\n\n"
        "def run():\n    return 1\n\n"
        "async def typed_sdk():\n"
        "    connection = await integrations.get('Contoso')\n"
        "    oauth = connection.oauth\n"
        "    await oauth.refresh()\n"
        "    return await tables.get('Contoso', 'order', scope='global')\n"
    )
    file = SourceFile(
        key=f"workflows/{uuid4()}.py",
        path="workflows/example.py",
        source=source,
        content_hash=hashlib.sha256(source.encode()).hexdigest(),
    )
    first = await conversion._summary(db_session, file)
    assert await conversion._summary(db_session, file) == first
    assert parse.call_count == 1
    changed = source.replace("return 1", "return 2")
    await conversion._summary(
        db_session,
        replace(
            file,
            source=changed,
            content_hash=hashlib.sha256(changed.encode()).hexdigest(),
        ),
    )
    assert parse.call_count == 2

    monkeypatch.setattr(conversion, "_sdk_permission_fingerprint", lambda: "b" * 64)
    await conversion._summary(db_session, file)
    assert parse.call_count == 3


def _workflow():
    return SimpleNamespace(
        id=uuid4(),
        is_active=True,
        path="workflows/example.py",
        function_name="run",
        access_level="authenticated",
        organization_id=None,
        solution_id=None,
        permission_mode="full",
        run_identity_id=None,
        updated_at=datetime(2026, 10, 18, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("identity", "authority", "expected"),
    [
        (None, {"identity_is_active": None}, ["The workflow run identity is unavailable."]),
        ({"id": str(uuid4())}, {"identity_is_active": False}, ["The workflow run identity is inactive."]),
        ({"id": str(uuid4())}, {"identity_is_active": True}, []),
    ],
)
def test_current_identity_status_is_informational_only(
    identity, authority, expected
) -> None:
    assert conversion._identity_warnings(identity, authority) == expected


@pytest.mark.asyncio
async def test_target_solution_admission_changes_invalidate_the_review_inputs(db_session):
    from src.models.orm.solutions import Solution

    target = Solution(slug=f"contoso-{uuid4()}", name="Contoso", allow_inbound_access=True)
    db_session.add(target)
    await db_session.flush()
    workflow = _workflow()
    first = await conversion._policy_revision_snapshot(db_session, workflow)

    target.allow_inbound_access = False
    await db_session.flush()
    second = await conversion._policy_revision_snapshot(db_session, workflow)

    assert first["solutions"] != second["solutions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("solution_id", [None, UUID("00000000-0000-0000-0000-000000000011")])
async def test_runtime_namespace_import_remains_a_resolved_analysis_dependency(
    monkeypatch: pytest.MonkeyPatch, solution_id: UUID | None
) -> None:
    workflow = _workflow()
    workflow.solution_id = solution_id
    resolve = AsyncMock(return_value={"kind": "namespace", "path": "modules/contoso"})
    monkeypatch.setattr(conversion, "resolve_module_name", resolve)
    source = SourceFile("workflows/example.py", "workflows/example.py", "", "a" * 64)

    result = await conversion._resolve_import(
        source, "modules.contoso", 0, workflow=workflow, allow_outbound_access=False
    )

    assert result is not None
    assert result.kind == "namespace"
    assert result.path == "modules/contoso"
    assert result.source == ""
    assert len(result.content_hash) == 64
    scope = resolve.call_args.kwargs["scope"]
    assert scope.solution_id == (str(solution_id) if solution_id else None)
    assert scope.global_repo_access is False


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return iter(self.value)

    def all(self):
        return self.value


class _Database:
    def __init__(self, *, rows: list[object] | None = None, job=None):
        self.rows = list(rows or [])
        self.job = job
        self.added = []
        self.commit = AsyncMock()

    def add(self, value):
        self.added.append(value)

    async def execute(self, _query):
        assert self.rows, "unexpected database query"
        return _Result(self.rows.pop(0))

    async def get(self, _model, _id):
        return self.job


def _context():
    return SimpleNamespace(
        job_id=uuid4(),
        lease_token=uuid4(),
        requested_by_user_id=str(uuid4()),
        requested_by_email="admin@example.test",
        requested_by_name="Admin",
        checkpoint=None,
        save_checkpoint=AsyncMock(),
        report=AsyncMock(),
    )


def _caller(*, is_platform_admin: bool) -> Caller:
    """Build a real authorization context for durable reauthorization gates."""
    user_id = uuid4()
    return Caller(
        UserPrincipal(
            user_id=user_id,
            email="admin@example.test",
            organization_id=None,
        ),
        AuthorizationContext(
            user_id=user_id,
            home_organization_id=None,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset({"*"}) if is_platform_admin else frozenset(),
        ),
    )


def _admin() -> Caller:
    return _caller(is_platform_admin=True)


def _patch_db_context(monkeypatch: pytest.MonkeyPatch, db: _Database) -> None:
    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(conversion, "get_db_context", context)

    @asynccontextmanager
    async def no_deployment(_solution_id):
        yield

    monkeypatch.setattr(conversion, "solution_write_lock", no_deployment)


def test_review_fingerprint_changes_when_observed_operations_or_unknowns_change() -> (
    None
):
    """A previously reviewed selection cannot apply a changed proposal."""
    stable_workflow = _workflow()
    common = {
        "source_hash": "a" * 64,
        "dependencies": {"_repo/workflows/example.py": "b" * 64},
        "workflow": stable_workflow,
        "grants": [],
        "request": None,
        "authority": {"identity_id": str(uuid4())},
        "policies": {},
        "proposed_permissions": ["secrets.read"],
    }
    reviewed = conversion._fingerprint(
        **common,
        observed_operations=["configs.get"],
        unknowns=[],
    )
    changed_observation = conversion._fingerprint(
        **common,
        observed_operations=["configs.get", "configs.list"],
        unknowns=[],
    )
    changed_review_requirement = conversion._fingerprint(
        **common,
        observed_operations=["configs.get"],
        unknowns=["Resource policy must be reviewed"],
    )

    assert reviewed != changed_observation
    assert reviewed != changed_review_requirement
    assert reviewed != conversion._fingerprint(
        **{**common, "policies": {"tables": [("table-id", "new-revision")]}},
        observed_operations=["configs.get"],
        unknowns=[],
    )


@pytest.mark.asyncio
async def test_apply_resumes_after_commit_without_replaying_the_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted applied selection is the idempotency fence before checkpointing."""
    workflow_id = uuid4()
    fingerprint = "a" * 64
    workflow = SimpleNamespace(id=workflow_id, solution_id=None)
    report = SimpleNamespace(
        source_fingerprint=fingerprint,
        status="already_declared",
        review={"applied_selection_fingerprint": fingerprint},
    )
    db = _Database(rows=[workflow, report])
    _patch_db_context(monkeypatch, db)
    monkeypatch.setattr(
        conversion, "load_human_caller", AsyncMock(return_value=_admin())
    )
    report_for_workflow = AsyncMock()
    monkeypatch.setattr(conversion, "_report_for_workflow", report_for_workflow)
    context = _context()
    payload = WorkflowConversionApplyPayload(
        analysis_job_id=uuid4(),
        selections=[
            WorkflowConversionSelection(
                workflow_id=workflow_id, source_fingerprint=fingerprint
            )
        ],
    )

    result = await conversion.run_apply(context, payload)

    assert result == {"completed": [str(workflow_id)]}
    report_for_workflow.assert_not_awaited()


def test_review_binds_entry_metadata_without_invalidating_on_routine_usage_timestamp():
    workflow = _workflow()
    inputs = {
        "source_hash": "a" * 64,
        "dependencies": {},
        "workflow": workflow,
        "grants": [],
        "request": None,
        "authority": {},
        "policies": {},
        "proposed_permissions": [],
        "observed_operations": [],
        "unknowns": [],
    }
    reviewed = conversion._fingerprint(**inputs)
    workflow.updated_at = datetime(2026, 10, 19, tzinfo=UTC)
    assert conversion._fingerprint(**inputs) == reviewed
    workflow.function_name = "different_entry"
    assert conversion._fingerprint(**inputs) != reviewed


@pytest.mark.asyncio
async def test_apply_requires_solution_review_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    fingerprint = "b" * 64
    workflow = SimpleNamespace(id=workflow_id, solution_id=uuid4())
    report = SimpleNamespace(
        source_fingerprint=fingerprint, review=None, status="needs_review"
    )
    db = _Database(rows=[workflow, report, None])
    _patch_db_context(monkeypatch, db)
    monkeypatch.setattr(
        conversion, "load_human_caller", AsyncMock(return_value=_admin())
    )
    monkeypatch.setattr(
        conversion,
        "_report_for_workflow",
        AsyncMock(
            return_value={
                "source_fingerprint": fingerprint,
                "unknowns": [],
                "identity": None,
            }
        ),
    )
    payload = WorkflowConversionApplyPayload(
        analysis_job_id=uuid4(),
        selections=[
            WorkflowConversionSelection(
                workflow_id=workflow_id, source_fingerprint=fingerprint
            )
        ],
        acknowledged_solution_requests=False,
    )

    with pytest.raises(
        PlatformJobFailure, match="reviewed permission declaration"
    ) as failure:
        await conversion.run_apply(_context(), payload)

    assert failure.value.code == "solution_consent_required"


@pytest.mark.asyncio
async def test_apply_refuses_after_requester_loses_platform_administration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_db_context(monkeypatch, _Database())
    monkeypatch.setattr(
        conversion,
        "load_human_caller",
        AsyncMock(return_value=_caller(is_platform_admin=False)),
    )
    payload = WorkflowConversionApplyPayload(
        analysis_job_id=uuid4(),
        selections=[
            WorkflowConversionSelection(
                workflow_id=uuid4(), source_fingerprint="c" * 64
            )
        ],
    )

    with pytest.raises(PlatformJobFailure, match="Platform administrator") as failure:
        await conversion.run_apply(_context(), payload)

    assert failure.value.code == "authorization_changed"


@pytest.mark.asyncio
async def test_cutover_rejects_a_report_that_became_stale_after_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    analysis_id = uuid4()
    analysis = SimpleNamespace(
        id=analysis_id,
        job_type=conversion.ANALYZE_JOB_TYPE,
        payload={"platform_cutover": True},
        status="succeeded",
    )
    workflow = _workflow()
    workflow.id = workflow_id
    report = SimpleNamespace(
        workflow_id=workflow_id, source_fingerprint="old" * 22, report={}
    )
    db = _Database(
        rows=[[workflow_id], [workflow_id], [workflow], [report]], job=analysis
    )
    _patch_db_context(monkeypatch, db)
    monkeypatch.setattr(conversion, "_fenced_job", AsyncMock())
    monkeypatch.setattr(
        conversion, "load_human_caller", AsyncMock(return_value=_admin())
    )
    monkeypatch.setattr(
        conversion,
        "_report_for_workflow",
        AsyncMock(return_value={"source_fingerprint": "new" * 22}),
    )
    payload = WorkflowConversionCutoverPayload(
        analysis_job_id=analysis_id,
        acknowledged_remaining_exceptions=True,
    )

    with pytest.raises(PlatformJobFailure, match="changed") as failure:
        await conversion.run_cutover(_context(), payload)

    assert failure.value.code == "stale_report"
    assert report.status == "stale"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "acknowledged,inventory_changed", [(False, False), (True, False), (True, True)]
)
async def test_cutover_accepts_only_persisted_review_of_unchanged_unknowns(
    monkeypatch, acknowledged, inventory_changed
):
    workflow_id = uuid4()
    analysis = SimpleNamespace(
        id=uuid4(),
        job_type=conversion.ANALYZE_JOB_TYPE,
        status="succeeded",
        payload={"platform_cutover": True},
    )
    workflow = _workflow()
    workflow.id = workflow_id
    report = SimpleNamespace(
        workflow_id=workflow_id,
        source_fingerprint="a" * 64,
        review={"acknowledged_unknowns": acknowledged},
    )
    db = _Database(
        rows=[
            [workflow_id],
            [workflow_id],
            [workflow],
            [report],
            None,
            [
                conversion._workflow_review_state(workflow),
                *([(uuid4(),)] if inventory_changed else []),
            ],
            None,
        ],
        job=analysis,
    )
    _patch_db_context(monkeypatch, db)
    monkeypatch.setattr(
        conversion, "load_human_caller", AsyncMock(return_value=_admin())
    )
    monkeypatch.setattr(
        conversion,
        "_report_for_workflow",
        AsyncMock(
            return_value={
                "source_fingerprint": "a" * 64,
                "unknowns": ["Dynamic call needs review"],
            }
        ),
    )
    monkeypatch.setattr(
        conversion,
        "effective_workflow_permission_mode",
        AsyncMock(return_value=WorkflowPermissionMode.RESTRICTED),
    )
    fenced = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(conversion, "_fenced_job", fenced)
    monkeypatch.setattr(conversion, "emit_audit", audit)
    context = _context()
    payload = WorkflowConversionCutoverPayload(
        analysis_job_id=analysis.id,
        acknowledged_remaining_exceptions=True,
    )

    if not acknowledged:
        with pytest.raises(PlatformJobFailure) as failure:
            await conversion.run_cutover(context, payload)
        assert failure.value.code == "cutover_unresolved"
        db.commit.assert_not_awaited()
    elif inventory_changed:
        with pytest.raises(PlatformJobFailure) as failure:
            await conversion.run_cutover(context, payload)
        assert failure.value.code == "cutover_inventory_changed"
        db.commit.assert_not_awaited()
    else:
        assert await conversion.run_cutover(context, payload) == {
            "default_mode": "restricted"
        }
        assert db.added[0].value_json == {"mode": "restricted"}
        fenced.assert_awaited_once_with(db, context)
        audit.assert_awaited_once()
        db.commit.assert_awaited_once()
