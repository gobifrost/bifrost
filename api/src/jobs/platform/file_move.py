# api/src/jobs/platform/file_move.py
"""Durable server-side file moves (``files.move`` / ``files.move_many``).

Authorization happened at enqueue (``shared.sdk_files.sdk_enqueue_file_moves``).
This job only moves the authorized items, one at a time in request order,
through :class:`src.services.file_storage.move.FileMover`. Contract:
``docs/superpowers/specs/2026-10-09-files-move-design.md``.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.contracts.cli import FileMoveResult

FILE_MOVE_JOB_TYPE = "files.move"
MAX_FILE_MOVE_ITEMS = 500
# Consecutive storage-level item failures that mean storage itself is down.
SYSTEMIC_FAILURE_RUN = 3


class FileMoveWorkItem(BaseModel):
    index: int = Field(ge=0)
    source: str
    destination: str


class RejectedFileMove(BaseModel):
    index: int = Field(ge=0)
    result: FileMoveResult


class FileMovePayload(BaseModel):
    location: str
    scope: str
    solution_id: UUID | None = None
    # Metadata org: the install's org for Solution rows, scope_to_org_id otherwise.
    organization_id: UUID | None = None
    on_conflict: Literal["error", "rename"] = "error"
    requested_by_user_id: UUID
    total: int = Field(ge=1, le=MAX_FILE_MOVE_ITEMS)
    items: list[FileMoveWorkItem] = Field(default_factory=list)
    rejected: list[RejectedFileMove] = Field(default_factory=list)

    @model_validator(mode="after")
    def every_index_once(self) -> "FileMovePayload":
        indices = sorted([i.index for i in self.items] + [r.index for r in self.rejected])
        if indices != list(range(self.total)):
            raise ValueError("items and rejected must cover each request index exactly once")
        return self


def _object_store() -> Any:
    from src.config import get_settings
    from src.services.file_storage.s3_client import S3StorageClient

    return S3StorageClient(get_settings())


def _failed_entry(index: int, result: FileMoveResult) -> dict[str, Any]:
    return {"index": index, **result.model_dump()}


def _terminal(results: dict[int, FileMoveResult], moved: int, total: int) -> dict[str, Any]:
    return {
        "results": [results[index].model_dump() for index in range(total)],
        "moved": moved,
        "failed": total - moved,
    }


async def run_file_move(context: PlatformJobContext, raw_payload: BaseModel) -> dict[str, Any]:
    from src.services.file_storage.move import FileMover, MoveRequest, MoveTarget

    payload = FileMovePayload.model_validate(raw_payload)
    results: dict[int, FileMoveResult] = {r.index: r.result for r in payload.rejected}
    failed = [_failed_entry(r.index, r.result) for r in sorted(payload.rejected, key=lambda r: r.index)]
    moved = 0
    storage_failures = 0

    async def log_cleanup_failure(source_key: str, destination_key: str) -> None:
        await context.log(
            "warning",
            "file_move_cleanup_failed",
            f"A failed move of {source_key} left its copy at {destination_key}; "
            "remove it, because it can be readable under its new prefix.",
        )

    mover = FileMover(
        _object_store(),
        MoveTarget(
            location=payload.location,
            scope=payload.scope,
            solution_id=payload.solution_id,
            organization_id=payload.organization_id,
            requested_by=payload.requested_by_user_id,
        ),
        on_cleanup_failed=log_cleanup_failure,
    )
    work = sorted(payload.items, key=lambda item: item.index)
    for position, item in enumerate(work):
        checkpoint = {
            "moved": moved,
            "failed": list(failed),
            "in_flight": {"index": item.index, "source": item.source, "destination": item.destination},
        }
        try:
            await context.save_checkpoint(
                checkpoint,
                phase=f"Moving {position + 1} of {len(work)}",
                current=position,
                total=len(work),
            )
        except PlatformJobCancelled:
            raise
        except Exception as exc:
            raise PlatformJobFailure(
                "checkpoint_failed", "Could not record move progress.", result=checkpoint
            ) from exc

        outcome = await mover.move(MoveRequest(item.source, item.destination), payload.on_conflict)
        result = FileMoveResult(
            source=item.source,
            destination=item.destination,
            path=outcome.path,
            renamed=outcome.renamed,
            adopted=outcome.adopted,
            status_code=outcome.status_code,
            error=outcome.error,
        )
        results[item.index] = result
        if outcome.status_code == 200:
            moved += 1
            storage_failures = 0
            continue
        failed.append(_failed_entry(item.index, result))
        storage_failures = storage_failures + 1 if outcome.storage_error else 0
        if storage_failures >= SYSTEMIC_FAILURE_RUN:
            for later in work[position + 1:]:
                results[later.index] = FileMoveResult(
                    source=later.source,
                    destination=later.destination,
                    status_code=500,
                    error="not_attempted",
                )
            raise PlatformJobFailure(
                "storage_unavailable",
                "Object storage is unavailable; the remaining moves were not attempted.",
                result=_terminal(results, moved, payload.total),
            )
    return _terminal(results, moved, payload.total)


FILE_MOVE_DEFINITION = PlatformJobDefinition(
    job_type=FILE_MOVE_JOB_TYPE,
    payload_version=1,
    payload_model=FileMovePayload,
    handler=run_file_move,
    policy=PlatformJobPolicy(
        timeout_seconds=4 * 60 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,  # a half-done item is not safely repeatable
        allow_running_cancellation=False,  # cancel only while queued
        min_memory_headroom_mb=128,  # copies are server-side; no bytes in memory
    ),
)
