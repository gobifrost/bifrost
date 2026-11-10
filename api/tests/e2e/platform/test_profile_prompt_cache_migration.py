"""The additive profile observation keeps old connection data and rollback usable."""
import asyncio
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config, _create_database, _direct_database_url, _drop_database,
    _run_in_database, _temporary_migration_database_url, _upgrade,
)

pytestmark = pytest.mark.e2e
PRIOR = "20261110_merge_lanes_sources"
REVISION = "20261110_profile_prompt_cache"


async def seed(connection):
    await connection.execute(sa.text("""
        INSERT INTO ai_provider_connections (id,name,provider,encrypted_api_key,anthropic_prompt_cache_supported)
        VALUES ('11111111-1111-1111-1111-111111111111','legacy','anthropic','fixture',false)
    """))
    await connection.execute(sa.text("""
        INSERT INTO ai_model_profiles (id,name,connection_id,model)
        VALUES ('22222222-2222-2222-2222-222222222222','legacy model',
                '11111111-1111-1111-1111-111111111111','model-a')
    """))


async def verify(connection):
    assert await connection.scalar(sa.text("SELECT anthropic_prompt_cache_supported FROM ai_provider_connections WHERE name='legacy'")) is False
    assert (await connection.execute(sa.text("SELECT model,anthropic_prompt_cache_supported FROM ai_model_profiles WHERE name='legacy model'"))).one() == ("model-a", None)
    # Legacy writers still omit the added nullable observation field.
    await connection.execute(sa.text("UPDATE ai_model_profiles SET model='model-b' WHERE name='legacy model'"))


async def verify_rollback(connection):
    assert await connection.scalar(sa.text("SELECT anthropic_prompt_cache_supported FROM ai_provider_connections WHERE name='legacy'")) is False
    assert await connection.scalar(sa.text("SELECT model FROM ai_model_profiles WHERE name='legacy model'")) == "model-b"


async def test_profile_observation_upgrade_and_rollback_preserve_legacy_data():
    name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    url = _direct_database_url(name)
    await _create_database(name)
    try:
        await asyncio.to_thread(_upgrade, url, PRIOR)
        await _run_in_database(url, seed)
        await asyncio.to_thread(_upgrade, url, REVISION)
        await _run_in_database(url, verify)
        def downgrade():
            with _temporary_migration_database_url(url):
                command.downgrade(_alembic_config(), PRIOR)
        await asyncio.to_thread(downgrade)
        await _run_in_database(url, verify_rollback)
    finally:
        await _drop_database(name)
