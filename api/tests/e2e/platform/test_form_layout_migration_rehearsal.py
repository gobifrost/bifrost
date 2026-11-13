"""Upgrade populated pre-layout forms without rewriting their content or identity."""

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e


def test_layout_upgrade_preserves_existing_forms_and_fields():
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    database_url = _direct_database_url(database_name)
    form_id = uuid4()

    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(sa.text(
            "INSERT INTO forms (id, name, workflow_id, access_level, is_active, created_by, "
            "allowed_query_params, default_launch_params) "
            "VALUES (:id, 'Existing Intake', 'legacy-workflow', 'authenticated', TRUE, "
            "'existing-owner', '[\"summary\"]'::jsonb, '{\"region\": \"west\"}'::jsonb)"
        ), {"id": form_id})
        for position, name in enumerate(("summary", "details")):
            await connection.execute(sa.text(
                "INSERT INTO form_fields (id, form_id, name, label, type, required, position, "
                "help_text, default_value, visibility_expression) "
                "VALUES (:id, :form_id, :name, :name, 'text', TRUE, :position, "
                "'Existing help', '\"Existing default\"'::jsonb, 'true')"
            ), {"id": uuid4(), "form_id": form_id, "name": name, "position": position})

    async def snapshot(connection: AsyncConnection) -> tuple[dict, list]:
        form = (await connection.execute(sa.text(
            "SELECT to_jsonb(f) FROM forms f WHERE id = :id"
        ), {"id": form_id})).scalar_one()
        fields = list((await connection.execute(sa.text(
            "SELECT to_jsonb(f) FROM form_fields f WHERE form_id = :id ORDER BY position"
        ), {"id": form_id})).scalars())
        return form, fields

    asyncio.run(_create_database(database_name))
    try:
        _upgrade(database_url, "20261111_provider_identity")
        asyncio.run(_run_in_database(database_url, seed))
        before_form, before_fields = asyncio.run(_run_in_database(database_url, snapshot))
        assert "layout" not in before_form

        _upgrade(database_url, "20261110_form_layout")

        after_form, after_fields = asyncio.run(_run_in_database(database_url, snapshot))
        layout = after_form.pop("layout")
        assert layout is None
        assert after_form == before_form
        assert after_fields == before_fields
    finally:
        asyncio.run(_drop_database(database_name))
