"""Rehearsals for the token program's migrations on disposable databases.

``alembic/env.py`` records the revision an upgrade run starts from; a run that
starts on an empty schema is a fresh install. These tests prove the PR1a
migration (``20261030_authz_versions``) on both paths: an empty database
upgraded to head, and an existing database upgraded from the revision before
it. They also rehearse its bounded lock waits behind a long transaction, and
the cost of a large cascade through the epoch triggers. The PR1b migration
(``20261031_authz_claims_precondition``) refuses an existing database until an
API of the PR1a release has recorded its start. PR1c adds its precondition
rehearsal here.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from src.services.authorization.authz import AUTHZ_FACTS
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    TOKEN_PR1A_REVISION,
    _assert_safe_database_name,
    _create_database,
    _current_revision,
    _direct_database_url,
    _drop_database,
    _record_token_pr1a_start,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PRE_PR1A_REVISION = "20261025_remote_worker_ownership"
PR1A_REVISION = TOKEN_PR1A_REVISION
PR1B_PRECONDITION = "Upgrade to the release that added the authority version"


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
            _upgrade(database_url, revision, roll_out_token_releases=False)
        return asyncio.run(_authz_state(database_url))
    finally:
        asyncio.run(_drop_database(database_name))


def test_an_empty_database_upgrades_to_head_as_a_fresh_install() -> None:
    state = _rehearse("head")

    assert state["epoch_kinds"] == ["global"]
    # A fresh install records both protocols: no older pod, token or run exists.
    assert state["protocols"] == [1, 2]
    assert state["triggers"] == _expected_trigger_count()
    assert len(state["columns"]) == 3
    assert state["bumps"] is True


def test_an_existing_database_upgrades_without_claiming_a_protocol_start() -> None:
    state = _rehearse(PRE_PR1A_REVISION, PR1A_REVISION)

    assert state["epoch_kinds"] == ["global"]
    # Only a starting API records its protocol on an upgraded database.
    assert state["protocols"] == []
    assert state["triggers"] == _expected_trigger_count()
    assert len(state["columns"]) == 3
    assert state["bumps"] is True


# --- PR1b precondition ------------------------------------------------------------


def test_a_skip_upgrade_across_both_token_releases_is_refused_in_one_transaction() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PRE_PR1A_REVISION, roll_out_token_releases=False)

        with pytest.raises(Exception, match=PR1B_PRECONDITION):
            _upgrade(database_url, "head", roll_out_token_releases=False)

        # The whole run rolled back, PR1a's migration included.
        assert _current_revision(database_url) == PRE_PR1A_REVISION
    finally:
        asyncio.run(_drop_database(database_name))


def test_the_claims_release_waits_for_a_started_pr1a_api() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PRE_PR1A_REVISION, roll_out_token_releases=False)
        _upgrade(database_url, PR1A_REVISION, roll_out_token_releases=False)

        # PR1a migrated, but no API of that release has started yet.
        with pytest.raises(Exception, match=PR1B_PRECONDITION):
            _upgrade(database_url, "head", roll_out_token_releases=False)
        assert _current_revision(database_url) == PR1A_REVISION

        asyncio.run(_run_in_database(database_url, _record_token_pr1a_start))
        _upgrade(database_url, "head", roll_out_token_releases=False)

        assert _current_revision(database_url) not in (PRE_PR1A_REVISION, PR1A_REVISION)
        # Protocol 2 is recorded when the first new API starts, not here.
        assert asyncio.run(_authz_state(database_url))["protocols"] == [1]
    finally:
        asyncio.run(_drop_database(database_name))


# --- Bounded lock waits ---------------------------------------------------------


def _hold(database_url: str, statement: str, ready: threading.Event, release: threading.Event) -> None:
    """Keep a transaction open after ``statement`` until ``release`` is set."""

    async def hold() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                await connection.execute(sa.text(statement))
                ready.set()
                while not release.is_set():
                    await asyncio.sleep(0.05)
                await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(hold())


def _probe(database_url: str, started: threading.Event, stop: threading.Event, waits: list[float]) -> None:
    """Product traffic: read ``executions`` in a loop, recording each wait."""

    async def probe() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                started.set()
                while not stop.is_set():
                    began = time.monotonic()
                    await connection.execute(sa.text("SELECT 1 FROM executions LIMIT 1"))
                    await connection.commit()
                    waits.append(time.monotonic() - began)
                    await asyncio.sleep(0.02)
        finally:
            await engine.dispose()

    asyncio.run(probe())


# Two LOCK TABLE statements capped at 1 s each: the run fails within about
# 2 s of waiting, plus Alembic start-up.
_FAIL_FAST_SECONDS = 10
# Worst-case queueing of traffic: about 2 s of lock waits plus DDL; the probe
# allows scheduling margin on top.
_TRAFFIC_WAIT_SECONDS = 2.5


@pytest.mark.parametrize(
    "statement",
    [
        # A long reader: conflicts with ACCESS EXCLUSIVE for the column adds.
        "SELECT count(*) FROM executions",
        # A long writer: conflicts with SHARE ROW EXCLUSIVE for the triggers,
        # after the executions lock is already held.
        "UPDATE users SET name = name WHERE false",
    ],
    ids=["long reader on executions", "open writer on users"],
)
def test_a_long_transaction_makes_the_migration_fail_fast_and_a_rerun_succeeds(statement: str) -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ready, release = threading.Event(), threading.Event()
    probing, stop_probe = threading.Event(), threading.Event()
    waits: list[float] = []
    holder = threading.Thread(target=_hold, args=(database_url, statement, ready, release), daemon=True)
    prober = threading.Thread(target=_probe, args=(database_url, probing, stop_probe, waits), daemon=True)
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PRE_PR1A_REVISION)
        holder.start()
        assert ready.wait(30)
        prober.start()
        assert probing.wait(30)

        started = time.monotonic()
        with pytest.raises(Exception, match="timeout"):
            _upgrade(database_url, PR1A_REVISION)
        elapsed = time.monotonic() - started
        stop_probe.set()
        prober.join(30)

        assert elapsed < _FAIL_FAST_SECONDS
        # Traffic on executions queued behind the migration only briefly.
        assert waits and max(waits) < _TRAFFIC_WAIT_SECONDS, max(waits, default=None)
        # One transaction: nothing of the run survives the timeout.
        assert _current_revision(database_url) == PRE_PR1A_REVISION

        release.set()
        holder.join(30)
        _upgrade(database_url, PR1A_REVISION)
        assert _current_revision(database_url) == PR1A_REVISION
        assert asyncio.run(_authz_state(database_url))["epoch_kinds"] == ["global"]
    finally:
        release.set()
        stop_probe.set()
        for thread in (holder, prober):
            if thread.is_alive():
                thread.join(30)
        asyncio.run(_drop_database(database_name))


# --- Cascade cost ---------------------------------------------------------------

_CASCADE_ROWS = 200_000
# Stated bound for deleting one user referenced by 200,000 executions: three
# SET NULL cascades (run user, actor, starter) queue 600,000 deferred trigger
# events, all of which find the bump already done.
_CASCADE_SECONDS = 60
_TIMING_FILE = Path("/tmp/bifrost/authz-cascade-timing.txt")

_SEED_USER = """
INSERT INTO users (id, email, is_active, is_superuser, is_verified, is_registered, is_system,
                   mfa_enabled, base_role_id)
VALUES (CAST(:user AS uuid), 'cascade@example.test', true, true, true, true, false, false,
        '00000000-0000-0000-0000-000000000006')
"""
_SEED_EXECUTIONS = """
INSERT INTO executions (id, workflow_name, status, parameters, time_saved, value, executed_by,
                        executed_by_name, run_user_id, started_by_user_id, root_execution_id)
SELECT gen_random_uuid(), 'cascade', 'Success', '{}'::jsonb, 0, 0, CAST(:user AS uuid), 'Cascade',
       CAST(:user AS uuid), CAST(:user AS uuid), gen_random_uuid()
  FROM generate_series(1, :rows)
"""
_EPOCH_UPDATES = sa.text(
    "SELECT COALESCE((SELECT n_tup_upd FROM pg_stat_xact_user_tables WHERE relname = 'authz_versions'), 0)"
)


def test_deleting_a_user_behind_two_hundred_thousand_executions_bumps_once_within_the_bound(
    record_property: Any,
) -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    user = str(uuid4())

    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(sa.text(_SEED_USER), {"user": user})
        await connection.execute(sa.text(_SEED_EXECUTIONS), {"user": user, "rows": _CASCADE_ROWS})

    async def delete_user() -> tuple[float, int, int, int]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                before = (await connection.execute(sa.text("SELECT version FROM authz_versions"))).scalar_one()
                await connection.commit()
                started = time.monotonic()
                transaction = await connection.begin()
                await connection.execute(sa.text("DELETE FROM users WHERE id = CAST(:user AS uuid)"), {"user": user})
                # Fire the queued deferred events now, to count their bumps.
                await connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
                bumps = (await connection.execute(_EPOCH_UPDATES)).scalar_one()
                await transaction.commit()
                elapsed = time.monotonic() - started
                after = (await connection.execute(sa.text("SELECT version FROM authz_versions"))).scalar_one()
                orphaned = (
                    await connection.execute(sa.text("SELECT count(*) FROM executions WHERE executed_by IS NULL"))
                ).scalar_one()
                await connection.commit()
                return elapsed, bumps, int(before != after), orphaned
        finally:
            await engine.dispose()

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, "head")
        asyncio.run(_run_in_database(database_url, seed))

        elapsed, bumps, changed, orphaned = asyncio.run(delete_user())
    finally:
        asyncio.run(_drop_database(database_name))

    record_property("cascade_seconds", f"{elapsed:.2f}")
    message = f"deleting a user behind {_CASCADE_ROWS} executions took {elapsed:.2f} s (bound {_CASCADE_SECONDS} s)"
    print(message)
    if _TIMING_FILE.parent.is_dir():
        _TIMING_FILE.write_text(message + "\n")
    assert orphaned == _CASCADE_ROWS
    assert (bumps, changed) == (1, 1)
    assert elapsed < _CASCADE_SECONDS, message
