"""Incrementally normalize entity logos created before thumbnail support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from shared.logo_processing import LogoProcessingError, process_logo
from src.core.database import get_session_factory
from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution

logger = logging.getLogger(__name__)

LOGO_BACKFILL_BATCH_SIZE = 25
_FAILED_VERSION = "failed"


async def _backfill_model(
    db: AsyncSession,
    model: Any,
    limit: int,
) -> tuple[int, int]:
    if limit <= 0:
        return 0, 0

    rows = (
        (
            await db.execute(
                select(model)
                .options(
                    load_only(
                        model.id,
                        model.logo_data,
                        model.logo_content_type,
                        model.logo_thumbnail_version,
                    )
                )
                .where(
                    model.logo_data.is_not(None),
                    model.logo_thumbnail_version.is_(None),
                )
                .order_by(model.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    succeeded = 0
    failed = 0
    for row in rows:
        try:
            processed = await asyncio.to_thread(
                process_logo,
                row.logo_data,
                row.logo_content_type or "",
            )
        except LogoProcessingError as exc:
            row.logo_thumbnail_version = _FAILED_VERSION
            failed += 1
            logger.warning(
                "Legacy logo thumbnail generation failed",
                extra={
                    "entity_type": model.__tablename__,
                    "entity_id": str(row.id),
                    "reason": str(exc),
                },
            )
            continue

        row.logo_data = processed.original_data
        row.logo_content_type = processed.original_content_type
        row.logo_thumbnail_data = processed.thumbnail_data
        row.logo_thumbnail_content_type = processed.thumbnail_content_type
        row.logo_thumbnail_version = processed.thumbnail_version
        succeeded += 1

    if rows:
        await db.commit()
    return succeeded, failed


async def backfill_logo_thumbnails() -> dict[str, int]:
    """Process one bounded batch; repeated scheduler runs drain legacy rows."""
    session_factory = get_session_factory()
    succeeded = 0
    failed = 0
    remaining = LOGO_BACKFILL_BATCH_SIZE

    async with session_factory() as db:
        for model in (Application, Agent, Solution):
            model_succeeded, model_failed = await _backfill_model(db, model, remaining)
            succeeded += model_succeeded
            failed += model_failed
            remaining -= model_succeeded + model_failed
            if remaining <= 0:
                break

    if succeeded or failed:
        logger.info(
            "Legacy logo thumbnail batch completed",
            extra={"succeeded": succeeded, "failed": failed},
        )
    return {"succeeded": succeeded, "failed": failed}
