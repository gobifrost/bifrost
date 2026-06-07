"""Provenance columns exist on Table and Config for orphan-and-reattach."""
import pytest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.tables import Table


@pytest.mark.e2e
class TestOrphanProvenanceColumns:
    async def test_table_provenance_roundtrip(self, db_session: AsyncSession) -> None:
        db = db_session
        sol_id = uuid4()
        t = Table(
            id=uuid4(),
            name=f"orph-{uuid4().hex[:8]}",
            organization_id=None,
            origin_solution_slug="acme-crm",
            origin_solution_id=sol_id,
            orphaned_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        assert t.origin_solution_slug == "acme-crm"
        assert t.origin_solution_id == sol_id
        assert t.orphaned_at is not None

    async def test_table_provenance_defaults_none(self, db_session: AsyncSession) -> None:
        db = db_session
        t = Table(id=uuid4(), name=f"plain-{uuid4().hex[:8]}", organization_id=None)
        db.add(t)
        await db.flush()
        assert t.origin_solution_slug is None
        assert t.origin_solution_id is None
        assert t.orphaned_at is None

    async def test_config_provenance_roundtrip(self, db_session: AsyncSession) -> None:
        db = db_session
        sol_id = uuid4()
        c = Config(
            id=uuid4(),
            key="REGION",
            value={"value": "x"},
            config_type=ConfigType.STRING,
            organization_id=None,
            updated_by="t",
            origin_solution_slug="acme-crm",
            origin_solution_id=sol_id,
            orphaned_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()
        assert c.origin_solution_slug == "acme-crm"
        assert c.origin_solution_id == sol_id
        assert c.orphaned_at is not None

    async def test_config_provenance_defaults_none(self, db_session: AsyncSession) -> None:
        db = db_session
        c = Config(
            id=uuid4(),
            key="REGION",
            value={"value": "x"},
            config_type=ConfigType.STRING,
            organization_id=None,
            updated_by="t",
        )
        db.add(c)
        await db.flush()
        assert c.origin_solution_slug is None
        assert c.origin_solution_id is None
        assert c.orphaned_at is None
