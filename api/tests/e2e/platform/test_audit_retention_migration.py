"""Existing-install policy written by 20261005_audit_retention.

The migration keeps history on upgrade: an install that already holds audit
events gets an explicit policy that archives but never deletes archives. The
fresh-install default is covered by the settings unit tests and by the test
stack itself, which migrates an empty database.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.audit_retention import AuditRetentionSettings
from src.models.orm import AuditLog, SystemConfig
from src.services.audit_retention.settings import AuditRetentionSettingsService

pytestmark = pytest.mark.e2e

_MIGRATION = (
    Path(__file__).resolve().parents[3] / "alembic" / "versions" / "20261005_audit_retention.py"
)


def _existing_install_policy_sql() -> str:
    spec = importlib.util.spec_from_file_location("audit_retention_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EXISTING_INSTALL_POLICY_SQL


async def _policy_row_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(SystemConfig)
        .where(
            SystemConfig.category == "audit_retention",
            SystemConfig.key == "policy",
            SystemConfig.organization_id.is_(None),
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_existing_install_keeps_archives_forever_and_is_idempotent(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(text("DELETE FROM system_configs WHERE category = 'audit_retention'"))
    db_session.add(AuditLog(action="test.retention.migration"))
    await db_session.flush()
    sql = _existing_install_policy_sql()

    await db_session.execute(text(sql))
    assert await AuditRetentionSettingsService(db_session).get_settings() == AuditRetentionSettings(
        hot_days=90, archive_days=None
    )

    await db_session.execute(text(sql))
    assert await _policy_row_count(db_session) == 1
