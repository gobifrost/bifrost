"""Upgrade a real pre-file-index database without losing its only source copy."""

import asyncio
import hashlib
from datetime import datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e


async def test_old_workspace_upgrade_preserves_source_and_utc_field_timestamps():
    # Reuse the validated disposable-database lifecycle; no production DB access.
    name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    url = _direct_database_url(name)
    workflow_id, indexed_workflow_id, form_id, field_id = uuid4(), uuid4(), uuid4(), uuid4()
    source = "def historical():\n    return 'preserve this source'\n"
    path = f"workflows/historical_{workflow_id.hex}.py"
    indexed_path = f"workflows/indexed_{indexed_workflow_id.hex}.py"
    stamp = datetime(2020, 1, 2, 3, 4, 5)
    await _create_database(name)
    try:
        await asyncio.to_thread(_upgrade, url, "25549bb29ea6")

        async def seed(connection):
            await connection.execute(sa.text(
                "INSERT INTO workflows (id,name,function_name,path,code,code_hash) "
                "VALUES (:id,'Historical source','historical',:path,:source,:hash)"
            ), {"id": workflow_id, "path": path, "source": source,
                "hash": hashlib.sha256(source.encode()).hexdigest()})
            await connection.execute(sa.text(
                "INSERT INTO workflows (id,name,function_name,path,code) "
                "VALUES (:id,'Older source','older',:path,'older database copy')"
            ), {"id": indexed_workflow_id, "path": indexed_path})
            await connection.execute(sa.text(
                "INSERT INTO forms (id,name,created_by) VALUES (:id,'Historical form','migration-rehearsal')"
            ), {"id": form_id})
            await connection.execute(sa.text(
                "INSERT INTO form_fields (id,form_id,name,type,position,created_at,updated_at) "
                "VALUES (:id,:form_id,'field','text',0,CAST(:stamp AS timestamp),CAST(:stamp AS timestamp))"
            ), {"id": field_id, "form_id": form_id, "stamp": stamp})

        await _run_in_database(url, seed)
        await asyncio.to_thread(_upgrade, url, "20260210_file_index")

        async def seed_newer_index(connection):
            await connection.execute(sa.text(
                "INSERT INTO file_index (path,content) VALUES (:path,'newer indexed copy')"
            ), {"path": indexed_path})

        await _run_in_database(url, seed_newer_index)
        await asyncio.to_thread(_upgrade, url, "head")

        async def verify(connection):
            content = await connection.scalar(sa.text("SELECT content FROM file_index WHERE path=:path"), {"path": path})
            types = dict((await connection.execute(sa.text(
                "SELECT column_name,data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='form_fields' "
                "AND column_name IN ('created_at','updated_at')"
            ))).all())
            problems = []
            if content != source:
                problems.append("legacy workflow source was not retained in file_index")
            if set(types.values()) != {"timestamp with time zone"}:
                problems.append(f"form field timestamp types: {types}")
            assert not problems, "; ".join(problems)
            assert await connection.scalar(sa.text(
                "SELECT content FROM file_index WHERE path=:path"
            ), {"path": indexed_path}) == "newer indexed copy"
            values = (await connection.execute(sa.text(
                "SELECT created_at,updated_at FROM form_fields WHERE id=:id"
            ), {"id": field_id})).one()
            assert all(v.isoformat() == "2020-01-02T03:04:05+00:00" for v in values)

        await _run_in_database(url, verify)
    finally:
        await _drop_database(name)
