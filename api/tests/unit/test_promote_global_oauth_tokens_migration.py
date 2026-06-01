"""Behavioral test for the global-OAuth-token heal migration.

The migration (alembic/versions/20260601_promote_mis_stamped_global_oauth_tokens.py)
is pure SQL, so we run its two statements against a seeded DB and assert it:

  - promotes a provider-org-stamped org-level token of a GLOBAL provider to NULL;
  - leaves a legitimately org-scoped connection's token alone;
  - drops the redundant provider-org row when a real NULL token already exists;
  - never touches per-user tokens.

Coupled to the migration file by importing its SQL via op.execute capture, so a
drift in the migration's WHERE clauses fails this test rather than passing silently.
"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260601_promote_mis_stamped_global_oauth_tokens.py"
)


def _migration_sql() -> list[str]:
    """Capture the SQL strings the migration's upgrade() passes to op.execute."""
    spec = importlib.util.spec_from_file_location("_heal_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)

    captured: list[str] = []

    class _FakeOp:
        @staticmethod
        def execute(sql):
            captured.append(str(sql))

    # Inject a fake `op` before exec so `from alembic import op` resolves to it.
    import sys
    import types

    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = _FakeOp()  # type: ignore[attr-defined]
    sys.modules["alembic"] = fake_alembic
    try:
        spec.loader.exec_module(module)
        module.upgrade()
    finally:
        sys.modules.pop("alembic", None)
    assert len(captured) == 2, "migration should issue exactly DELETE then UPDATE"
    return captured


async def _org(db, name, *, is_provider=False):
    o = Organization(name=name, created_by="test", is_provider=is_provider)
    db.add(o)
    await db.flush()
    return o


async def _provider(db, *, organization_id):
    p = OAuthProvider(
        provider_name=f"conn_{uuid4().hex[:8]}",
        oauth_flow_type="client_credentials",
        client_id="cid",
        encrypted_client_secret=b"sec",
        organization_id=organization_id,
    )
    db.add(p)
    await db.flush()
    return p


async def _token(db, *, provider_id, organization_id, user_id=None):
    t = OAuthToken(
        provider_id=provider_id,
        organization_id=organization_id,
        user_id=user_id,
        encrypted_access_token=b"acc",
        status="completed",
    )
    db.add(t)
    await db.flush()
    return t


async def _run_migration(db):
    for sql in _migration_sql():
        await db.execute(text(sql))
    await db.flush()


@pytest.mark.asyncio
async def test_promotes_provider_org_token_of_global_provider(db_session):
    provider_org = await _org(db_session, f"Provider {uuid4().hex[:6]}", is_provider=True)
    gp = await _provider(db_session, organization_id=None)  # GLOBAL provider
    tok = await _token(db_session, provider_id=gp.id, organization_id=provider_org.id)

    await _run_migration(db_session)

    await db_session.refresh(tok)
    assert tok.organization_id is None, "mis-stamped global token should be promoted to NULL"


@pytest.mark.asyncio
async def test_leaves_org_specific_connection_token_alone(db_session):
    """A connection whose PROVIDER is org-scoped is a legitimate per-org
    connection — its token must not be touched even if stamped with the
    provider org.
    """
    provider_org = await _org(db_session, f"Provider {uuid4().hex[:6]}", is_provider=True)
    op_ = await _provider(db_session, organization_id=provider_org.id)  # org-scoped provider
    tok = await _token(db_session, provider_id=op_.id, organization_id=provider_org.id)

    await _run_migration(db_session)

    await db_session.refresh(tok)
    assert tok.organization_id == provider_org.id, "per-org connection token must be preserved"


@pytest.mark.asyncio
async def test_drops_redundant_provider_org_token_when_global_exists(db_session):
    provider_org = await _org(db_session, f"Provider {uuid4().hex[:6]}", is_provider=True)
    gp = await _provider(db_session, organization_id=None)
    good = await _token(db_session, provider_id=gp.id, organization_id=None)
    dup = await _token(db_session, provider_id=gp.id, organization_id=provider_org.id)

    await _run_migration(db_session)

    rows = (
        await db_session.execute(
            select(OAuthToken).where(OAuthToken.provider_id == gp.id)
        )
    ).scalars().all()
    ids = {r.id for r in rows}
    assert good.id in ids, "the real global token must survive"
    assert dup.id not in ids, "the redundant provider-org token must be dropped"
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ignores_non_provider_org_token(db_session):
    """A token stamped with a normal (non-provider) org is a real per-org
    token and must never be promoted.
    """
    normal_org = await _org(db_session, f"Managed {uuid4().hex[:6]}", is_provider=False)
    gp = await _provider(db_session, organization_id=None)
    tok = await _token(db_session, provider_id=gp.id, organization_id=normal_org.id)

    await _run_migration(db_session)

    await db_session.refresh(tok)
    assert tok.organization_id == normal_org.id, "non-provider-org token must be left alone"
