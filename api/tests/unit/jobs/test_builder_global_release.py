"""Reviewed Global release PlatformJob behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.builder_global_release import (
    BuilderGlobalReleaseApplyPayload,
    BuilderGlobalReleaseRollbackPayload,
    run_builder_global_release_apply,
    run_builder_global_release_rollback,
)
from src.services.builder.global_workspace import (
    GlobalWorkspaceApplyResult,
    GlobalWorkspaceError,
)


class _JobContext:
    def __init__(
        self,
        *,
        fail_report_phase: str | None = None,
        fail_log: bool = False,
    ) -> None:
        self.job_id = uuid4()
        self.lease_token = uuid4()
        self.organization_id = None
        self.requested_by_user_id = str(uuid4())
        self.requested_by_email = "builder@example.test"
        self.requested_by_name = "Builder"
        self.fail_report_phase = fail_report_phase
        self.fail_log = fail_log

    async def report(self, phase: str, *args, **kwargs) -> None:
        if phase == self.fail_report_phase:
            raise RuntimeError(f"report failed: {phase}")
        return None

    async def log(self, *args, **kwargs) -> None:
        if self.fail_log:
            raise RuntimeError("diagnostic log failed")
        return None


def _context(
    *,
    fail_report_phase: str | None = None,
    fail_log: bool = False,
) -> _JobContext:
    return _JobContext(fail_report_phase=fail_report_phase, fail_log=fail_log)


class _DbContext:
    def __init__(self, db: object) -> None:
        self.db = db

    async def __aenter__(self) -> object:
        return self.db

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _ScalarDb:
    def __init__(self, value: object) -> None:
        self.value = value

    async def scalar(self, _statement) -> object:
        return self.value


@pytest.mark.asyncio
async def test_combined_rollback_preflights_source_before_rolling_back_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def preflight(*args, **kwargs) -> None:
        calls.append("source-preflight")

    async def rollback_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-rollback")
        return {"changes": [{"change_id": "change-1"}]}

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-rollback")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(object()),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.preflight_global_workspace_rollback",
        preflight,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_operations_for_release",
        rollback_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    await run_builder_global_release_rollback(
        _context(),
        BuilderGlobalReleaseRollbackPayload(
            solution_id=uuid4(),
            source_apply_id=source_apply_id,
            approved_operation_changes={uuid4(): "fingerprint"},
        ),
    )

    assert calls == [
        "authorize",
        "source-preflight",
        "operations-rollback",
        "source-rollback",
    ]


@pytest.mark.asyncio
async def test_combined_rollback_reports_partial_when_source_fails_after_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def preflight(*args, **kwargs) -> None:
        calls.append("source-preflight")

    async def rollback_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-rollback")
        return {"changes": [{"change_id": "change-1"}]}

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-rollback")
        raise GlobalWorkspaceError("source restore failed")

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(object()),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.preflight_global_workspace_rollback",
        preflight,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_operations_for_release",
        rollback_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(PlatformJobFailure) as raised:
        await run_builder_global_release_rollback(
            _context(),
            BuilderGlobalReleaseRollbackPayload(
                solution_id=uuid4(),
                source_apply_id=source_apply_id,
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert raised.value.code == "global_release_partial_rollback_uncompensated"
    assert calls == [
        "authorize",
        "source-preflight",
        "operations-rollback",
        "source-rollback",
    ]


@pytest.mark.asyncio
async def test_combined_rollback_reports_partial_when_restore_progress_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def preflight(*args, **kwargs) -> None:
        calls.append("source-preflight")

    async def rollback_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-rollback")
        return {"changes": [{"change_id": "change-1"}]}

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-rollback")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=[],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(object()),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.preflight_global_workspace_rollback",
        preflight,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_operations_for_release",
        rollback_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(PlatformJobFailure) as raised:
        await run_builder_global_release_rollback(
            _context(fail_report_phase="Restoring reviewed Global source revision"),
            BuilderGlobalReleaseRollbackPayload(
                solution_id=uuid4(),
                source_apply_id=source_apply_id,
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert raised.value.code == "global_release_partial_rollback_uncompensated"
    assert calls == ["authorize", "source-preflight", "operations-rollback"]


@pytest.mark.asyncio
async def test_combined_apply_compensates_source_if_operation_apply_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def apply_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-apply")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=False,
        )

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        raise PlatformJobFailure(
            "global_operation_apply_failed",
            "operation failed",
            retryable=False,
        )

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(_ScalarDb(source_apply_id)),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_workspace",
        apply_source,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(PlatformJobFailure, match="operation failed"):
        await run_builder_global_release_apply(
            _context(),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                from_revision_id=uuid4(),
                to_revision_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert calls == [
        "authorize",
        "source-apply",
        "operations-apply",
        "source-compensation",
    ]


@pytest.mark.asyncio
async def test_combined_apply_compensation_does_not_depend_on_diagnostic_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def apply_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-apply")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=False,
        )

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        raise PlatformJobFailure(
            "global_operation_apply_failed",
            "operation failed",
            retryable=False,
        )

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=[],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(_ScalarDb(source_apply_id)),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_workspace",
        apply_source,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(PlatformJobFailure, match="operation failed"):
        await run_builder_global_release_apply(
            _context(fail_log=True),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                from_revision_id=uuid4(),
                to_revision_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert calls == [
        "authorize",
        "source-apply",
        "operations-apply",
        "source-compensation",
    ]


@pytest.mark.asyncio
async def test_combined_apply_compensates_source_if_operation_apply_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def apply_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-apply")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=False,
        )

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        raise RuntimeError("bridge exploded")

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(_ScalarDb(source_apply_id)),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_workspace",
        apply_source,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(RuntimeError, match="bridge exploded"):
        await run_builder_global_release_apply(
            _context(),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                from_revision_id=uuid4(),
                to_revision_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert calls == [
        "authorize",
        "source-apply",
        "operations-apply",
        "source-compensation",
    ]


@pytest.mark.asyncio
async def test_combined_apply_reports_uncompensated_when_source_apply_row_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def apply_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-apply")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=False,
        )

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        raise RuntimeError("operation failed")

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=[],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(_ScalarDb(None)),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_workspace",
        apply_source,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(
        PlatformJobFailure,
        match="no durable source apply row was found",
    ) as raised:
        await run_builder_global_release_apply(
            _context(),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                from_revision_id=uuid4(),
                to_revision_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert raised.value.code == "global_release_partial_apply_uncompensated"
    assert calls == ["authorize", "source-apply", "operations-apply"]


@pytest.mark.asyncio
async def test_combined_apply_compensates_if_operation_phase_report_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def apply_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-apply")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=False,
        )

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        return {"changes": []}

    async def finalize_release(*args, **kwargs) -> None:
        calls.append("release-finalize")

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(_ScalarDb(source_apply_id)),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_workspace",
        apply_source,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.finalize_global_workspace_release_revision",
        finalize_release,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(RuntimeError, match="report failed"):
        await run_builder_global_release_apply(
            _context(fail_report_phase="Applying reviewed Global operation changes"),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                from_revision_id=uuid4(),
                to_revision_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert calls == ["authorize", "source-apply", "source-compensation"]


@pytest.mark.asyncio
async def test_combined_apply_does_not_compensate_after_final_success_report_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> None:
        calls.append("authorize")

    async def apply_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-apply")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=False,
        )

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        return {"changes": [{"change_id": "change-1"}]}

    async def finalize_release(*args, **kwargs) -> None:
        calls.append("release-finalize")

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(_ScalarDb(source_apply_id)),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_workspace",
        apply_source,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.finalize_global_workspace_release_revision",
        finalize_release,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(RuntimeError, match="Global release applied"):
        await run_builder_global_release_apply(
            _context(fail_report_phase="Global release applied"),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                from_revision_id=uuid4(),
                to_revision_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert calls == ["authorize", "source-apply", "operations-apply", "release-finalize"]


@pytest.mark.asyncio
async def test_operation_only_apply_compensates_operations_if_finalize_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def authorize(*args, **kwargs) -> object:
        calls.append("authorize")
        return object()

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        return {"changes": [{"change_id": "change-1"}]}

    async def finalize_release(*args, **kwargs) -> None:
        calls.append("release-finalize")
        raise RuntimeError("snapshot failed")

    async def compensate_operations(*args, **kwargs) -> None:
        calls.append("operations-compensation")

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=[],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(object()),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.finalize_global_workspace_release_revision",
        finalize_release,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release._compensate_operations_after_finalize_failure",
        compensate_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(PlatformJobFailure, match="final source snapshot failed"):
        await run_builder_global_release_apply(
            _context(),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert calls == [
        "authorize",
        "operations-apply",
        "release-finalize",
        "operations-compensation",
    ]


@pytest.mark.asyncio
async def test_combined_apply_compensates_operations_then_source_if_finalize_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    source_apply_id = uuid4()

    async def authorize(*args, **kwargs) -> object:
        calls.append("authorize")
        return object()

    async def apply_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-apply")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=False,
        )

    async def apply_operations(*args, **kwargs) -> dict[str, object]:
        calls.append("operations-apply")
        return {"changes": [{"change_id": "change-1"}]}

    async def finalize_release(*args, **kwargs) -> None:
        calls.append("release-finalize")
        raise RuntimeError("snapshot failed")

    async def compensate_operations(*args, **kwargs) -> None:
        calls.append("operations-compensation")

    async def rollback_source(*args, **kwargs) -> GlobalWorkspaceApplyResult:
        calls.append("source-compensation")
        return GlobalWorkspaceApplyResult(
            revision_id=uuid4(),
            changed_paths=["workflows/example.py"],
            applied_at=datetime.now(timezone.utc),
            rolled_back=True,
        )

    monkeypatch.setattr("src.jobs.platform.builder_global_release._authorize_release", authorize)
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.get_db_context",
        lambda: _DbContext(_ScalarDb(source_apply_id)),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_workspace",
        apply_source,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.apply_global_operations_for_release",
        apply_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.global_operations_mcp_context",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.finalize_global_workspace_release_revision",
        finalize_release,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release._compensate_operations_after_finalize_failure",
        compensate_operations,
    )
    monkeypatch.setattr(
        "src.jobs.platform.builder_global_release.rollback_global_workspace",
        rollback_source,
    )

    with pytest.raises(PlatformJobFailure, match="final source snapshot failed"):
        await run_builder_global_release_apply(
            _context(),
            BuilderGlobalReleaseApplyPayload(
                solution_id=uuid4(),
                from_revision_id=uuid4(),
                to_revision_id=uuid4(),
                approved_operation_changes={uuid4(): "fingerprint"},
            ),
        )

    assert calls == [
        "authorize",
        "source-apply",
        "operations-apply",
        "release-finalize",
        "operations-compensation",
        "source-compensation",
    ]
