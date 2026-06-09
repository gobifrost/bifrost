"""_derive_solution_scope picks the install scope from solution_id > form_id > app_id."""
from uuid import uuid4

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.routers.workflows import _derive_solution_scope


async def _org(db):
    o = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
    db.add(o)
    await db.flush()
    return o


async def _sol(db, org_id):
    s = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=org_id)
    db.add(s)
    await db.flush()
    return s


@pytest.mark.e2e
class TestDeriveSolutionScope:
    async def test_explicit_solution_id_wins(self, db_session):
        db = db_session
        sid = uuid4()
        got = await _derive_solution_scope(db, solution_id=str(sid), form_id=None, app_id=None)
        assert got == sid

    async def test_form_id_resolves_to_form_solution_id(self, db_session):
        db = db_session
        org = (await _org(db)).id
        sol = await _sol(db, org)
        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=sol.id, workflow_id="workflows/foo.py::main", created_by="test")
        db.add(form)
        await db.flush()
        got = await _derive_solution_scope(db, solution_id=None, form_id=str(form.id), app_id=None)
        assert got == sol.id

    async def test_app_id_resolves_to_application_solution_id(self, db_session):
        db = db_session
        org = (await _org(db)).id
        sol = await _sol(db, org)
        app = Application(id=uuid4(), name="a", slug=f"a-{uuid4().hex[:6]}", organization_id=org, solution_id=sol.id, repo_path="apps/a", created_by="test")
        db.add(app)
        await db.flush()
        got = await _derive_solution_scope(db, solution_id=None, form_id=None, app_id=str(app.id))
        assert got == sol.id

    async def test_none_when_no_source(self, db_session):
        got = await _derive_solution_scope(db_session, solution_id=None, form_id=None, app_id=None)
        assert got is None

    async def test_invalid_uuid_yields_none(self, db_session):
        got = await _derive_solution_scope(db_session, solution_id="not-a-uuid", form_id=None, app_id=None)
        assert got is None

    async def test_form_exists_with_null_solution_id_yields_none(self, db_session):
        db = db_session
        org = (await _org(db)).id
        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=None, workflow_id="workflows/foo.py::main", created_by="test")
        db.add(form)
        await db.flush()
        got = await _derive_solution_scope(db, solution_id=None, form_id=str(form.id), app_id=None)
        assert got is None

    async def test_form_id_with_no_matching_row_yields_none(self, db_session):
        # Valid UUID, but no such form -> None (not an error).
        got = await _derive_solution_scope(db_session, solution_id=None, form_id=str(uuid4()), app_id=None)
        assert got is None
