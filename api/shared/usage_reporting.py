"""Pseudonymous daily aggregate usage reporting for production installations."""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import SEMVER_PATTERN, UsageReport
from shared.version import get_version
from src.config import get_settings
from src.core.database import get_session_factory
from src.models.orm import AgentRun, SystemConfig
from src.scheduler.registry import ScheduledTaskOutcome
from src.services.run_retention.source import workflow_run_source

logger = logging.getLogger(__name__)

USAGE_REPORT_URL = "https://reports.gobifrost.com/usage"
USAGE_REPORTING_CATEGORY = "usage_reporting"
INSTALLATION_CONFIG_KEY = "installation"
LAST_SUCCESSFUL_PERIOD_CONFIG_KEY = "last_successful_period"
HTTP_TIMEOUT_SECONDS = 10.0

_SEMVER_RE = re.compile(SEMVER_PATTERN)


def _canonical_version() -> str | None:
    """Return the release version accepted by the reporting schema."""
    version = get_version()
    if version.startswith("v"):
        version = version[1:]
    if len(version) > 128 or not _SEMVER_RE.fullmatch(version):
        return None
    return version


def _debugpy_enabled() -> bool:
    """Treat the compose debugpy switch as a debug process."""
    return os.environ.get("ENABLE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def _config_statement(key: str):
    return select(SystemConfig).where(
        SystemConfig.category == USAGE_REPORTING_CATEGORY,
        SystemConfig.key == key,
        SystemConfig.organization_id.is_(None),
    )


async def _last_successful_period(db: AsyncSession) -> date | None:
    config = (await db.execute(_config_statement(LAST_SUCCESSFUL_PERIOD_CONFIG_KEY))).scalars().first()
    if not config or not config.value_json:
        return None
    raw_period = config.value_json.get("period")
    if not isinstance(raw_period, str):
        return None
    try:
        return date.fromisoformat(raw_period)
    except ValueError:
        return None


async def get_or_create_installation_id(db: AsyncSession) -> UUID:
    """Return the one persistent random installation identifier."""
    config = (await db.execute(_config_statement(INSTALLATION_CONFIG_KEY))).scalars().first()
    if config is not None:
        return _stored_installation_id(config)

    installation_id = uuid4()
    await db.execute(
        pg_insert(SystemConfig)
        .values(
            id=uuid4(),
            category=USAGE_REPORTING_CATEGORY,
            key=INSTALLATION_CONFIG_KEY,
            value_json={"id": str(installation_id)},
            organization_id=None,
        )
        .on_conflict_do_nothing(
            index_elements=[SystemConfig.category, SystemConfig.key],
            index_where=SystemConfig.organization_id.is_(None),
        )
    )
    await db.commit()

    config = (await db.execute(_config_statement(INSTALLATION_CONFIG_KEY))).scalars().one()
    return _stored_installation_id(config)


def _stored_installation_id(config: SystemConfig) -> UUID:
    try:
        installation_id = UUID(str(config.value_json["id"]))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Stored usage reporting installation ID is invalid") from exc
    if installation_id.version != 4:
        raise ValueError("Stored usage reporting installation ID is invalid")
    return installation_id


async def collect_usage_report(
    db: AsyncSession,
    *,
    now: datetime,
    installation_id: UUID,
    version: str,
) -> UsageReport:
    """Count started workflow and agent runs for yesterday's completed UTC day."""
    now_utc = now.astimezone(UTC)
    period = now_utc.date() - timedelta(days=1)
    start = datetime.combine(period, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)

    runs = workflow_run_source(start=start, end=end)
    workflow_runs = (
        await db.execute(
            select(func.coalesce(func.sum(runs.c.run_count), 0)).where(
                runs.c.last_started_at >= start,
                runs.c.last_started_at < end,
            )
        )
    ).scalar_one()
    agent_runs = (
        await db.execute(
            select(func.count(AgentRun.id)).where(
                func.coalesce(AgentRun.started_at, AgentRun.completed_at) >= start,
                func.coalesce(AgentRun.started_at, AgentRun.completed_at) < end,
            )
        )
    ).scalar_one()

    return UsageReport(
        installation_id=installation_id,
        version=version[1:] if version.startswith("v") else version,
        period=period,
        workflow_runs=int(workflow_runs),
        agent_runs=int(agent_runs),
    )


async def _record_successful_period(db: AsyncSession, period: date) -> None:
    await db.execute(
        pg_insert(SystemConfig)
        .values(
            id=uuid4(),
            category=USAGE_REPORTING_CATEGORY,
            key=LAST_SUCCESSFUL_PERIOD_CONFIG_KEY,
            value_json={"period": period.isoformat()},
            organization_id=None,
        )
        .on_conflict_do_update(
            index_elements=[SystemConfig.category, SystemConfig.key],
            index_where=SystemConfig.organization_id.is_(None),
            set_={"value_json": {"period": period.isoformat()}},
        )
    )
    await db.commit()


async def report_usage() -> ScheduledTaskOutcome:
    """Send one yesterday snapshot when production reporting is eligible."""
    settings = get_settings()
    if (
        settings.environment != "production"
        or settings.debug
        or _debugpy_enabled()
        or not settings.usage_reporting_enabled
    ):
        return ScheduledTaskOutcome(summary="Usage reporting skipped")

    version = _canonical_version()
    if version is None:
        return ScheduledTaskOutcome(summary="Usage reporting skipped")

    now = datetime.now(UTC)
    period = now.date() - timedelta(days=1)
    session_factory = get_session_factory()
    async with session_factory() as db:
        last_successful = await _last_successful_period(db)
        if last_successful is not None and last_successful >= period:
            return ScheduledTaskOutcome(summary="Usage report already delivered")
        installation_id = await get_or_create_installation_id(db)
        report = await collect_usage_report(
            db,
            now=now,
            installation_id=installation_id,
            version=version,
        )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.post(USAGE_REPORT_URL, json=report.model_dump(mode="json"))
    except httpx.HTTPError:
        logger.info("Usage report delivery failed")
        return ScheduledTaskOutcome(summary="Usage report delivery failed")

    if response.status_code != 204:
        logger.info("Usage report delivery failed")
        return ScheduledTaskOutcome(summary="Usage report delivery failed")

    async with session_factory() as db:
        await _record_successful_period(db, period)
    return ScheduledTaskOutcome(summary="Usage report delivered")
