# api/src/jobs/platform/file_move.py
"""Durable server-side file moves (``files.move`` / ``files.move_many``).

Authorization happened at enqueue (``shared.sdk_files.sdk_enqueue_file_moves``),
except for a numbered ``name (n)`` destination the job copies to, resumes, or
adopts, which is authorized here. This job moves the authorized items, one at a time in request order,
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
    PlatformJobFailure,
    define_platform_job,
)
from src.models.contracts.cli import FileMoveResult

FILE_MOVE_JOB_TYPE = "files.move"
MAX_FILE_MOVE_ITEMS = 500
# Bounds the terminal result, which is broadcast on notification channels.
MAX_FILE_MOVE_PATH_CHARS = 200_000
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
    # The policy principal enqueue authorized: a renamed destination is
    # authorized again for this user (current roles) with these authorities.
    policy_user_id: UUID
    policy_authorities: list[str] = Field(default_factory=list)
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


async def authorize_renamed_destination(payload: FileMovePayload, path: str) -> bool:
    """Write policy on a numbered destination under ``on_conflict="rename"``.

    Enqueue authorized the requested destination only. A ``stem (n)`` name can
    fall under a different policy, and an interrupted copy there may have been
    made for another user, so it is evaluated for the user
    enqueue authorized, with that user's current roles; a denial is audited
    like the enqueue-time ones.
    """
    from src.core.database import get_db_context
    from src.services.access_check_policies import load_policy_user_principal
    from src.services.audit import emit_file_policy_deny
    from src.services.file_policy_service import FilePolicyService

    async with get_db_context() as db:
        principal = await load_policy_user_principal(
            db, payload.policy_user_id, frozenset(payload.policy_authorities)
        )
        allowed = principal is not None and (
            await FilePolicyService(db).evaluate(
                "write",
                organization_id=payload.organization_id,
                location=payload.location,
                path=path,
                user=principal,
                solution_id=payload.solution_id,
            )
        ).allowed
        if not allowed:
            await emit_file_policy_deny(
                db,
                policy_action="write",
                location=payload.location,
                path=path,
                scope=payload.scope,
                solution_id=payload.solution_id,
            )
            await db.commit()
        return allowed


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

    async def authorize_rename(path: str) -> bool:
        return await authorize_renamed_destination(payload, path)

    mover = FileMover(
        _object_store(),
        MoveTarget(
            location=payload.location,
            scope=payload.scope,
            solution_id=payload.solution_id,
            organization_id=payload.organization_id,
            requested_by=payload.requested_by_user_id,
        ),
        authorize_rename=authorize_rename,
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


FILE_MOVE_DEFINITION = define_platform_job(
    FILE_MOVE_JOB_TYPE, FileMovePayload, run_file_move
)
