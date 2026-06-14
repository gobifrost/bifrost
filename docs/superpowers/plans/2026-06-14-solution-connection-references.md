# Solution Connection References & Unified Unmet-Needs Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Solutions declare, template, and surface the integrations + dependencies they need so they no longer install "dead" — auto-scanned connection references, safe integration template shells, install-time dependency blocking, a markdown README tab, and a guided Setup wizard.

**Architecture:** Mirror the existing config-declaration story (`SolutionConfigSchema` → new `SolutionConnectionSchema`; `_config_entries` → `_connection_entries`; `setup_status` extended to a unified engine). The dependency walker is overhauled to also run at install (`check_install`) for module/cross-solution blocking. Capture serializes a secret-scrubbed integration skeleton; deploy pre-creates an empty integration shell when absent. Setup becomes a launched wizard; OAuth status is warn-only.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic / Pydantic (backend); TypeScript / React / Vite / TipTap (client); pytest + vitest.

**Spec:** `docs/superpowers/specs/2026-06-14-solution-connection-references-design.md`

**Conventions (read before starting):**
- Tests run via `./test.sh` (never two concurrent in one worktree). Unit: `./test.sh tests/unit/<f>.py::<test> -v`. JUnit XML at `/tmp/bifrost-<project>/test-results.xml`.
- Datetime: `datetime.now(timezone.utc)` + `DateTime(timezone=True)`. ORM defaults use lambdas.
- After touching DTOs under `models/contracts/` or `dto_flags.py`: run `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py`; if the contract fingerprint changed, follow CLAUDE.md's contract-version bump procedure.
- After a migration: restart `bifrost-debug-<project>-init-1` then `-api-1` to apply (migrations don't hot-reload).
- After backend model/contract changes: `cd client && npm run generate:types` (dev stack up).
- Commit in logical batches with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

---

## File Structure

**Backend — create:**
- `api/src/models/orm/solution_connection_schema.py` — `SolutionConnectionSchema` ORM (declaration + template JSON).
- `api/alembic/versions/<rev>_solution_connection_refs.py` — migration: new table + `solutions.readme` column.

**Backend — modify:**
- `api/src/services/solutions/ref_scanner.py` — add `scan_integration_refs`.
- `api/src/services/solutions/dependency_walker.py` — scan integration refs; add `check_install` unmet-needs path.
- `api/src/services/solutions/capture.py` — `_connection_entries` + integration-template build; add to bundle.
- `api/bifrost/portable.py` — integration-template secret scrub.
- `api/src/services/solutions/deploy.py` — `_upsert_integration_shells`; write `Solution.readme`; call `check_install` block.
- `api/src/services/solutions/zip_install.py` — wire `check_install` block into install preview/apply.
- `api/src/services/solutions/setup_status.py` — unify config + connection into `SolutionSetupStatus`.
- `api/src/services/solutions/git_sync.py` — pull `README.md` repo→`Solution.readme`.
- `api/src/models/contracts/solutions.py` — `SolutionSetupItem.kind` + connection meta; bundle fields; README field.
- `api/src/models/orm/solutions.py` — `readme` column + `connection_schema` relationship.
- `api/bifrost/integrations.py` + `api/src/routers/cli.py` (`sdk_integrations_get`) — `RequiredConnectionUnset` runtime escalation for declared-but-missing integrations.
- `api/src/routers/solutions.py` — README get/set endpoint; setup endpoint returns unified status.

**Client — create:**
- `client/src/components/solutions/SolutionReadmeTab.tsx` (+ `.test.tsx`) — TipTap render/edit.
- `client/src/components/solutions/SolutionSetupWizard.tsx` (+ `.test.tsx`) — stepped wizard.

**Client — modify:**
- `client/src/components/solutions/SolutionSetupChecklist.tsx` — becomes wizard step bodies (config + connection items).
- `client/src/pages/SolutionDetail.tsx` — README first tab, "Setup Required" triangle + "Continue Setup" launch.
- `client/src/services/solutions.ts` — types/wrappers for new endpoints.

---

## Phase 1 — Connection scanning & declaration

### Task 1: `scan_integration_refs` static scanner

**Files:**
- Modify: `api/src/services/solutions/ref_scanner.py`
- Test: `api/tests/unit/test_solution_ref_scanner.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_ref_scanner.py
from src.services.solutions.ref_scanner import scan_integration_refs


def test_scan_integration_refs_matches_get_calls():
    src = '''
    a = await integrations.get("HaloPSA")
    b = await sdk.integrations.get('Microsoft Partner')
    c = await integrations.get(name)  # dynamic — invisible
    '''
    assert scan_integration_refs(src) == {"HaloPSA", "Microsoft Partner"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_ref_scanner.py::test_scan_integration_refs_matches_get_calls -v`
Expected: FAIL with `ImportError: cannot import name 'scan_integration_refs'`.

- [ ] **Step 3: Implement the scanner**

Add to `ref_scanner.py` (after `_WORKFLOW_RE`), and add `scan_integration_refs` to `__all__`:

```python
# ``integrations.get("Name")`` / ``sdk.integrations.get("Name")``.
# First arg is the integration NAME (a string literal). Dynamic refs are
# invisible — same documented static-scan tradeoff as configs/tables.
_INTEGRATION_RE = re.compile(
    rf"""\bintegrations\s*\.\s*get\s*\(\s*{_STR}"""
)


def scan_integration_refs(source: str) -> set[str]:
    """Return integration NAMES referenced via ``integrations.get(...)``."""
    return set(_INTEGRATION_RE.findall(source))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_ref_scanner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/ref_scanner.py api/tests/unit/test_solution_ref_scanner.py
git commit -m "feat(solutions): scan_integration_refs for connection declarations"
```

### Task 2: `SolutionConnectionSchema` ORM + migration

**Files:**
- Create: `api/src/models/orm/solution_connection_schema.py`
- Create: `api/alembic/versions/<rev>_solution_connection_refs.py`
- Modify: `api/src/models/orm/solutions.py` (relationship + `readme` column)
- Test: `api/tests/unit/test_solution_connection_schema_orm.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_connection_schema_orm.py
import pytest
from uuid import uuid4
from sqlalchemy import select
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solutions import Solution, SolutionScope


@pytest.mark.asyncio
async def test_connection_schema_round_trips(db_session):
    sol = Solution(id=uuid4(), slug="s", name="S", scope=SolutionScope.ORGANIZATION)
    db_session.add(sol)
    await db_session.flush()
    row = SolutionConnectionSchema(
        solution_id=sol.id,
        integration_name="HaloPSA",
        position=0,
        template={"name": "HaloPSA", "config_schema": [], "oauth": None},
    )
    db_session.add(row)
    await db_session.flush()
    got = (await db_session.execute(
        select(SolutionConnectionSchema).where(
            SolutionConnectionSchema.solution_id == sol.id
        )
    )).scalar_one()
    assert got.integration_name == "HaloPSA"
    assert got.template["name"] == "HaloPSA"


@pytest.mark.asyncio
async def test_solution_readme_column(db_session):
    sol = Solution(id=uuid4(), slug="s2", name="S2",
                   scope=SolutionScope.ORGANIZATION, readme="# Hello")
    db_session.add(sol)
    await db_session.flush()
    assert sol.readme == "# Hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_connection_schema_orm.py -v`
Expected: FAIL (`ModuleNotFoundError` for the new ORM, or `readme` attribute error).

- [ ] **Step 3: Create the ORM**

```python
# api/src/models/orm/solution_connection_schema.py
"""SolutionConnectionSchema: a Solution-owned integration (connection) DECLARATION.

A Solution declares the integrations its code references (``integrations.get("X")``)
plus a secret-scrubbed TEMPLATE skeleton (config schema, OAuth provider shape, data
provider) so an install can pre-create an empty integration shell to fill in. Like
``SolutionConfigSchema`` it is portable and carries NO secrets by design.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionConnectionSchema(Base):
    __tablename__ = "solution_connection_schema"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False
    )
    integration_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Secret-scrubbed skeleton: {name, entity_id_name?, default_entity_id?,
    # data_provider_name?, config_schema: [...], oauth: {...} | None}.
    template: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_solution_connection_schema_solution_id", "solution_id"),
        Index(
            "ix_solution_connection_schema_sol_name_unique",
            "solution_id",
            "integration_name",
            unique=True,
        ),
    )
```

- [ ] **Step 4: Add `readme` column + relationship to `solutions.py`**

In `api/src/models/orm/solutions.py`, add after the `logo_content_type` column:

```python
    readme: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
```

(ensure `Text` is imported from sqlalchemy). After the existing relationships, add:

```python
    connection_schema: Mapped[list["SolutionConnectionSchema"]] = relationship(
        "SolutionConnectionSchema",
        cascade="all, delete-orphan",
        order_by="SolutionConnectionSchema.position",
        lazy="selectin",
    )
```

Add the import at the bottom (matching how `SolutionConfigSchema` is referenced) or use the string form already used in the file.

- [ ] **Step 5: Generate the migration**

Run: `cd api && alembic revision -m "solution connection refs"`
Edit the new file's `upgrade()`/`downgrade()`:

```python
def upgrade() -> None:
    op.create_table(
        "solution_connection_schema",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("integration_name", sa.String(length=255), nullable=False),
        sa.Column("template", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default="{}"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_solution_connection_schema_solution_id",
                    "solution_connection_schema", ["solution_id"])
    op.create_index("ix_solution_connection_schema_sol_name_unique",
                    "solution_connection_schema",
                    ["solution_id", "integration_name"], unique=True)
    op.add_column("solutions", sa.Column("readme", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("solutions", "readme")
    op.drop_index("ix_solution_connection_schema_sol_name_unique",
                  table_name="solution_connection_schema")
    op.drop_index("ix_solution_connection_schema_solution_id",
                  table_name="solution_connection_schema")
    op.drop_table("solution_connection_schema")
```

Ensure `from alembic import op`, `import sqlalchemy as sa`, `from sqlalchemy.dialects import postgresql` are present.

- [ ] **Step 6: Apply migration to the test stack & run tests**

The test stack runs alembic on stack-up/reset. Run:
`./test.sh tests/unit/test_solution_connection_schema_orm.py -v`
Expected: PASS. (If the table is missing, `./test.sh stack reset` to re-run migrations, then re-run.)

- [ ] **Step 7: Commit**

```bash
git add api/src/models/orm/solution_connection_schema.py api/src/models/orm/solutions.py api/alembic/versions/ api/tests/unit/test_solution_connection_schema_orm.py
git commit -m "feat(solutions): SolutionConnectionSchema ORM + readme column + migration"
```

### Task 3: Integration template builder + secret scrub

**Files:**
- Modify: `api/bifrost/portable.py`
- Create: `api/src/services/solutions/integration_template.py` (build skeleton from an `Integration`)
- Test: `api/tests/unit/test_integration_template.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_integration_template.py
from src.services.solutions.integration_template import build_integration_template


class _Prov:
    provider_name = "halo"; display_name = "HaloPSA"; oauth_flow_type = "authorization_code"
    authorization_url = "https://auth"; token_url = "https://token"; audience = None
    token_url_defaults = {}; entity_id_source = None; scopes = ["all"]; redirect_uri = None
    client_id = "SECRET-CLIENT"; encrypted_client_secret = b"SECRET"


class _Schema:
    key = "url"; type = "string"; required = True; description = None
    options = None; position = 0


class _Integration:
    name = "HaloPSA"; entity_id_name = "tenant"; default_entity_id = None
    list_entities_data_provider_id = None
    config_schema = [_Schema()]; oauth_provider = _Prov()


def test_template_carries_safe_fields_and_scrubs_secrets():
    t = build_integration_template(_Integration())
    assert t["name"] == "HaloPSA"
    assert t["config_schema"][0]["key"] == "url"
    assert t["oauth"]["authorization_url"] == "https://auth"
    assert t["oauth"]["scopes"] == ["all"]
    # No secret survives anywhere in the serialized template.
    blob = repr(t)
    assert "SECRET-CLIENT" not in blob and "SECRET" not in blob
    assert "client_id" not in t["oauth"]
    assert "client_secret" not in t["oauth"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_integration_template.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the builder**

```python
# api/src/services/solutions/integration_template.py
"""Build a secret-scrubbed, portable skeleton of an Integration for a Solution
connection declaration. Carries the fill-out shape (config schema, OAuth provider
shape, data provider) but NEVER client_id/client_secret/tokens/mappings/org ids.
"""
from __future__ import annotations

from typing import Any

# Safe OAuthProvider fields to carry. Everything not listed is dropped — in
# particular client_id, encrypted_client_secret, organization_id, status*,
# tokens, last_token_refresh.
_SAFE_OAUTH_FIELDS = (
    "provider_name", "display_name", "oauth_flow_type", "authorization_url",
    "token_url", "audience", "token_url_defaults", "entity_id_source",
    "scopes", "redirect_uri",
)


def build_integration_template(integration: Any) -> dict[str, Any]:
    config_schema = [
        {
            "key": s.key, "type": s.type, "required": bool(s.required),
            "description": s.description, "options": s.options,
            "position": s.position,
        }
        for s in (integration.config_schema or [])
    ]
    oauth = None
    prov = getattr(integration, "oauth_provider", None)
    if prov is not None:
        oauth = {f: getattr(prov, f, None) for f in _SAFE_OAUTH_FIELDS}
    return {
        "name": integration.name,
        "entity_id_name": getattr(integration, "entity_id_name", None),
        "default_entity_id": getattr(integration, "default_entity_id", None),
        "data_provider_id": (
            str(integration.list_entities_data_provider_id)
            if getattr(integration, "list_entities_data_provider_id", None)
            else None
        ),
        "config_schema": config_schema,
        "oauth": oauth,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_integration_template.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/integration_template.py api/tests/unit/test_integration_template.py
git commit -m "feat(solutions): build_integration_template (secret-scrubbed skeleton)"
```

### Task 4: Walker scans integrations; capture builds declarations + templates

**Files:**
- Modify: `api/src/services/solutions/dependency_walker.py` (collect integration refs)
- Modify: `api/src/services/solutions/capture.py` (`_connection_entries`, add to bundle)
- Modify: `api/src/models/contracts/solutions.py` (bundle/preview `connection_declarations`)
- Test: `api/tests/unit/test_solution_capture_connections.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_capture_connections.py
import pytest
from src.services.solutions.capture import SolutionCaptureService
# (Use existing capture test fixtures as a model — see test_solution_capture.py.)


@pytest.mark.asyncio
async def test_capture_declares_referenced_integration(capture_fixture):
    """A solution whose workflow calls integrations.get("HaloPSA") and a global
    Integration 'HaloPSA' exists -> a SolutionConnectionSchema row is created
    with a scrubbed template."""
    svc, solution, db = capture_fixture  # fixture seeds wf source + global integration
    entries = await svc._connection_entries(solution.id)
    names = {e["integration_name"] for e in entries}
    assert "HaloPSA" in names
    halo = next(e for e in entries if e["integration_name"] == "HaloPSA")
    assert halo["template"]["name"] == "HaloPSA"
    assert "client_id" not in (halo["template"].get("oauth") or {})
```

> NOTE TO IMPLEMENTER: model `capture_fixture` on the existing capture-test
> setup in `api/tests/unit/test_solution_capture.py` (seed a Solution-owned
> workflow whose source contains `integrations.get("HaloPSA")`, and a global
> `Integration(name="HaloPSA")` with an `oauth_provider`). Reuse its session
> fixtures; do not invent new infra.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_capture_connections.py -v`
Expected: FAIL (`AttributeError: _connection_entries`).

- [ ] **Step 3: Add integration scanning to the walker**

In `dependency_walker.py`, import `scan_integration_refs` from `ref_scanner`. In the worklist drain loop (after the `scan_config_refs` block, ~line 177), add:

```python
            for iname in scan_integration_refs(src):
                _add_pulled("integration", iname, iname, False)
```

Add `"integration"` to the accepted `kind` literals where `DependencyRef.kind` is typed (contracts), and ensure the preview surfaces them (they ride `pulled_in`).

- [ ] **Step 4: Implement `_connection_entries` in capture**

```python
    async def _connection_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        """Scan the install's workflow sources for integrations.get("X") refs,
        resolve each to a global Integration, and build a scrubbed declaration
        + template. Persist SolutionConnectionSchema rows (upsert by name)."""
        from src.models.orm.integrations import Integration
        from src.models.orm.solution_connection_schema import SolutionConnectionSchema
        from src.services.solutions.integration_template import build_integration_template
        from src.services.solutions.ref_scanner import scan_integration_refs

        # Gather referenced names from this install's workflow source.
        wfs = (await self.db.execute(
            select(Workflow).where(Workflow.solution_id == solution_id)
        )).scalars().all()
        names: set[str] = set()
        for wf in wfs:
            if not wf.path:
                continue
            try:
                src = (await self.repo.read(wf.path)).decode("utf-8")
            except Exception:
                continue
            names |= scan_integration_refs(src)

        entries: list[dict[str, Any]] = []
        for pos, name in enumerate(sorted(names)):
            integ = (await self.db.execute(
                select(Integration).where(Integration.name == name)
            )).scalar_one_or_none()
            if integ is None:
                # Referenced but not configured on this instance — still declare
                # it (template is a bare name shell) so install surfaces it.
                template = {"name": name, "config_schema": [], "oauth": None}
            else:
                template = build_integration_template(integ)
            entries.append({"integration_name": name, "template": template, "position": pos})
            # Upsert SolutionConnectionSchema by (solution_id, integration_name).
            existing = (await self.db.execute(
                select(SolutionConnectionSchema).where(
                    SolutionConnectionSchema.solution_id == solution_id,
                    SolutionConnectionSchema.integration_name == name,
                )
            )).scalar_one_or_none()
            if existing is None:
                self.db.add(SolutionConnectionSchema(
                    solution_id=solution_id, integration_name=name,
                    template=template, position=pos,
                ))
            else:
                existing.template = template
                existing.position = pos
        return entries
```

Wire it into `bundle_for`: add `connection_schemas = await self._connection_entries(solution.id)` next to `config_schemas`, and pass `connection_schemas=connection_schemas` to `SolutionBundle(...)`.

- [ ] **Step 5: Add bundle/contract fields**

In `api/src/models/contracts/solutions.py`, add to `SolutionBundle` (the class with `config_schemas`), to `SolutionDeployRequest`, and to `SolutionInstallPreview`:

```python
    # Each: {integration_name, template, position}. Secret-scrubbed skeletons
    # (no client_id/secret/tokens). Declared from integrations.get("X") refs.
    connection_schemas: list[dict[str, Any]] = Field(default_factory=list)
```

Find `SolutionBundle`'s real definition (grep `class SolutionBundle`) and add the field there too. Add `"integration"` to the `DependencyRef.kind` Literal if one exists.

- [ ] **Step 6: Run tests (incl. contract tripwire)**

Run: `./test.sh tests/unit/test_solution_capture_connections.py tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`
Expected: capture test PASS. If contract fingerprint changed: refresh per CLAUDE.md (additive → refresh fingerprint only).

- [ ] **Step 7: Commit**

```bash
git add api/src/services/solutions/dependency_walker.py api/src/services/solutions/capture.py api/src/models/contracts/solutions.py api/tests/unit/test_solution_capture_connections.py
git commit -m "feat(solutions): capture connection declarations + integration templates"
```

---

## Phase 2 — Deploy: integration shells + README write

### Task 5: `_upsert_integration_shells` (create-if-absent, never clobber)

**Files:**
- Modify: `api/src/services/solutions/deploy.py`
- Test: `api/tests/unit/test_solution_deploy_shells.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_deploy_shells.py
import pytest
from sqlalchemy import select
from src.models.orm.integrations import Integration
from src.services.solutions.deploy import SolutionDeployer


@pytest.mark.asyncio
async def test_creates_shell_when_absent(db_session):
    dep = SolutionDeployer(db_session)
    decls = [{"integration_name": "NewInteg", "template": {
        "name": "NewInteg", "config_schema": [{"key": "url", "type": "string",
        "required": True, "description": None, "options": None, "position": 0}],
        "oauth": {"provider_name": "p", "display_name": "P",
                  "oauth_flow_type": "authorization_code",
                  "authorization_url": "https://a", "token_url": "https://t",
                  "audience": None, "token_url_defaults": {},
                  "entity_id_source": None, "scopes": [], "redirect_uri": None}}}]
    created = await dep._upsert_integration_shells(decls)
    assert created == 1
    integ = (await db_session.execute(
        select(Integration).where(Integration.name == "NewInteg")
    )).scalar_one()
    assert integ.oauth_provider is not None
    assert integ.oauth_provider.client_id == ""  # empty shell, no secret


@pytest.mark.asyncio
async def test_noop_when_integration_exists(db_session):
    db_session.add(Integration(name="Existing"))
    await db_session.flush()
    dep = SolutionDeployer(db_session)
    created = await dep._upsert_integration_shells(
        [{"integration_name": "Existing", "template": {"name": "Existing",
          "config_schema": [], "oauth": None}}]
    )
    assert created == 0  # never clobber
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_deploy_shells.py -v`
Expected: FAIL (`AttributeError: _upsert_integration_shells`).

- [ ] **Step 3: Implement the upsert**

Add to `SolutionDeployer` in `deploy.py`:

```python
    async def _upsert_integration_shells(
        self, connection_schemas: list[dict[str, Any]]
    ) -> int:
        """Create an EMPTY integration (+ config schema + OAuth skeleton) for any
        declared integration that doesn't already exist globally. Never touches
        an existing integration. Returns the count created."""
        from src.models.orm.integrations import Integration, IntegrationConfigSchema
        from src.models.orm.oauth import OAuthProvider

        created = 0
        for decl in connection_schemas:
            name = decl["integration_name"]
            template = decl.get("template") or {}
            exists = (await self.db.execute(
                select(Integration).where(Integration.name == name)
            )).scalar_one_or_none()
            if exists is not None:
                continue  # never clobber a configured integration
            integ = Integration(
                name=name,
                entity_id_name=template.get("entity_id_name"),
                default_entity_id=template.get("default_entity_id"),
            )
            self.db.add(integ)
            await self.db.flush()
            for s in template.get("config_schema") or []:
                self.db.add(IntegrationConfigSchema(
                    integration_id=integ.id, key=s["key"], type=s["type"],
                    required=bool(s.get("required")),
                    description=s.get("description"), options=s.get("options"),
                    position=s.get("position", 0),
                ))
            oauth = template.get("oauth")
            if oauth:
                self.db.add(OAuthProvider(
                    integration_id=integ.id,
                    provider_name=oauth.get("provider_name") or name,
                    display_name=oauth.get("display_name"),
                    oauth_flow_type=oauth.get("oauth_flow_type") or "authorization_code",
                    client_id="",                      # empty shell — admin fills
                    encrypted_client_secret=b"",       # empty shell — admin fills
                    authorization_url=oauth.get("authorization_url"),
                    token_url=oauth.get("token_url"),
                    audience=oauth.get("audience"),
                    token_url_defaults=oauth.get("token_url_defaults") or {},
                    entity_id_source=oauth.get("entity_id_source"),
                    scopes=oauth.get("scopes") or [],
                    redirect_uri=oauth.get("redirect_uri"),
                    status="not_connected",
                ))
            created += 1
        return created
```

Call it inside `deploy(...)` after entity upserts: `await self._upsert_integration_shells(bundle.connection_schemas)`. Add `integrations_shell_created` to `DeployResult`/`SolutionDeployResponse` and return the count.

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_deploy_shells.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/deploy.py api/src/models/contracts/solutions.py api/tests/unit/test_solution_deploy_shells.py
git commit -m "feat(solutions): pre-create empty integration shells on deploy"
```

### Task 6: README repo→DB on deploy/git-sync + endpoint

**Files:**
- Modify: `api/src/services/solutions/deploy.py` (write `Solution.readme` from bundle)
- Modify: `api/src/services/solutions/git_sync.py` (read `README.md` repo→bundle/solution)
- Modify: `api/src/models/contracts/solutions.py` (`readme` on bundle + deploy request)
- Modify: `api/src/routers/solutions.py` (GET/PUT readme)
- Test: `api/tests/unit/test_solution_readme.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_readme.py
import pytest
from uuid import uuid4
from src.models.orm.solutions import Solution, SolutionScope
from src.services.solutions.deploy import SolutionDeployer
from src.models.contracts.solutions import SolutionDeployRequest


@pytest.mark.asyncio
async def test_deploy_writes_readme(db_session):
    sol = Solution(id=uuid4(), slug="r", name="R", scope=SolutionScope.ORGANIZATION)
    db_session.add(sol)
    await db_session.flush()
    dep = SolutionDeployer(db_session)
    bundle = SolutionDeployRequest(readme="# Setup\nDo the thing.")
    await dep._apply_readme(sol, bundle)
    assert sol.readme == "# Setup\nDo the thing."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_readme.py -v`
Expected: FAIL (`AttributeError: _apply_readme` / missing `readme` field).

- [ ] **Step 3: Add `readme` to contracts + `_apply_readme` + git-sync read**

Add `readme: str | None = None` to `SolutionDeployRequest`, `SolutionBundle`, `SolutionInstallPreview`. In `deploy.py`:

```python
    async def _apply_readme(self, solution, bundle) -> None:
        """README is repo-sourced and full-replaces (absent => cleared)."""
        solution.readme = getattr(bundle, "readme", None)
```

Call `await self._apply_readme(solution, bundle)` in `deploy(...)`. In `git_sync.py`, where repo files are read for a solution, read `README.md` at repo root (if present, UTF-8) and set it on the bundle/solution; absent → None.

- [ ] **Step 4: Add the README endpoint**

In `api/src/routers/solutions.py`:

```python
@router.get("/solutions/{solution_id}/readme")
async def get_solution_readme(solution_id: UUID, ...) -> dict:
    sol = await _load_solution(solution_id, ...)
    return {"readme": sol.readme}


@router.put("/solutions/{solution_id}/readme")
async def put_solution_readme(solution_id: UUID, body: dict, ...) -> dict:
    sol = await _load_solution(solution_id, ...)
    sol.readme = body.get("readme")
    await db.commit()
    return {"readme": sol.readme}
```

(Match the file's existing auth-dep + `_load_solution` patterns; do not invent new helpers.)

- [ ] **Step 5: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_readme.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solutions/deploy.py api/src/services/solutions/git_sync.py api/src/models/contracts/solutions.py api/src/routers/solutions.py api/tests/unit/test_solution_readme.py
git commit -m "feat(solutions): README round-trips repo->DB + get/put endpoint"
```

---

## Phase 3 — Install-time dependency blocking

### Task 7: `check_install` unmet-needs (block on missing module / cross-solution dep)

**Files:**
- Modify: `api/src/services/solutions/dependency_walker.py` (add `check_install`)
- Modify: `api/src/models/contracts/solutions.py` (`UnmetNeed`/`UnmetNeeds` models)
- Test: `api/tests/unit/test_solution_check_install.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_check_install.py
from src.services.solutions.dependency_walker import check_install_needs


def test_blocks_on_missing_module():
    # bundle workflow imports modules.helpers but no modules/helpers.py present.
    python_files = {"workflows/w.py": "from modules.helpers import x\n"}
    needs = check_install_needs(python_files)
    assert any(n.kind == "module" and "helpers" in n.ref for n in needs)


def test_passes_when_module_present():
    python_files = {
        "workflows/w.py": "from modules.helpers import x\n",
        "modules/helpers.py": "x = 1\n",
    }
    needs = check_install_needs(python_files)
    assert not [n for n in needs if n.kind == "module"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_check_install.py -v`
Expected: FAIL (`ImportError: check_install_needs`).

- [ ] **Step 3: Implement `check_install_needs`**

Add a module-level function in `dependency_walker.py` (pure, no DB — operates on the bundle's `python_files`), using the existing `scan_imported_modules`:

```python
from bifrost.solution_vendoring import scan_imported_modules
from src.models.contracts.solutions import UnmetNeed


def check_install_needs(python_files: dict[str, str]) -> list[UnmetNeed]:
    """Module-closure check over a bundle's python_files. Every `modules.x`
    import must resolve to a file present in the bundle. Returns the unmet
    needs (empty => satisfied). Cross-solution deps are added by the DB-aware
    caller; this pure core covers the module class."""
    present = set(python_files.keys())
    needs: list[UnmetNeed] = []
    seen: set[str] = set()
    for path, src in python_files.items():
        for module in scan_imported_modules(src):
            if module.split(".")[0] != "modules" or module in seen:
                continue
            seen.add(module)
            base = module.replace(".", "/")
            if f"{base}.py" not in present and f"{base}/__init__.py" not in present:
                needs.append(UnmetNeed(
                    kind="module", ref=module,
                    detail=f"imported by {path} but not present in the bundle",
                ))
    return needs
```

Add to `contracts/solutions.py`:

```python
class UnmetNeed(BaseModel):
    kind: str  # "module" | "solution_dep"
    ref: str
    detail: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_check_install.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/dependency_walker.py api/src/models/contracts/solutions.py api/tests/unit/test_solution_check_install.py
git commit -m "feat(solutions): check_install_needs module-closure blocking core"
```

### Task 8: Wire `check_install` into install/deploy (hard block)

**Files:**
- Modify: `api/src/services/solutions/zip_install.py`
- Modify: `api/src/services/solutions/deploy.py`
- Test: `api/tests/e2e/platform/test_solution_install_blocking.py`

- [ ] **Step 1: Write the failing e2e test**

```python
# api/tests/e2e/platform/test_solution_install_blocking.py
import pytest


@pytest.mark.asyncio
async def test_install_blocks_on_missing_module(client, ...):
    """Installing a bundle whose workflow imports a missing module returns a
    clear error and lands nothing."""
    # Build a minimal zip whose workflows/w.py does `from modules.absent import x`
    # but includes no modules/absent.py. POST to the install endpoint.
    resp = await client.post("/api/solutions/install", files=...)
    assert resp.status_code == 422
    assert "modules.absent" in resp.text
```

> NOTE: model the zip-build + install POST on the existing
> `api/tests/e2e/platform/test_solution_import_data.py` install tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh e2e` (then inspect `/tmp/bifrost-<project>/test-results.xml` for this test).
Expected: FAIL (install currently succeeds).

- [ ] **Step 3: Wire the block**

In `zip_install.py` (and `deploy.py`'s entrypoint), before applying the bundle:

```python
from src.services.solutions.dependency_walker import check_install_needs

needs = check_install_needs(bundle.python_files)
# cross-solution deps appended by the DB-aware resolver here (see Task 9 note)
if needs:
    raise HTTPException(
        status_code=422,
        detail="Solution has unmet dependencies: " +
               ", ".join(f"{n.kind}:{n.ref}" for n in needs),
    )
```

Place the check BEFORE any deploy/commit so nothing lands (mirror the secrets/collision "lands nothing" discipline).

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh e2e`; confirm the test passes in the XML.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/zip_install.py api/src/services/solutions/deploy.py api/tests/e2e/platform/test_solution_install_blocking.py
git commit -m "feat(solutions): block install on missing module dependency"
```

---

## Phase 4 — Unified setup status + runtime escalation

### Task 9: Unify config + connection into `SolutionSetupStatus`

**Files:**
- Modify: `api/src/services/solutions/setup_status.py`
- Modify: `api/src/models/contracts/solutions.py` (`SolutionSetupItem.kind` + connection meta)
- Test: `api/tests/unit/test_solution_setup_status.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_setup_status.py
import pytest
from uuid import uuid4
from src.models.orm.solutions import Solution, SolutionScope
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.integrations import Integration
from src.services.solutions.setup_status import compute_setup_status


@pytest.mark.asyncio
async def test_connection_item_satisfied_when_integration_exists(db_session):
    sol = Solution(id=uuid4(), slug="s", name="S", scope=SolutionScope.ORGANIZATION)
    db_session.add(sol)
    db_session.add(Integration(name="HaloPSA"))
    await db_session.flush()
    db_session.add(SolutionConnectionSchema(
        solution_id=sol.id, integration_name="HaloPSA", position=0,
        template={"name": "HaloPSA", "config_schema": [], "oauth": {"provider_name": "p"}},
    ))
    await db_session.flush()
    status = await compute_setup_status(db_session, sol)
    conn = [i for i in status.items if i.kind == "connection"]
    assert len(conn) == 1
    assert conn[0].is_set is True          # integration exists -> satisfied
    assert conn[0].has_oauth is True       # template carried oauth -> warn flag
    assert status.setup_complete is True   # no required configs, connection exists


@pytest.mark.asyncio
async def test_connection_item_unmet_when_integration_absent(db_session):
    sol = Solution(id=uuid4(), slug="s2", name="S2", scope=SolutionScope.ORGANIZATION)
    db_session.add(sol)
    await db_session.flush()
    db_session.add(SolutionConnectionSchema(
        solution_id=sol.id, integration_name="Ghost", position=0,
        template={"name": "Ghost", "config_schema": [], "oauth": None},
    ))
    await db_session.flush()
    status = await compute_setup_status(db_session, sol)
    conn = [i for i in status.items if i.kind == "connection"][0]
    assert conn.is_set is False
    assert status.setup_complete is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_setup_status.py -v`
Expected: FAIL (`kind`/`has_oauth` attributes missing; connections not computed).

- [ ] **Step 3: Extend contracts**

In `contracts/solutions.py`, extend `SolutionSetupItem`:

```python
class SolutionSetupItem(BaseModel):
    key: str                 # config key OR integration name
    type: str                # config type, or "integration" for connections
    required: bool
    is_set: bool             # config: value present; connection: integration exists
    description: str | None = None
    default: str | None = None
    kind: str = "config"     # "config" | "connection"
    # Connection-only meta (None for config items):
    has_oauth: bool = False  # template carried an OAuth provider shape (warn-only)
    connected: bool = False  # informational: a token/mapping resolves
```

- [ ] **Step 4: Extend `compute_setup_status`**

Append connection items after configs. Connection `is_set` = a global `Integration` with that name exists; `has_oauth` = `template.get("oauth")` truthy; `connected` = best-effort mapping/token lookup (informational — wrap in try/except, default False). `setup_complete` = `all(required configs set) and all(connection.is_set)`:

```python
    # ... existing config items computation produces `items` ...
    conn_decls = (await db.execute(
        select(SolutionConnectionSchema)
        .where(SolutionConnectionSchema.solution_id == solution.id)
        .order_by(SolutionConnectionSchema.position)
    )).scalars().all()
    if conn_decls:
        names = [d.integration_name for d in conn_decls]
        existing = set((await db.execute(
            select(Integration.name).where(Integration.name.in_(names))
        )).scalars().all())
        for d in conn_decls:
            items.append(SolutionSetupItem(
                key=d.integration_name, type="integration", required=True,
                is_set=d.integration_name in existing,
                description=None, kind="connection",
                has_oauth=bool((d.template or {}).get("oauth")),
                connected=False,  # informational; refine later if cheap
            ))
    complete = (
        all(i.is_set for i in items if i.kind == "config" and i.required)
        and all(i.is_set for i in items if i.kind == "connection")
    )
    return SolutionSetupStatus(setup_complete=complete, items=items)
```

(Set `kind="config"` explicitly on the existing config `SolutionSetupItem(...)` construction.)

- [ ] **Step 5: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_setup_status.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solutions/setup_status.py api/src/models/contracts/solutions.py api/tests/unit/test_solution_setup_status.py
git commit -m "feat(solutions): unified config+connection setup status"
```

### Task 10: `RequiredConnectionUnset` runtime escalation

**Files:**
- Modify: `api/src/routers/cli.py` (`sdk_integrations_get`)
- Modify: `api/bifrost/integrations.py` (raise on the SDK side if the API signals it) OR raise server-side
- Test: `api/tests/e2e/platform/test_solution_connection_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/e2e/platform/test_solution_connection_runtime.py
import pytest


@pytest.mark.asyncio
async def test_declared_missing_integration_raises(client, ...):
    """A solution declares integration 'Ghost' (no global Integration exists).
    A workflow in that solution calling integrations.get('Ghost') gets a loud
    error naming the integration, not a silent None."""
    # Seed a solution + SolutionConnectionSchema 'Ghost', run a workflow that
    # calls integrations.get('Ghost') with the solution context.
    resp = await client.post("/api/sdk/integrations/get",
                             json={"name": "Ghost", "scope": ...,
                                   "solution": str(solution_id)})
    assert resp.status_code == 424  # or chosen "dependency unmet" code
    assert "Ghost" in resp.text
```

> NOTE: model solution-context propagation on the F2 fix (ctx.solution_id /
> `?solution=`) referenced in memory `project_solutions_implementation`. Use the
> same `solution` field the table/config SDK resolver already accepts.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh e2e`; inspect XML.
Expected: FAIL (currently returns 200 + null).

- [ ] **Step 3: Implement server-side escalation**

In `sdk_integrations_get` (cli.py), when the integration is not found AND the request carries a `solution` id whose `SolutionConnectionSchema` declares that name, return a 424 (or the project's chosen "unmet dependency" status) with a body naming the integration and the remedy ("set it up in Integrations"). Non-declared lookups keep returning `None` (200) as today.

```python
    integ = ... # existing lookup
    if integ is None:
        sol_id = request_data.get("solution")
        if sol_id and await _connection_is_declared(db, sol_id, name):
            raise HTTPException(
                status_code=424,
                detail=f"Required integration '{name}' is not set up. "
                       f"Set it up in Integrations.",
            )
        return None  # unchanged loose behavior
```

Add `_connection_is_declared(db, solution_id, name)` querying `SolutionConnectionSchema`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh e2e`; confirm PASS in XML.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/cli.py api/bifrost/integrations.py api/tests/e2e/platform/test_solution_connection_runtime.py
git commit -m "feat(solutions): RequiredConnectionUnset runtime escalation for declared integrations"
```

---

## Phase 5 — Client: README tab + Setup wizard

### Task 11: Regenerate types + service wrappers

**Files:**
- Modify: `client/src/services/solutions.ts`
- Run: `cd client && npm run generate:types` (dev stack up)

- [ ] **Step 1: Regenerate types**

Run: `cd client && npm run generate:types` (set `OPENAPI_URL` to the worktree dev URL from `./debug.sh status` if non-default). Confirm `SolutionSetupItem` now has `kind`/`has_oauth`/`connected` in `client/src/lib/v1.d.ts`.

- [ ] **Step 2: Add service wrappers + types**

In `client/src/services/solutions.ts`, re-export `SolutionSetupItem` (now with new fields) and add `getSolutionReadme(id)` / `putSolutionReadme(id, readme)` wrappers matching the new endpoints.

- [ ] **Step 3: Type-check & commit**

Run: `cd client && npm run tsc`
Expected: PASS.

```bash
git add client/src/lib/v1.d.ts client/src/services/solutions.ts
git commit -m "chore(client): regen types + readme service wrappers for connection refs"
```

### Task 12: Setup wizard (configs → connections, OAuth warn-only)

**Files:**
- Modify: `client/src/components/solutions/SolutionSetupChecklist.tsx` (connection item rendering)
- Create: `client/src/components/solutions/SolutionSetupWizard.tsx`
- Test: `client/src/components/solutions/SolutionSetupWizard.test.tsx`

- [ ] **Step 1: Write the failing vitest**

```tsx
// client/src/components/solutions/SolutionSetupWizard.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SolutionSetupWizard } from "./SolutionSetupWizard";

const items = [
  { key: "API_URL", type: "string", required: true, is_set: false, kind: "config" },
  { key: "HaloPSA", type: "integration", required: true, is_set: false,
    kind: "connection", has_oauth: true, connected: false },
];

describe("SolutionSetupWizard", () => {
  it("warns about OAuth on a connection but does not gate completion on it", () => {
    render(<SolutionSetupWizard items={items as any} setupComplete={false}
              onSetConfig={vi.fn()} integrationHref={() => "/integrations/x"} />);
    // configs step first
    expect(screen.getByText("API_URL")).toBeInTheDocument();
    // connection warning is present and labeled warn-only
    expect(screen.getByText(/uses OAuth/i)).toBeInTheDocument();
    // "Set up integration" deep-link present for the connection
    expect(screen.getByRole("link", { name: /set up integration/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh client unit` (or `cd client && npx vitest run SolutionSetupWizard`)
Expected: FAIL (component doesn't exist).

- [ ] **Step 3: Implement the wizard**

Create `SolutionSetupWizard.tsx` — a two-step flow. Step 1 renders config items (reuse the existing `ConfigItem` body from `SolutionSetupChecklist`). Step 2 renders connection items: integration name, a "Set up integration" link (`integrationHref(item.key)`, `target="_blank"`), a connectedness icon when `item.connected`, and a **warn-only** banner when `item.has_oauth && !item.connected` ("This integration uses OAuth — connect it in Integrations"). The banner must NOT disable Finish. Extend `SolutionSetupChecklist.tsx` to branch on `item.kind` so connection items render correctly when shown there too.

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh client unit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/solutions/SolutionSetupWizard.tsx client/src/components/solutions/SolutionSetupWizard.test.tsx client/src/components/solutions/SolutionSetupChecklist.tsx
git commit -m "feat(client): Setup wizard with config + connection steps (OAuth warn-only)"
```

### Task 13: README tab (TipTap) + "Setup Required" triangle launch

**Files:**
- Create: `client/src/components/solutions/SolutionReadmeTab.tsx` (+ `.test.tsx`)
- Modify: `client/src/pages/SolutionDetail.tsx`
- Test: `client/src/components/solutions/SolutionReadmeTab.test.tsx`

- [ ] **Step 1: Write the failing vitest**

```tsx
// client/src/components/solutions/SolutionReadmeTab.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SolutionReadmeTab } from "./SolutionReadmeTab";

describe("SolutionReadmeTab", () => {
  it("renders readme content", () => {
    render(<SolutionReadmeTab readme="# Hello" onSave={vi.fn()} canEdit={false} />);
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });
  it("shows empty state when no readme", () => {
    render(<SolutionReadmeTab readme={null} onSave={vi.fn()} canEdit />);
    expect(screen.getByText(/add setup instructions/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh client unit`
Expected: FAIL (component missing).

- [ ] **Step 3: Implement the README tab + wire SolutionDetail**

Create `SolutionReadmeTab.tsx` rendering the markdown via the existing TipTap editor (read mode by default; edit when `canEdit`, calling `onSave`). Empty state ("Add setup instructions") when `readme` is null and `canEdit`. In `SolutionDetail.tsx`: make README the **first tab**; add a "Setup Required" yellow-triangle badge + "Continue Setup" button (opens the `SolutionSetupWizard` in a dialog) whenever `setupStatus.setup_complete` is false. Wire `getSolutionReadme`/`putSolutionReadme`.

- [ ] **Step 4: Run test + tsc + lint**

Run: `./test.sh client unit && cd client && npm run tsc && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/solutions/SolutionReadmeTab.tsx client/src/components/solutions/SolutionReadmeTab.test.tsx client/src/pages/SolutionDetail.tsx
git commit -m "feat(client): README first-tab + Setup Required triangle launches wizard"
```

---

## Phase 6 — E2E round-trip + verification

### Task 14: Full capture→export→install e2e

**Files:**
- Test: `api/tests/e2e/platform/test_solution_connection_refs_e2e.py`

- [ ] **Step 1: Write the e2e test**

```python
# api/tests/e2e/platform/test_solution_connection_refs_e2e.py
import pytest


@pytest.mark.asyncio
async def test_connection_refs_round_trip(client, ...):
    """Capture a solution whose workflow calls integrations.get('HaloPSA')
    (global Integration exists with oauth) -> export -> install into a fresh
    org -> assert: (a) connection declared, (b) integration shell created when
    absent / untouched when present, (c) setup status surfaces the connection,
    (d) README round-trips, (e) no secret in the bundle."""
    # 1. Seed solution + workflow source + global HaloPSA integration (oauth).
    # 2. Export (shareable mode). Assert bundle.connection_schemas has HaloPSA,
    #    template has no client_id/secret.
    # 3. Install into a fresh org WITHOUT HaloPSA -> assert a shell Integration
    #    'HaloPSA' was created with empty client_id and its config schema.
    # 4. GET /solutions/{id}/setup -> a connection item for HaloPSA, has_oauth True.
    # 5. README present in repo round-trips to the install.
```

> Model transport + fresh-org install on `test_solution_import_data.py`.

- [ ] **Step 2: Run + iterate to green**

Run: `./test.sh e2e`; inspect `/tmp/bifrost-<project>/test-results.xml`.
Expected: PASS (fix integration points until green).

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_solution_connection_refs_e2e.py
git commit -m "test(solutions): e2e connection-refs capture->export->install round-trip"
```

### Task 15: Full pre-completion verification

- [ ] **Step 1: Backend quality gates**

Run: `cd api && pyright && ruff check .`
Expected: 0 errors, clean.

- [ ] **Step 2: Contract tripwire**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`
Expected: PASS (bump/refresh fingerprint per CLAUDE.md if it flags).

- [ ] **Step 3: Frontend gates**

Run: `cd client && npm run generate:types && npm run tsc && npm run lint`
Expected: PASS.

- [ ] **Step 4: Full test suites**

Run: `./test.sh all && ./test.sh client unit`
Expected: PASS — except the two KNOWN pre-existing branch failures (`SafeHTMLRenderer.test.tsx`, `test_solution_entities_endpoint::...app_logo`), which are NOT ours and stay failing.

- [ ] **Step 5: Live-drive (debug stack)**

Boot `./debug.sh`, install a solution that references an integration, confirm: shell integration created, "Setup Required" triangle shows, wizard walks configs→connections with the OAuth warning, README renders first-tab. Capture a screenshot.

- [ ] **Step 6: Final commit (if drive surfaced fixes)**

```bash
git add -A && git commit -m "fix(solutions): connection-refs live-drive fixes"
```

---

## Self-Review notes (author)

- **Spec coverage:** §1 unified model → Tasks 4,7,9; §2 declaration → Tasks 1,2,4; §3 template/shell → Tasks 3,5; §4 install blocking → Tasks 7,8; §5 README → Tasks 2,6,13; §6 wizard/triangle → Tasks 9,12,13; runtime backstop → Task 10; testing → Tasks 14,15. All spec sections map to a task.
- **Deferred-but-spec'd:** `connected` (informational icon) is implemented as a best-effort `False` default in Task 9 and the icon in Task 12 — refine the resolver only if cheap; the warn-only contract does not depend on it being accurate.
- **Cross-solution dep blocking** (Task 7/8): the pure module-closure core ships; the DB-aware cross-solution resolver is noted as an append point. If it grows non-trivial, split into a follow-up task rather than blocking the module fix.
- **Type consistency:** `connection_schemas` (bundle field), `SolutionConnectionSchema` (ORM), `_connection_entries` (capture), `_upsert_integration_shells` (deploy), `check_install_needs` (walker), `SolutionSetupItem.kind/has_oauth/connected`, `UnmetNeed` — names used identically across tasks.
