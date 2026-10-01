"""E2E: solution lifecycle — uninstall (status flip) vs hard-delete (confirmed cascade).

uninstall = POST /{id}/uninstall → flips status to 'inactive', data frozen in place.
  - owned Table row still has solution_id == sid (NOT orphaned/nulled)
  - Solution row still exists after uninstall
  - idempotent: a second uninstall returns 200

hard-delete = DELETE /{id}?confirm=<slug>
  - confirm mismatch → 422, nothing touched
  - confirm match → Solution row gone, owned Table row gone (cascade)
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.solutions import Solution as SolutionORM
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_uninstall_flips_status_and_freezes_data(
    e2e_client, platform_admin, db_session
):
    """Uninstall sets status='inactive', leaves all owned data frozen in place."""
    headers = platform_admin.headers
    slug = f"uninst-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    real_tid = uuid.uuid4()
    db_session.add(
        Table(id=real_tid, name=f"customers_{slug}", solution_id=UUID(sid))
    )
    await db_session.commit()

    summary = e2e_client.get(
        f"/api/solutions/{sid}/deletion-summary", headers=headers
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["solution_id"] == sid
    assert summary.json()["tables"] >= 1

    r = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive"

    # Solution row STILL EXISTS (not deleted).
    db_session.expire_all()
    sol = await db_session.get(SolutionORM, UUID(sid))
    assert sol is not None, "solution row was deleted on uninstall — should only flip status"
    assert sol.status == "inactive"

    # Owned table still has solution_id == sid (NOT orphaned).
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is not None, "table row deleted on uninstall — data was destroyed"
    assert tbl.solution_id == UUID(sid), (
        f"solution_id was cleared on uninstall (got {tbl.solution_id}) — "
        "uninstall must NOT mutate owned entities"
    )


async def test_uninstall_is_idempotent(e2e_client, platform_admin, db_session):
    """Uninstalling an already-inactive install returns 200 without error."""
    headers = platform_admin.headers
    slug = f"uninst-idem-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    r1 = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r1.status_code == 200, r1.text

    r2 = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "inactive"


async def test_hard_delete_requires_confirm(e2e_client, platform_admin, db_session):
    """A wrong confirm token → 422; nothing is deleted."""
    headers = platform_admin.headers
    slug = f"hdel-confirm-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    bad = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                              params={"confirm": "wrong-slug"})
    assert bad.status_code in (400, 422), f"expected 4xx on mismatch, got {bad.status_code}"

    # Install must still exist.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 200, "solution was deleted despite confirm mismatch"


async def test_hard_delete_cascades_all_owned_rows(e2e_client, platform_admin, db_session):
    """Confirmed hard-delete removes the Solution row and all owned entities via cascade."""
    headers = platform_admin.headers
    slug = f"hdel-cas-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    # Cascade ownership is the contract here; deploy-to-entity paths have
    # their own E2E coverage.
    real_tid = uuid.uuid4()
    db_session.add_all([
        Table(
            id=real_tid, name=f"rows_{slug}", solution_id=UUID(sid),
            schema={"columns": [{"name": "val"}]},
        ),
        Workflow(
            id=uuid.uuid4(), name=f"go_{slug}", function_name="go",
            path="workflows/w.py", type="workflow", solution_id=UUID(sid),
        ),
        SolutionConfigSchema(
            id=uuid.uuid4(), solution_id=UUID(sid), key=f"API_KEY_{slug}",
            type="secret", required=True, description="needed", position=0,
        ),
    ])
    await db_session.commit()

    ok = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                             params={"confirm": slug})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["solution_id"] == sid
    assert body["tables_deleted"] >= 1
    assert body["workflows_deleted"] >= 1
    assert body["config_declarations_deleted"] >= 1

    # Solution row is gone.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, "solution row still exists after hard-delete"

    # Owned Table row cascaded away.
    db_session.expire_all()
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is None, "owned table survived hard-delete — cascade did not fire"

    # Owned Workflow row cascaded away.
    wf_rows = (
        await db_session.execute(select(Workflow).where(Workflow.solution_id == UUID(sid)))
    ).scalars().all()
    assert wf_rows == [], f"owned workflows survived hard-delete: {len(wf_rows)}"

    declarations = (
        await db_session.execute(
            select(SolutionConfigSchema).where(SolutionConfigSchema.solution_id == UUID(sid))
        )
    ).scalars().all()
    assert declarations == [], "owned config declarations survived hard-delete"


async def test_deletion_summary_counts_external_listeners(
    e2e_client, platform_admin, db_session
):
    """The hard-delete cascades operator-created subscriptions on the install's
    sources (and on its workflows), so the summary must report them separately
    instead of silently undercounting."""
    from src.models.orm.events import EventSource, EventSubscription

    headers = platform_admin.headers
    slug = f"hdel-ext-{uuid.uuid4().hex[:8]}"
    sid = UUID(_create_solution(e2e_client, headers, slug))

    source_id, sol_wf, ws_wf = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_source = uuid.uuid4()
    db_session.add_all([
        EventSource(id=source_id, name=f"topic_{slug}", source_type="topic",
                    solution_id=sid, created_by="test"),
        EventSource(id=other_source, name=f"ws_topic_{slug}", source_type="topic",
                    solution_id=None, created_by="test"),
        Workflow(id=sol_wf, name=f"sol_{slug}", function_name="go",
                 path="workflows/s.py", type="workflow", solution_id=sid),
        Workflow(id=ws_wf, name=f"ws_{slug}", function_name="go",
                 path="workflows/ws.py", type="workflow", solution_id=None),
    ])
    await db_session.flush()
    managed_sub, ext_on_source, ext_to_sol_wf, unrelated = (uuid.uuid4() for _ in range(4))
    db_session.add_all([
        # Managed: counted under `events`.
        EventSubscription(id=managed_sub, event_source_id=source_id, workflow_id=sol_wf,
                          solution_id=sid, created_by="solution-deploy"),
        # External listener on the Solution's source.
        EventSubscription(id=ext_on_source, event_source_id=source_id, workflow_id=ws_wf,
                          solution_id=None, created_by="operator"),
        # External sub on a workspace source targeting a Solution workflow.
        EventSubscription(id=ext_to_sol_wf, event_source_id=other_source, workflow_id=sol_wf,
                          solution_id=None, created_by="operator"),
        # Unrelated workspace sub: survives the hard-delete, not counted.
        EventSubscription(id=unrelated, event_source_id=other_source, workflow_id=ws_wf,
                          solution_id=None, created_by="operator"),
    ])
    await db_session.commit()

    summary = e2e_client.get(f"/api/solutions/{sid}/deletion-summary", headers=headers)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["events"] == 2  # 1 source + 1 managed subscription
    assert body["external_event_subscriptions"] == 2

    ok = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                             params={"confirm": slug})
    assert ok.status_code == 200, ok.text

    db_session.expire_all()
    remaining = set((await db_session.execute(
        select(EventSubscription.id).where(EventSubscription.id.in_(
            [managed_sub, ext_on_source, ext_to_sol_wf, unrelated]
        ))
    )).scalars().all())
    # The summary matched reality: exactly the counted rows were removed.
    assert remaining == {unrelated}
