# Solution Capture → Pull → Deploy Round-Trip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the capture→deploy round-trip so entities captured into a Solution (via UI or CLI) survive deploy: capture enqueues a pending row, `bifrost solution pull` materializes captured entities into the source `.bifrost/` manifest, and deploy 409-BLOCKS (never silently deletes) when an un-pulled capture is absent from the manifest. **Then (Tasks 8–9) validate the rebuilt `bifrost:build` skill empirically** with fresh Sonnet builds across both modes to a 3-consecutive-clean streak — the platform fix (Tasks 1–7) unblocks the solution track. This single plan runs the whole arc end-to-end.

**Architecture:** A new `pending_captures` queue table is the single source of truth for "captured but not yet pulled to source." Capture inserts rows; `pull` (a new CLI command) reuses the EXISTING `/export` endpoint to fetch a live-rebuilt `.bifrost/` bundle and unzips only its manifest files into the workspace, then clears the queue rows it materialized; deploy checks the queue before its reconcile sweep and 409s on any pending entity absent from the incoming manifest. Entities absent with NO queue row remain genuine deletes (unchanged behavior).

**Tech Stack:** Python (FastAPI, SQLAlchemy, Alembic, pytest), Bifrost CLI (`api/bifrost/`), the existing solutions services (`capture.py`, `export.py`, the deployer).

**Spec:** `docs/superpowers/specs/2026-06-15-solution-capture-roundtrip-design.md`

---

## Key discovery (shrinks the work)

`bifrost solution capture` already builds a `SolutionBundle` server-side (`SolutionCaptureService`). The existing endpoint **`POST /api/solutions/{id}/export`** (`api/src/routers/solutions.py:206`) **rebuilds the workspace bundle LIVE from the entities the install currently owns** (`SolutionCaptureService(db).bundle_for(...)` → `build_workspace_zip(...)`) and returns a `.bifrost/`-complete zip. So `pull` does NOT need new serialization or a new read endpoint — it calls `/export` (shareable mode, no values) and unzips only the `.bifrost/*.yaml` entries. The genuinely new server work is just: the queue table, enqueue-on-capture, a clear-queue endpoint, and the deploy guard.

## File Structure

**Create:**
- `api/src/models/orm/pending_capture.py` — `PendingCaptureORM` (the queue table).
- `api/alembic/versions/<ts>_pending_captures.py` — migration creating the table.
- `api/tests/unit/test_pending_captures.py` — enqueue + guard + clear unit tests.
- `api/tests/e2e/platform/test_capture_roundtrip.py` — the full UI/CLI-capture → deploy-blocks → pull → deploy-succeeds → genuine-delete e2e.

**Modify:**
- `api/src/services/solutions/capture.py` — `SolutionCaptureService.capture()` inserts a `pending_captures` row per captured entity.
- `api/src/routers/solutions.py` — `deploy_solution` runs the pending-capture guard before deploy; add `POST /{id}/pull/ack` (clear queue rows server-authoritatively); `SolutionEntities`/status may surface pending count (optional).
- `api/src/services/solutions/deployer.py` (or wherever `SolutionDeployer.deploy` lives) — only if the guard is cleaner inside the deployer; default is to guard in the router before calling deploy.
- `api/bifrost/commands/solution.py` — add the `pull` command; register it in the `solution_group`.

**Reuse (no change):**
- `api/src/routers/solutions.py:206` `export_solution` + `SolutionCaptureService.bundle_for` + `build_workspace_zip` — `pull` consumes these.

---

## Conventions for every task

- **Worktree only** (`solutions-success-criteria`). Never two concurrent `./test.sh`.
- **Run tests in-container** (the `./test.sh` api-exit flake): `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest <path> -v`. (`-k` is a separate arg, not in the path string.)
- **Solution-managed writes MUST use Core statements** (insert/update/delete via `sqlalchemy` Core), not ORM-object mutation — the always-on read-only `before_flush` guard (`api/src/services/solutions/guard.py`) 500s on ORM mutation in prod but passes in isolated unit tests. **Install that guard in every test that writes a solution-managed entity** so the test is prod-faithful (see memory `project_solution_managed_guard_deploy_core`). The `pending_captures` table is NOT solution-managed, so its own writes are unguarded — but tests that also touch the captured entities need the guard.
- **Restart the test-stack API container after deploy/capture-path code changes** (it's long-running; the HTTP API reads source at boot, the test-runner reads fresh): `docker restart bifrost-test-75bc0d9c-api-1` (or the api container for this project).
- **Commit** at the end of each task with the message shown.

---

## Task 1: `pending_captures` ORM + migration

**Files:**
- Create: `api/src/models/orm/pending_capture.py`
- Create: `api/alembic/versions/<ts>_pending_captures.py`
- Test: `api/tests/unit/test_pending_captures.py`

- [ ] **Step 1: Write the failing test (table exists + unique constraint)**

```python
# api/tests/unit/test_pending_captures.py
import uuid
import pytest
from sqlalchemy import select
from src.models.orm.pending_capture import PendingCaptureORM


@pytest.mark.asyncio
async def test_pending_capture_row_roundtrips(db_session):
    sol_id = uuid.uuid4()
    row = PendingCaptureORM(
        solution_id=sol_id,
        entity_type="form",
        entity_id="abc-123",
        captured_by=None,
    )
    db_session.add(row)
    await db_session.commit()

    got = (await db_session.execute(
        select(PendingCaptureORM).where(PendingCaptureORM.solution_id == sol_id)
    )).scalars().all()
    assert len(got) == 1
    assert got[0].entity_type == "form"
    assert got[0].entity_id == "abc-123"
    assert got[0].captured_at is not None
```

(Use the project's existing async DB fixture — find it in `api/tests/` conftest, commonly `db_session`. If the fixture name differs, match it.)

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py::test_pending_capture_row_roundtrips -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `PendingCaptureORM` (or table missing).

- [ ] **Step 3: Write the ORM model**

```python
# api/src/models/orm/pending_capture.py
"""Queue of entities captured into a Solution but not yet pulled into source.

A capture (UI or CLI) sets solution_id on the entity server-side AND inserts a
row here. `bifrost solution pull` materializes the entity into the workspace
.bifrost/ manifest and clears its row. Deploy 409-blocks while any row for the
install is absent from the incoming manifest — so a captured-but-unpulled entity
is never silently deleted by deploy's full-replace reconcile.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base  # match the project's declarative Base import


class PendingCaptureORM(Base):
    __tablename__ = "pending_captures"
    __table_args__ = (
        UniqueConstraint(
            "solution_id", "entity_type", "entity_id",
            name="uq_pending_capture_entity",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(index=True)
    entity_type: Mapped[str] = mapped_column(String(32))   # table|form|agent|config|event|claim
    entity_id: Mapped[str] = mapped_column(String(255))    # entity id; for config, its key
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    captured_by: Mapped[UUID | None] = mapped_column(default=None, nullable=True)
```

Confirm the real `Base` import path by checking a sibling, e.g. `api/src/models/orm/forms.py` line 1-20 (it imports `Base` from somewhere — match it exactly). Ensure the model is imported by the ORM package `__init__` if the project registers models there (check `api/src/models/orm/__init__.py`).

- [ ] **Step 4: Write the migration**

First find the current head to chain from (multiple heads exist on this branch):
```bash
cd api && alembic heads
```
Pick the solutions-lineage head (most recent `20260614_solution_*`). Then:

```python
# api/alembic/versions/<ts>_pending_captures.py
"""pending_captures queue table

Revision ID: pending_captures
Revises: <CURRENT_SOLUTIONS_HEAD>   # from `alembic heads` — e.g. 20260614_solution_triggers
"""
from alembic import op
import sqlalchemy as sa

revision = "pending_captures"
down_revision = "<CURRENT_SOLUTIONS_HEAD>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_captures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_by", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("solution_id", "entity_type", "entity_id", name="uq_pending_capture_entity"),
    )
    op.create_index("ix_pending_captures_solution_id", "pending_captures", ["solution_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_captures_solution_id", table_name="pending_captures")
    op.drop_table("pending_captures")
```

Match `sa.Uuid()` vs `postgresql.UUID(as_uuid=True)` to whatever the sibling migrations use (check `20260614_solution_triggers.py`).

- [ ] **Step 5: Apply migration to the test DB + run the test**

The test stack applies migrations on stack boot/reset. Reset state so the new migration runs, then test:
```bash
docker restart bifrost-test-75bc0d9c-api-1   # picks up the new model
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py::test_pending_capture_row_roundtrips -v
```
Expected: PASS. (If the test DB doesn't auto-migrate, follow the memory `project_debug_stack_migration_apply` pattern: restart the init container that runs alembic, then api.)

- [ ] **Step 6: Commit**

```bash
git add api/src/models/orm/pending_capture.py api/alembic/versions/*pending_captures*.py api/tests/unit/test_pending_captures.py api/src/models/orm/__init__.py
git commit -m "feat(solutions): pending_captures queue table + migration"
```

---

## Task 2: Capture enqueues pending rows

**Files:**
- Modify: `api/src/services/solutions/capture.py` (`SolutionCaptureService.capture`)
- Test: `api/tests/unit/test_pending_captures.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# append to api/tests/unit/test_pending_captures.py
@pytest.mark.asyncio
async def test_capture_enqueues_one_row_per_entity(db_session, captured_solution_fixture):
    """After capture(), each captured table/form/agent/config has a pending row."""
    sol, selectors = captured_solution_fixture  # a solution + loose entities in its org
    from src.services.solutions.capture import SolutionCaptureService

    result = await SolutionCaptureService(db_session).capture(sol.id, selectors)
    await db_session.commit()

    rows = (await db_session.execute(
        select(PendingCaptureORM).where(PendingCaptureORM.solution_id == sol.id)
    )).scalars().all()
    keyset = {(r.entity_type, r.entity_id) for r in rows}
    # one row per captured entity (match what selectors captured)
    assert ("table", str(selectors.table_id)) in keyset
    assert ("form", str(selectors.form_id)) in keyset
    # re-capture is idempotent (UNIQUE) — capturing again adds no duplicate
    await SolutionCaptureService(db_session).capture(sol.id, selectors)
    await db_session.commit()
    rows2 = (await db_session.execute(
        select(PendingCaptureORM).where(PendingCaptureORM.solution_id == sol.id)
    )).scalars().all()
    assert len(rows2) == len(rows)
```

(Build `captured_solution_fixture` to create an install + a loose same-org table and form — mirror the setup in the existing capture e2e/unit tests; reuse `SolutionCaptureService`'s candidate model. If a similar fixture exists in `api/tests/`, reuse it. Install the read-only guard in the fixture per the Conventions note, since capture mutates solution-managed entities.)

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py::test_capture_enqueues_one_row_per_entity -v`
Expected: FAIL — no rows enqueued (assert on keyset fails).

- [ ] **Step 3: Enqueue in `capture()`**

In `SolutionCaptureService.capture`, after the entities are stamped with `solution_id` and before/with the commit, insert a queue row per captured entity using a **Core upsert** (so re-capture is a no-op via the unique constraint). Add near the end of `capture()`:

```python
# capture.py — inside capture(), after ownership is stamped
from sqlalchemy.dialects.postgresql import insert as pg_insert
from src.models.orm.pending_capture import PendingCaptureORM

def _enqueue(entity_type: str, entity_id: str) -> None:
    stmt = pg_insert(PendingCaptureORM.__table__).values(
        id=uuid4(),
        solution_id=solution_id,
        entity_type=entity_type,
        entity_id=str(entity_id),
        captured_at=datetime.now(timezone.utc),
        captured_by=captured_by,   # thread the acting user in, or None
    ).on_conflict_do_nothing(constraint="uq_pending_capture_entity")
    await self.db.execute(stmt)

for tid in selectors.tables:   # match the real selector attribute names in this service
    await _enqueue("table", tid)
for fid in selectors.forms:
    await _enqueue("form", fid)
for aid in selectors.agents:
    await _enqueue("agent", aid)
for cfg in selectors.configs:
    await _enqueue("config", cfg)
for eid in selectors.events:
    await _enqueue("event", eid)
for clm in selectors.claims:
    await _enqueue("claim", clm)
```

Adapt the selector attribute names to whatever `SolutionCaptureService` actually iterates (read the `capture()` body — it already loops the captured entity types to stamp them; enqueue alongside each stamp). `captured_by` should be the acting user id if available in the service; otherwise `None`. **Use Core (`pg_insert`), not ORM add**, for consistency with the guard discipline.

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/capture.py api/tests/unit/test_pending_captures.py
git commit -m "feat(solutions): capture enqueues pending_captures rows (idempotent)"
```

---

## Task 3: Deploy guard — block on pending-but-absent, never delete

**Files:**
- Modify: `api/src/routers/solutions.py` (`deploy_solution`)
- Test: `api/tests/unit/test_pending_captures.py` (extend)

- [ ] **Step 1: Write the failing tests (block vs genuine-delete)**

```python
# append to api/tests/unit/test_pending_captures.py
from src.services.solutions.pending import unpulled_blockers  # helper added in step 3


def test_unpulled_capture_blocks(_make_pending_and_manifest):
    # one pending form, manifest does NOT contain it → blocker
    pending = [("form", "form-1")]
    manifest_ids = {"table": set(), "form": set(), "agent": set(), "config": set(),
                    "event": set(), "claim": set()}
    blockers = unpulled_blockers(pending, manifest_ids)
    assert blockers == [("form", "form-1")]


def test_pulled_then_removed_is_not_blocked():
    # genuine delete: entity absent from manifest AND no pending row
    pending = []  # nothing pending
    manifest_ids = {"table": set(), "form": set(), "agent": set(), "config": set(),
                    "event": set(), "claim": set()}
    assert unpulled_blockers(pending, manifest_ids) == []


def test_pending_present_in_manifest_clears():
    # captured AND now in the manifest (pulled) → not a blocker
    pending = [("form", "form-1")]
    manifest_ids = {"table": set(), "form": {"form-1"}, "agent": set(), "config": set(),
                    "event": set(), "claim": set()}
    assert unpulled_blockers(pending, manifest_ids) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py -k unpulled -v`
Expected: FAIL — `src.services.solutions.pending` / `unpulled_blockers` does not exist.

- [ ] **Step 3: Write the pure guard helper**

```python
# api/src/services/solutions/pending.py
"""Deploy-time guard: which captured-but-unpulled entities are absent from the
incoming manifest (and therefore block the deploy)."""
from __future__ import annotations


def unpulled_blockers(
    pending: list[tuple[str, str]],            # [(entity_type, entity_id), ...] from pending_captures
    manifest_ids: dict[str, set[str]],          # {entity_type: {id, ...}} present in the deploy body
) -> list[tuple[str, str]]:
    """Return pending entities NOT present in the incoming manifest.

    An entity that is pending (captured, not yet pulled) AND absent from the
    manifest must block the deploy — otherwise the full-replace reconcile would
    delete it. An entity absent with NO pending row is a genuine delete and is
    NOT returned here.
    """
    out: list[tuple[str, str]] = []
    for etype, eid in pending:
        if eid not in manifest_ids.get(etype, set()):
            out.append((etype, eid))
    return out
```

- [ ] **Step 4: Run the pure-helper tests to verify they pass**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py -k unpulled -v` (and the other two new ones).
Expected: PASS.

- [ ] **Step 5: Wire the guard into `deploy_solution` (before reconcile)**

In `api/src/routers/solutions.py` `deploy_solution`, after loading the solution and acquiring the write lock but BEFORE calling `deployer.deploy(...)`:

```python
# build the manifest id set from the incoming body
from src.services.solutions.pending import unpulled_blockers
from src.models.orm.pending_capture import PendingCaptureORM
from sqlalchemy import select

manifest_ids = {
    "table": {t["id"] for t in (body.tables or []) if t.get("id")},
    "form": {f["id"] for f in (body.forms or []) if f.get("id")},
    "agent": {a["id"] for a in (body.agents or []) if a.get("id")},
    "config": {str(k) for k in (body.config_schemas or {})},   # config keyed by key
    "event": {e["id"] for e in (body.events or []) if e.get("id")},
    "claim": {c["id"] for c in (body.claims or []) if c.get("id")},
}
pending_rows = (await ctx.db.execute(
    select(PendingCaptureORM.entity_type, PendingCaptureORM.entity_id)
    .where(PendingCaptureORM.solution_id == solution_id)
)).all()
# Drop dangling rows whose entity no longer exists? (defer — they just won't match a manifest entry;
# if they block spuriously, prune them. For v1, a dangling row blocks until pull/prune. Keep simple.)
blockers = unpulled_blockers([(t, i) for t, i in pending_rows], manifest_ids)
if not body.force and blockers:
    detail = ", ".join(f"{t}:{i}" for t, i in blockers)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"{len(blockers)} entity(ies) were captured into this solution but are not "
            f"in your source manifest: {detail}. Run `bifrost solution pull`, then deploy."
        ),
    )
```

Confirm the real field shapes on `SolutionDeployRequest` (`body.tables` etc. — are they list-of-dict with `id`, or typed models?). Read the DTO (`api/src/models/contracts/...` for `SolutionDeployRequest`) and adapt the id-extraction to the actual shape. Match the manifest id keys to how `pending_captures.entity_id` was stored in Task 2 (string ids; config by key).

- [ ] **Step 6: Add an integration test for the endpoint guard**

```python
# api/tests/unit/test_pending_captures.py — endpoint-level (uses the test client + db)
@pytest.mark.asyncio
async def test_deploy_409s_on_unpulled_capture(async_client, captured_solution_fixture):
    sol, selectors = captured_solution_fixture
    from src.services.solutions.capture import SolutionCaptureService
    # capture (enqueues), then deploy with an EMPTY manifest (nothing pulled)
    # ... capture via service or POST /capture ...
    resp = await async_client.post(f"/api/solutions/{sol.id}/deploy", json={"force": False, "tables": [], "forms": [], "agents": [], "config_schemas": {}, "events": [], "claims": [], "workflows": [], "apps": [], "python_files": {}, "connection_schemas": []})
    assert resp.status_code == 409
    assert "bifrost solution pull" in resp.json()["detail"]
```

(Use the project's authenticated superuser test-client fixture — capture/deploy are `CurrentSuperuser`. Match the real `SolutionDeployRequest` JSON shape. If an empty deploy is rejected for other reasons first, capture then deploy with a manifest that omits ONLY the captured entity.)

- [ ] **Step 7: Restart API + run**

```bash
docker restart bifrost-test-75bc0d9c-api-1
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py -v
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/src/services/solutions/pending.py api/src/routers/solutions.py api/tests/unit/test_pending_captures.py
git commit -m "feat(solutions): deploy guard 409s on captured-but-unpulled entities"
```

---

## Task 4: Clear-queue endpoint (`POST /{id}/pull/ack`)

**Files:**
- Modify: `api/src/routers/solutions.py`
- Test: `api/tests/unit/test_pending_captures.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_pull_ack_clears_only_acked_rows(async_client, db_session, captured_solution_fixture):
    sol, selectors = captured_solution_fixture
    # enqueue table + form, then ack only the form
    # ... capture ...
    resp = await async_client.post(
        f"/api/solutions/{sol.id}/pull/ack",
        json={"entities": [{"entity_type": "form", "entity_id": str(selectors.form_id)}]},
    )
    assert resp.status_code == 200
    rows = (await db_session.execute(
        select(PendingCaptureORM).where(PendingCaptureORM.solution_id == sol.id)
    )).scalars().all()
    keyset = {(r.entity_type, r.entity_id) for r in rows}
    assert ("form", str(selectors.form_id)) not in keyset   # cleared
    assert ("table", str(selectors.table_id)) in keyset      # untouched
```

- [ ] **Step 2: Run to verify it fails** (404/route missing).

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py::test_pull_ack_clears_only_acked_rows -v`

- [ ] **Step 3: Add the endpoint**

```python
# api/src/routers/solutions.py
from sqlalchemy import delete, and_

class PullAckEntity(BaseModel):
    entity_type: str
    entity_id: str

class PullAckRequest(BaseModel):
    entities: list[PullAckEntity]

@router.post("/{solution_id}/pull/ack", summary="Clear pending_captures rows the client has pulled into source (admin only)")
async def ack_pulled_captures(solution_id: UUID, body: PullAckRequest, ctx: Context, user: CurrentSuperuser):
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=404, detail="Solution not found")
    for ent in body.entities:
        await ctx.db.execute(
            delete(PendingCaptureORM).where(and_(
                PendingCaptureORM.solution_id == solution_id,
                PendingCaptureORM.entity_type == ent.entity_type,
                PendingCaptureORM.entity_id == ent.entity_id,
            ))
        )
    await ctx.db.commit()
    return {"cleared": len(body.entities)}
```

Server-authoritative clear (client says what it materialized; server deletes exactly those rows). Match the `BaseModel`/router import style already in the file.

- [ ] **Step 4: Restart API + run to verify PASS.**

```bash
docker restart bifrost-test-75bc0d9c-api-1
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py::test_pull_ack_clears_only_acked_rows -v
```

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/solutions.py api/tests/unit/test_pending_captures.py
git commit -m "feat(solutions): POST /{id}/pull/ack clears pulled pending_captures rows"
```

---

## Task 5: `bifrost solution pull` CLI command

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: covered by the e2e in Task 6 (CLI command; unit-testing the HTTP-glue adds little — the e2e is the real check). Optionally a thin unit test that the command is registered + parses args.

- [ ] **Step 1: Add the `pull` command**

```python
# api/bifrost/commands/solution.py
@solution_group.command(name="pull", help="Pull captured entities into the local .bifrost/ manifest (does not touch source code).")
@click.argument("path", required=False, default=".")
@click.option("--solution-id", default=None, help="Install id (defaults to bifrost.solution.yaml).")
def pull_cmd(path: str, solution_id: str | None) -> None:
    import asyncio, io, zipfile
    from pathlib import Path
    workspace = Path(path).resolve()
    sid = solution_id or _read_solution_id(workspace)  # reuse the helper deploy uses to read bifrost.solution.yaml

    async def _run() -> int:
        async with _client() as client:   # match how other commands construct the API client
            # 1. fetch the live-rebuilt bundle (shareable mode = no secret values)
            resp = await client.get(f"/api/solutions/{sid}/export?mode=shareable")
            # export is POST in the router — match the real method/params; it returns application/zip
            zip_bytes = resp.content
            # 2. unzip ONLY .bifrost/*.yaml into the workspace (never apps/, functions/, source)
            materialized: list[dict] = []
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    if name.startswith(".bifrost/") and name.endswith((".yaml", ".yml")):
                        target = workspace / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(name))
            # 3. determine which entities we materialized (parse the .bifrost/*.yaml keys) and ack them
            materialized = _entities_in_manifest(workspace)   # [{entity_type, entity_id}, ...]
            if materialized:
                await client.post(f"/api/solutions/{sid}/pull/ack", json={"entities": materialized})
            click.echo(f"Pulled {len(materialized)} entity manifest(s) into {workspace}/.bifrost/")
        return 0

    raise SystemExit(asyncio.run(_run()))
```

CRITICAL adaptations the implementer must make against the real code:
- `/export` is `POST` in the router (`api/src/routers/solutions.py:206`) with a `mode` and possibly `password` body — match the real signature (read it). It may require a body even for shareable mode.
- Reuse the existing helpers: how `deploy_cmd` reads the solution id from `bifrost.solution.yaml` (`_read_solution_id` or inline), and how commands build the authed client (`_client()` / whatever pattern `capture_cmd`/`deploy_cmd` use). Do NOT invent new client plumbing.
- `_entities_in_manifest(workspace)`: parse `.bifrost/{tables,forms,agents,configs,events,claims}.yaml` — each is keyed by entity id (config by key) — and return `[{entity_type, entity_id}]` matching what Task 4's ack expects and what Task 2 enqueued. Keep the type strings identical across enqueue/guard/ack/parse (`table|form|agent|config|event|claim`).
- **Only write `.bifrost/`** — the zip also contains `apps/`/`functions/`/python source; the `name.startswith(".bifrost/")` filter is load-bearing. Do not extract anything else (never clobber the dev's working tree).

- [ ] **Step 2: Smoke-test the command parses + registers**

```bash
# from a scratch venv CLI install (see CLAUDE.md), against the debug stack:
./.venv/bin/bifrost solution pull --help
```
Expected: shows the help text (command registered). Full behavior is verified by the Task 6 e2e.

- [ ] **Step 3: Commit**

```bash
git add api/bifrost/commands/solution.py
git commit -m "feat(cli): bifrost solution pull — materialize captured entities into .bifrost/"
```

---

## Task 6: End-to-end round-trip test (the real proof)

**Files:**
- Create: `api/tests/e2e/platform/test_capture_roundtrip.py`

- [ ] **Step 1: Write the e2e**

The full arc, mirroring `api/tests/e2e/platform/test_git_sync_local.py` conventions (session-scoped fixtures, superuser client):

```python
# api/tests/e2e/platform/test_capture_roundtrip.py
# 1. create a solution install + scaffold/deploy a minimal workspace (workflow only)
# 2. create a loose same-org table + form + agent + config
# 3. POST /capture them  → assert pending_captures has 4 rows
# 4. POST /deploy with a manifest that OMITS the captured entities → assert 409 + "pull" in detail
#    AND assert the entities still EXIST (not deleted) — the block protected them
# 5. POST /export?mode=shareable → unzip .bifrost/ → assert it contains tables/forms/agents/configs yaml
#    POST /pull/ack for those entities → assert pending_captures now empty
# 6. POST /deploy with the manifest NOW including the captured entities → assert 200 + entities survive
# 7. genuine delete: POST /deploy with manifest OMITTING one entity (no pending row now) → assert it IS deleted
```

Write it as real assertions against the test client + DB (no pseudocode in the committed file — the comment above is the shape; implement each step concretely, reading `test_git_sync_local.py` for the deploy/capture request shapes and fixtures). Install the read-only guard in the test setup per the Conventions note.

- [ ] **Step 2: Restart API + run the e2e**

```bash
docker restart bifrost-test-75bc0d9c-api-1
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/e2e/platform/test_capture_roundtrip.py -v
```
Expected: PASS — capture→409→pull→deploy-succeeds→genuine-delete all verified.

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_capture_roundtrip.py
git commit -m "test(solutions): e2e capture→deploy-block→pull→deploy→genuine-delete round-trip"
```

---

## Task 7: Pre-completion verification + skill-doc reconcile

**Files:**
- Modify: `.claude/skills/bifrost-build/references/solutions.md` (replace the TBD open-question section with the now-real mechanism)

- [ ] **Step 1: Replace the solutions.md open-question with the real flow**

The skill's `solutions.md` currently marks the entities-into-a-solution mechanism as "TBD (capture vs manifest)". Now that it's real, replace that section with the working flow: capture (UI or CLI) → `bifrost solution pull` (brings them into `.bifrost/`) → `bifrost solution deploy`. Note that deploy 409-blocks until you pull. Keep it light + lint-clean.

- [ ] **Step 2: Lint solutions.md + re-sync the Codex mirror**

```bash
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner python -c "import sys; sys.path.insert(0,'/app/scripts/skill-truth'); import lint_claims as l; from pathlib import Path; print('FINDINGS', len(l.lint_paths([Path('/.claude/skills/bifrost-build/references/solutions.md')])))"
./scripts/sync-codex-skills.sh
```
Expected: FINDINGS 0; mirror synced. Bump that file's `verified_at_sha` in `references/sources.yaml` to the new HEAD.

- [ ] **Step 3: Full backend verification**

```bash
cd api && pyright && ruff check .
cd .. && docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_pending_captures.py tests/e2e/platform/test_capture_roundtrip.py -v
```
Expected: pyright 0 errors, ruff clean, tests green.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/bifrost-build/references/solutions.md .claude/skills/bifrost-build/references/sources.yaml plugins/bifrost/skills/bifrost-build/
git commit -m "docs(build-skill): solutions.md documents the real capture→pull→deploy round-trip"
```

---

## Task 8: Sonnet validation loop — Track A (solution build)

> **Orchestration, not TDD.** This is the centerpiece from the build-skill rebuild (its Tasks 11–12 are folded in here so this plan runs end-to-end). It runs ONLY after Tasks 1–7 above make the capture→pull→deploy round-trip work. Done bar: **3 consecutive clean runs with no skill-doc edits between them.** Each run's misleading-moment fix resets the streak to 0. Log: `docs/plans/2026-06-15-build-skill-validation-log.md` (run A1 already recorded as blocked-on-the-now-fixed bug).

**Prerequisite — debug stack in PORT mode** (Chrome can't drive netbird Vite):
```bash
./debug.sh status | grep -q "Mode:     port" || BIFROST_FORCE_PORT=1 ./debug.sh up
./debug.sh status   # capture the URL, e.g. http://localhost:37791
```

- [ ] **Step 1: Build the SDK-surface coverage checklist**

From `.claude/skills/bifrost-build/generated/python-sdk-signatures.md` (71 methods) and `generated/web-sdk-surface.md` (22 exports), enumerate every public Python SDK method + web export into a checklist in the validation log. The union of Track A + Track B must tick every box; gaps logged with a reason.

- [ ] **Step 2: Dispatch a fresh Sonnet run (skill-only guidance)**

Dispatch a `general-purpose` subagent on `model: sonnet`, in a clean scratch dir (`/tmp/bifrost-val-A<n>`), pointed at the port-mode stack. Instruct it to follow ONLY the `bifrost:build` skill (start at SKILL.md, no reading platform source) and build a complete solution from scratch: `bifrost solution init` → scaffold a **Tailwind-styled** app → get a **table + form + agent + config** into the solution via the **capture → `bifrost solution pull` → `bifrost solution deploy`** flow (now working) → `bifrost solution start` + drive every page → update an entity → `bifrost solution deploy`. It must exercise as much of the SDK surface as the build touches and report: a scorecard (styled? entities round-tripped? update? deploy clean? read-only invariant respected?), every misleading skill moment (quoting the file + text), and which SDK methods/exports it used. (Reuse the run-A1 dispatch prompt in this session's history as the template; it's thorough.)

- [ ] **Step 3: Score, log, fix, repeat**

Record the run in the Track A table of the validation log; tick the coverage checklist. For each misleading moment → fix the relevant `.claude/skills/bifrost-build/references/*.md` (or SKILL.md), re-lint (`lint_claims.py`, 0 findings), re-sync the Codex mirror (`./scripts/sync-codex-skills.sh`), bump the touched file's `verified_at_sha` in `sources.yaml`, and **reset the consecutive-clean counter to 0**. Apply the queued A1 skill-doc findings here (org-scoping-for-capture, `solution start [APP_SLUG]` positional, the capture→pull→deploy doc now that it's real — much of this is Task 7 above). Loop Steps 2–3 until **3 consecutive clean runs** (no doc edits between them).

- [ ] **Step 4: Commit**

```bash
git add docs/plans/2026-06-15-build-skill-validation-log.md .claude/skills/bifrost-build/ plugins/bifrost/skills/bifrost-build/
git commit -m "validate(build-skill): Track A (solution) — 3-clean streak + SDK coverage"
```

---

## Task 9: Sonnet validation loop — Track B (repo/global) + closeout

> Orchestration. Covers SDK surface Track A didn't reach, so the union drives the whole SDK. Same done bar (3 consecutive clean, no doc edits between).

- [ ] **Step 1: Dispatch a fresh Sonnet repo-mode run**

Dispatch a fresh `sonnet` subagent in a clean scratch dir, skill-only guidance, in a **non-solution** (global `_repo`) workspace (no `bifrost.solution.yaml` → the dispatcher routes it to `repo.md`): author a workflow `.py`, create entities via live `bifrost <entity> create|update` (correct in repo mode), execute the workflow, iterate. Target the coverage-checklist boxes Track A left unticked. If cheap, also exercise the MCP-only variant (repo-only concept, `mcp-mode.md`).

- [ ] **Step 2: Score, log, fix, loop to the bar**

Same scorecard + coverage ticks + doc-fix-resets-streak discipline as Task 8. Loop to **3 consecutive clean runs, no doc edits between**.

- [ ] **Step 3: Coverage closeout**

Confirm every box on the SDK-surface checklist is ticked by Track A ∪ Track B. Any still-unreached op is a logged gap with a reason (`log()` it — never silently drop). Bump `verified_at_sha` for every reference file whose claims were driven this session.

- [ ] **Step 4: Full pre-completion verification**

```bash
cd api && pyright && ruff check .
cd ../client && npm run tsc && npm run lint
cd .. && docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit -v
# skill-accuracy gates:
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner python /app/scripts/skill-truth/generate.py --check
./scripts/sync-codex-skills.sh && git diff --exit-code -- plugins/bifrost/skills .codex/skills
```
Expected: pyright 0, ruff/eslint/tsc clean, tests green, generate.py --check clean, mirror diff clean.

- [ ] **Step 5: Commit**

```bash
git add docs/plans/2026-06-15-build-skill-validation-log.md .claude/skills/bifrost-build/ plugins/bifrost/skills/bifrost-build/
git commit -m "validate(build-skill): Track B (repo) — 3-clean streak + full SDK coverage closeout"
```

This completes the entire arc: build-skill rebuild (done) → capture round-trip fix (Tasks 1–7) → empirical validation to a clean streak across both modes (Tasks 8–9). At this point the skill is proven, the platform round-trip works, and Tasks 11–12 of the build-skill rebuild plan are satisfied.

---

## Self-review notes (for the executor)

- **Multiple alembic heads exist** — Task 1 step 4 MUST run `alembic heads` and chain off the real current solutions head; do not hardcode.
- **DTO shapes are assumptions** — Task 3's `body.tables[].id` / `config_schemas` keying and Task 5's `/export` method/params must be verified against the real `SolutionDeployRequest` and `export_solution` signatures before coding; the plan flags each spot.
- **Entity-type strings are a contract** — `table|form|agent|config|event|claim` must be byte-identical across enqueue (Task 2), guard (Task 3), ack (Task 4), and manifest-parse (Task 5). A mismatch silently breaks the round-trip.
- **Core-not-ORM for solution-managed writes** + install the read-only guard in tests (Conventions) — else prod-faithful behavior diverges from green unit tests.
- **Restart the api container** after each deploy/capture-path change before running endpoint tests.
- Config capture's `solution_id`/`solution_config_schema` quirk (spec §3.5): verify in Task 2/6 that a captured config actually enqueues + round-trips; if configs need different handling, scope that explicitly rather than assuming parity with tables/forms.
