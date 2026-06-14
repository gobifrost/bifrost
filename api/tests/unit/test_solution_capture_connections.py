"""Task 4: capture declares the integrations a solution's workflows reference.

Mirrors test_solution_capture.py's fixtures (db_session + an in-memory repo).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.models.orm.integrations import Integration
from src.models.orm.oauth import OAuthProvider
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.capture import SolutionCaptureService


pytestmark = pytest.mark.e2e


class _FakeRepo:
    """In-memory stand-in for RepoStorage: maps repo paths to byte content."""

    def __init__(self, files: dict[str, bytes]):
        self._files = files

    async def list(self, prefix: str = "") -> list[str]:
        return [p for p in self._files if p.startswith(prefix)]

    async def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc


async def _make_solution(db, *, org_id=None) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"conn-{uuid.uuid4().hex[:8]}",
        name="Conn",
        organization_id=org_id,
    )
    db.add(sol)
    await db.flush()
    return sol


async def test_capture_declares_referenced_integration(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)

    wf = Workflow(
        id=uuid.uuid4(),
        name=f"wf-{uuid.uuid4().hex[:8]}",
        function_name="main",
        path="workflows/sync.py",
        type="workflow",
        is_active=True,
        solution_id=sol.id,
    )
    db.add(wf)

    integ = Integration(id=uuid.uuid4(), name="HaloPSA")
    db.add(integ)
    await db.flush()
    # OAuth provider carries client_id — the template MUST scrub it.
    db.add(OAuthProvider(
        id=uuid.uuid4(),
        provider_name="halopsa",
        display_name="HaloPSA",
        client_id="super-secret-client-id",
        encrypted_client_secret=b"nope",
        integration_id=integ.id,
    ))
    await db.flush()

    repo = _FakeRepo({
        "workflows/sync.py": b'tickets = sdk.integrations.get("HaloPSA").list()\n',
    })

    svc = SolutionCaptureService(db, repo=repo)
    entries = await svc._connection_entries(sol.id)

    names = {e["integration_name"] for e in entries}
    assert "HaloPSA" in names
    halo = next(e for e in entries if e["integration_name"] == "HaloPSA")
    assert halo["template"]["name"] == "HaloPSA"
    # Secret-scrubbed: the carried OAuth shape never includes client_id.
    assert "client_id" not in (halo["template"].get("oauth") or {})

    # A SolutionConnectionSchema row was upserted for the install.
    rows = (
        await db.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == sol.id
            )
        )
    ).scalars().all()
    assert {r.integration_name for r in rows} == {"HaloPSA"}
    assert rows[0].template["name"] == "HaloPSA"
    assert "client_id" not in (rows[0].template.get("oauth") or {})


async def test_connection_entries_prefers_persisted_rows(db_session) -> None:
    """Drive F4: export/DR of an installed solution must read the persisted
    SolutionConnectionSchema rows, NOT re-scan workflow source.

    For a deployed install the source lives under _solutions/ (unreadable via
    repo.read of the _repo/ path), so the scan path would silently drop every
    declaration. With persisted rows present, _connection_entries returns them
    directly — carrying integration_name + template + position — without needing
    the repo at all.
    """
    db = db_session
    sol = await _make_solution(db)

    # A workflow whose source is NOT in the repo (simulating a deployed install).
    wf = Workflow(
        id=uuid.uuid4(),
        name=f"wf-{uuid.uuid4().hex[:8]}",
        function_name="main",
        path="workflows/sync.py",
        type="workflow",
        is_active=True,
        solution_id=sol.id,
    )
    db.add(wf)

    # Persisted rows created by deploy (out of order to prove position is carried).
    db.add(SolutionConnectionSchema(
        solution_id=sol.id,
        integration_name="Microsoft365",
        template={"name": "Microsoft365", "config_schema": [], "oauth": None},
        position=1,
    ))
    db.add(SolutionConnectionSchema(
        solution_id=sol.id,
        integration_name="HaloPSA",
        template={"name": "HaloPSA", "config_schema": [], "oauth": None},
        position=0,
    ))
    await db.flush()

    # Empty repo: a re-scan would find nothing. Persisted rows must win.
    repo = _FakeRepo({})
    svc = SolutionCaptureService(db, repo=repo)
    entries = await svc._connection_entries(sol.id)

    # Both declarations carried, ordered by position, with templates intact.
    assert [e["integration_name"] for e in entries] == ["HaloPSA", "Microsoft365"]
    assert [e["position"] for e in entries] == [0, 1]
    assert entries[0]["template"]["name"] == "HaloPSA"
    assert entries[1]["template"]["name"] == "Microsoft365"


async def test_connection_entries_idempotent_upsert(db_session) -> None:
    """Re-running capture updates the same row (no unique-constraint blowup)."""
    db = db_session
    sol = await _make_solution(db)
    wf = Workflow(
        id=uuid.uuid4(),
        name=f"wf-{uuid.uuid4().hex[:8]}",
        function_name="main",
        path="workflows/sync.py",
        type="workflow",
        is_active=True,
        solution_id=sol.id,
    )
    db.add(wf)
    db.add(Integration(id=uuid.uuid4(), name="HaloPSA"))
    await db.flush()

    repo = _FakeRepo({"workflows/sync.py": b'integrations.get("HaloPSA")\n'})
    svc = SolutionCaptureService(db, repo=repo)
    await svc._connection_entries(sol.id)
    await db.flush()
    await svc._connection_entries(sol.id)
    await db.flush()

    rows = (
        await db.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == sol.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
