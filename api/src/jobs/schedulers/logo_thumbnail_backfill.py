"""Incrementally normalize entity logos created before thumbnail support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.logo_processing import LogoProcessingError, process_logo
from src.core.database import get_session_factory
from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.models.orm.integrations import Integration
from src.models.orm.solutions import Solution

logger = logging.getLogger(__name__)

LOGO_BACKFILL_BATCH_SIZE = 25
_FAILED_VERSION = "failed"


async def _pending_ids(db: AsyncSession, model: Any, limit: int) -> list[Any]:
    result = await db.execute(
        select(model.id)
        .where(
            model.logo_data.is_not(None),
            model.logo_thumbnail_version.is_(None),
        )
        .order_by(model.id)
        .limit(limit)
    )
    return list(result.scalars().all())


async def _backfill_row(db: AsyncSession, model: Any, row_id: Any) -> bool | None:
    """Process one row in its own transaction.

    Returns True on success, False when the logo was marked failed, and None
    when another worker holds or already handled the row.  Columns are written
    with a Core UPDATE (like Solution deploy) so the Solution-managed write
    guard, which watches ORM flushes, is neither triggered nor loosened.
    """
    row = (
        await db.execute(
            select(model.logo_data, model.logo_content_type)
            .where(
                model.id == row_id,
                model.logo_data.is_not(None),
                model.logo_thumbnail_version.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
    ).first()
    if row is None:
        await db.rollback()
        return None

    try:
        processed = await asyncio.to_thread(
            process_logo,
            row.logo_data,
            row.logo_content_type or "",
        )
    except LogoProcessingError as exc:
        await db.execute(
            update(model)
            .where(model.id == row_id)
            .values(logo_thumbnail_version=_FAILED_VERSION)
        )
        await db.commit()
        logger.warning(
            "Legacy logo thumbnail generation failed",
            extra={
                "entity_type": model.__tablename__,
                "entity_id": str(row_id),
                "reason": str(exc),
            },
        )
        return False

    await db.execute(
        update(model)
        .where(model.id == row_id)
        .values(
            logo_data=processed.original_data,
            logo_content_type=processed.original_content_type,
            logo_thumbnail_data=processed.thumbnail_data,
            logo_thumbnail_content_type=processed.thumbnail_content_type,
            logo_thumbnail_version=processed.thumbnail_version,
        )
    )
    await db.commit()
    return True


async def _backfill_model(
    db: AsyncSession,
    model: Any,
    limit: int,
) -> tuple[int, int]:
    if limit <= 0:
        return 0, 0

    succeeded = 0
    failed = 0
    for row_id in await _pending_ids(db, model, limit):
        try:
            outcome = await _backfill_row(db, model, row_id)
        except Exception:
            await db.rollback()
            failed += 1
            logger.exception(
                "Legacy logo backfill failed for row",
                extra={"entity_type": model.__tablename__, "entity_id": str(row_id)},
            )
            continue
        if outcome is True:
            succeeded += 1
        elif outcome is False:
            failed += 1
    return succeeded, failed


async def backfill_logo_thumbnails() -> dict[str, int]:
    """Process one bounded batch; repeated scheduler runs drain legacy rows."""
    session_factory = get_session_factory()
    succeeded = 0
    failed = 0
    remaining = LOGO_BACKFILL_BATCH_SIZE

    async with session_factory() as db:
        for model in (Application, Agent, Form, Integration, Solution):
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
