"""Rehearsals for the token program's migrations on disposable databases.

``alembic/env.py`` records the revision an upgrade run starts from; a run that
starts on an empty schema is a fresh install. These tests prove the PR1a
migration (``20261030_authz_versions``) on both paths: an empty database
upgraded to head, and an existing database upgraded from the revision before
it. PR1b and PR1c add their precondition rehearsals here.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from src.services.authorization.authz import AUTHZ_FACTS
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PRE_PR1A_REVISION = "20261025_remote_worker_ownership"


def _expected_trigger_count() -> int:
    return sum(1 + int(f.insert) + int(f.delete) + 1 for f in AUTHZ_FACTS)


async def _authz_state(database_url: str) -> dict[str, Any]:
    async def inspect(connection: AsyncConnection) -> dict[str, Any]:
        epochs = (await connection.execute(sa.text("SELECT subject_kind, version FROM authz_versions"))).all()
        protocols = (await connection.execute(sa.text("SELECT protocol FROM authz_protocol_state"))).scalars().all()
        triggers = (
            await connection.execute(
                sa.text("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'authz\\_epoch\\_%'")
            )
        ).scalar_one()
        columns = {
            (table, column)
            for table, column in (
                await connection.execute(
                    sa.text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE (table_name, column_name) IN (('executions', 'credential_version'), "
                        "('service_attempts', 'credential_version'), "
                        "('execution_workers', 'remote_credential_protocol'))"
                    )
                )
            ).all()
        }
        before = epochs[0][1] if epochs else None
        await connection.execute(sa.text("SELECT authz_epoch_bump_once()"))
        after = (await connection.execute(sa.text("SELECT version FROM authz_versions"))).scalar_one()
        return {
            "epoch_kinds": [kind for kind, _ in epochs],
            "protocols": sorted(protocols),
            "triggers": triggers,
            "columns": columns,
            "bumps": before is not None and after != before,
        }

    return await _run_in_database(database_url, inspect)


def _rehearse(*revisions: str) -> dict[str, Any]:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    try:
        asyncio.run(_create_database(database_name))
        for revision in revisions:
            _upgrade(database_url, revision)
        return asyncio.run(_authz_state(database_url))
    finally:
        asyncio.run(_drop_database(database_name))


def test_an_empty_database_upgrades_to_head_as_a_fresh_install() -> None:
    state = _rehearse("head")

    assert state["epoch_kinds"] == ["global"]
    # A fresh install records protocol 1: no older pod, token or run exists.
    assert state["protocols"] == [1]
    assert state["triggers"] == _expected_trigger_count()
    assert len(state["columns"]) == 3
    assert state["bumps"] is True


def test_an_existing_database_upgrades_without_claiming_a_protocol_start() -> None:
    state = _rehearse(PRE_PR1A_REVISION, "head")

    assert state["epoch_kinds"] == ["global"]
    # Only a starting API records its protocol on an upgraded database.
    assert state["protocols"] == []
    assert state["triggers"] == _expected_trigger_count()
    assert len(state["columns"]) == 3
    assert state["bumps"] is True
