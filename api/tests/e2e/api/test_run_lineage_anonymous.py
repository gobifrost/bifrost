"""Anonymous and embedded starts run for their organization's identity.

No person signs in to a public form or an embedded app, so the run is for the
default identity of the form's or app's organization — the global identity
when it has none.
"""

import asyncio
import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.enums import IdentityKind
from src.models.orm.executions import Execution
from src.models.orm.users import User
from tests.e2e.api.test_embed_workflow_execution import _compute_hmac, _extract_token_from_redirect
from tests.e2e.api.test_form_publication import _solve_public_captcha
from tests.e2e.conftest import poll_until, write_and_register

pytestmark = pytest.mark.e2e


async def _identity(db_session, organization_id: UUID | None) -> UUID:
    query = (
        select(User.id).where(User.identity_kind == IdentityKind.GLOBAL_DEFAULT)
        if organization_id is None
        else select(User.id).where(
            User.identity_kind == IdentityKind.ORG_DEFAULT, User.organization_id == organization_id
        )
    )
    return (await db_session.execute(query)).scalar_one()


async def _lineage(db_session, **where) -> tuple:
    async def read():
        db_session.expire_all()
        row = (
            await db_session.execute(
                select(
                    Execution.id,
                    Execution.run_user_id,
                    Execution.started_by_user_id,
                    Execution.root_execution_id,
                ).filter_by(**where)
            )
        ).one_or_none()
        return tuple(row) if row is not None else None

    for _ in range(60):
        row = await read()
        if row is not None:
            return row
        await asyncio.sleep(0.5)
    raise AssertionError(f"no execution row for {where}")


def _workflow(e2e_client, platform_admin, name: str) -> dict:
    workflow = write_and_register(
        e2e_client,
        platform_admin.headers,
        f"{name}.py",
        f'from bifrost import workflow\n\n@workflow(name="{name}")\n'
        f"async def {name}(email: str | None = None) -> dict:\n    return {{'ok': True}}\n",
        name,
        organization_id=None,
    )
    response = e2e_client.patch(
        f"/api/workflows/{workflow['id']}",
        headers=platform_admin.headers,
        json={"access_level": "authenticated"},
    )
    assert response.status_code == 200, response.text
    return workflow


async def test_public_form_on_a_global_form_runs_for_the_global_identity(
    e2e_client, platform_admin, db_session
):
    name = f"lineage_public_{uuid.uuid4().hex[:8]}"
    workflow = _workflow(e2e_client, platform_admin, name)
    response = e2e_client.post(
        "/api/forms",
        headers=platform_admin.headers,
        json={
            "name": f"Lineage public form {name}",
            "workflow_id": workflow["id"],
            "form_schema": {"fields": [{"name": "email", "label": "Email", "type": "email", "required": True}]},
            "access_level": "authenticated",
            "organization_id": None,
        },
    )
    assert response.status_code == 201, response.text
    form = response.json()
    assert form["organization_id"] is None
    try:
        review = e2e_client.get(f"/api/forms/{form['id']}/publication-review", headers=platform_admin.headers).json()
        publication = e2e_client.put(
            f"/api/forms/{form['id']}/publication",
            headers=platform_admin.headers,
            json={"reviewed_fingerprint": review["fingerprint"], "allowed_origins": []},
        ).json()
        bootstrap = e2e_client.get(f"/embed/forms/public/{publication['public_key']}", follow_redirects=False)
        assert bootstrap.status_code == 302, bootstrap.text
        headers = {"Authorization": f"Bearer {bootstrap.headers['location'].split('#embed_token=')[1]}"}

        accepted = e2e_client.post(
            f"/api/forms/{form['id']}/submissions",
            headers=headers,
            json={
                "form_data": {"email": "visitor@example.com"},
                "submission_nonce": "nonce-lineage-000001",
                "honeypot": "",
                "captcha_payload": _solve_public_captcha(e2e_client, form["id"], headers),
            },
        )
        assert accepted.status_code == 200, accepted.text

        execution_id, run_user, started_by, root = await _lineage(db_session, form_id=UUID(form["id"]))
        identity = await _identity(db_session, None)
        assert (run_user, started_by, root) == (identity, identity, execution_id)
    finally:
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)
        e2e_client.delete(f"/api/files/editor?path={name}.py", headers=platform_admin.headers)


async def test_app_embed_in_an_organization_runs_for_that_organization_identity(
    e2e_client, platform_admin, org1, db_session
):
    name = f"lineage_embed_{uuid.uuid4().hex[:8]}"
    workflow = _workflow(e2e_client, platform_admin, name)
    response = e2e_client.post(
        "/api/applications",
        headers=platform_admin.headers,
        json={"name": name, "slug": name.replace("_", "-"), "app_model": "inline_v1", "organization_id": org1["id"]},
    )
    assert response.status_code == 201, response.text
    app = response.json()
    try:
        secret = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets", headers=platform_admin.headers, json={"name": "Test"}
        )
        assert secret.status_code in (200, 201), secret.text
        params = {"agent_id": "1"}
        entry = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": _compute_hmac(params, secret.json()["raw_secret"])},
            follow_redirects=False,
        )
        assert entry.status_code == 302, entry.text
        headers = {"Authorization": f"Bearer {_extract_token_from_redirect(entry)}"}

        started = e2e_client.post(
            "/api/workflows/execute", headers=headers, json={"workflow_id": workflow["id"], "input_data": {}}
        )
        assert started.status_code == 200, started.text
        execution_id = started.json()["execution_id"]
        assert poll_until(
            lambda: e2e_client.get(f"/api/executions/{execution_id}", headers=headers).json().get("status")
            in ("Success", "Failed")
            or None,
            max_wait=30.0,
        )

        row = await _lineage(db_session, id=UUID(execution_id))
        identity = await _identity(db_session, UUID(org1["id"]))
        assert row == (UUID(execution_id), identity, identity, UUID(execution_id))
    finally:
        e2e_client.delete(f"/api/applications/{app['id']}", headers=platform_admin.headers)
        e2e_client.delete(f"/api/files/editor?path={name}.py", headers=platform_admin.headers)
