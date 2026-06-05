"""Sub-plan 3 — Solution table deploy: schema/policies from the bundle, rows
preserved.

Criterion 11: redeploying a Solution with a changed table schema migrates
structure (the schema/policies JSONB on the Table row) and PRESERVES existing
rows (Document records). Row data is runtime state; deploy never writes or wipes
it. Mirrors the app source-vs-data split (§3.7).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.models.orm.solutions import Solution
from src.models.orm.tables import Document, Table
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    import src.core.redis_client as rc
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    rc._redis_client = None
    yield
    rc._redis_client = None


def _table_entry(table_id: str, name: str, schema: dict) -> dict:
    return {"id": table_id, "name": name, "schema": schema, "policies": None}


@pytest.mark.e2e
class TestSolutionTableDeploy:
    async def _install(self, db) -> Solution:
        sol = Solution(id=uuid.uuid4(), slug=f"tbl-{uuid.uuid4().hex[:8]}", name="TBL", organization_id=None)
        db.add(sol)
        await db.flush()
        return sol

    async def test_deploy_creates_table_with_schema_and_scope(self, db_session) -> None:
        db = db_session
        sol = await self._install(db)
        tid = str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            tables=[_table_entry(tid, "people", {"columns": [{"name": "email"}]})],
        ))
        await db.flush()

        tbl = await db.get(Table, uuid.UUID(tid))
        assert tbl is not None
        assert tbl.solution_id == sol.id
        assert tbl.organization_id == sol.organization_id
        assert tbl.schema == {"columns": [{"name": "email"}]}

    async def test_redeploy_changed_schema_preserves_rows(self, db_session) -> None:
        db = db_session
        sol = await self._install(db)
        tid = str(uuid.uuid4())

        # Deploy v1 schema.
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            tables=[_table_entry(tid, "people", {"columns": [{"name": "email"}]})],
        ))
        await db.flush()

        # Seed runtime rows (these are NOT part of the bundle).
        db.add(Document(id="row-1", table_id=uuid.UUID(tid), data={"email": "a@x.com"}))
        db.add(Document(id="row-2", table_id=uuid.UUID(tid), data={"email": "b@x.com"}))
        await db.flush()

        # Redeploy with a CHANGED schema (added column).
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            tables=[_table_entry(tid, "people", {"columns": [{"name": "email"}, {"name": "phone"}]})],
        ))
        await db.flush()

        tbl = await db.get(Table, uuid.UUID(tid))
        assert tbl is not None
        # Structure migrated.
        assert {"name": "phone"} in tbl.schema["columns"]
        # Rows preserved.
        rows = (
            await db.execute(select(Document.id).where(Document.table_id == uuid.UUID(tid)))
        ).scalars().all()
        assert set(rows) == {"row-1", "row-2"}

    async def test_malformed_policy_is_rejected_at_deploy(self, db_session) -> None:
        """A bad policy AST is rejected at deploy, not stored to fail at read
        time (Codex Sub-plan 3 P2)."""
        db = db_session
        sol = await self._install(db)
        tid = str(uuid.uuid4())
        bad = {"id": tid, "name": "bad", "schema": {}, "policies": [{"not_a_real_op": 123}]}
        with pytest.raises(Exception):
            await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, tables=[bad]))
        await db.rollback()

    async def test_full_replace_clears_removed_description(self, db_session) -> None:
        """Removing description from the bundle clears the DB value (full
        replace), not leaves it stale (Codex Sub-plan 3 P2)."""
        db = db_session
        sol = await self._install(db)
        tid = str(uuid.uuid4())
        e = _table_entry(tid, "t", {})
        e["description"] = "v1 desc"
        await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, tables=[e]))
        await db.flush()
        assert (await db.get(Table, uuid.UUID(tid))).description == "v1 desc"

        # Redeploy without description -> cleared.
        await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, tables=[_table_entry(tid, "t", {})]))
        await db.flush()
        assert (await db.get(Table, uuid.UUID(tid))).description is None

    async def test_policy_change_emits_policy_changed(self, db_session, monkeypatch) -> None:
        """Redeploying with changed policies invalidates subscribers' policy
        cache via publish_policy_changed; a first deploy (insert) does not
        (Codex Sub-plan 3 P1)."""
        import src.core.pubsub as pubsub

        calls: list[str] = []

        async def _spy(table_id: str) -> None:
            calls.append(table_id)

        monkeypatch.setattr(pubsub, "publish_policy_changed", _spy)

        db = db_session
        sol = await self._install(db)
        tid = str(uuid.uuid4())
        admin_policy = [{"name": "p1", "actions": ["read", "create", "update", "delete"]}]

        # Insert with explicit policies — no emission on create.
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, tables=[{"id": tid, "name": "t", "schema": {}, "policies": admin_policy}],
        ))
        await db.flush()
        assert calls == []

        # Redeploy with DIFFERENT policies — emission fires once.
        other_policy = [{"name": "p1", "actions": ["read"]}]
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, tables=[{"id": tid, "name": "t", "schema": {}, "policies": other_policy}],
        ))
        await db.flush()
        assert calls == [tid]

    async def test_redeploy_removing_table_deletes_it_for_this_install_only(self, db_session) -> None:
        db = db_session
        sol = await self._install(db)
        t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            tables=[_table_entry(t1, "keep", {}), _table_entry(t2, "drop", {})],
        ))
        await db.flush()
        # Redeploy without t2 → t2 removed for this install.
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, tables=[_table_entry(t1, "keep", {})],
        ))
        await db.flush()
        active = (
            await db.execute(select(Table.id).where(Table.solution_id == sol.id))
        ).scalars().all()
        assert set(active) == {uuid.UUID(t1)}
