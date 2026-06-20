# Manifest Serialization Unification (Slice 4 of #390) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each `Manifest*` model the single source of truth for its own serialization — `from_row` (DB→model), `view(dest)` (model→the dict each path emits), and `to_orm_values(dest)` (model→importer field dict) — so adding an ORM column is a one-site change, with the existing round-trip detector + per-entity byte-equality proving zero behavior change.

**Architecture:** Today every entity's field handling is hand-written in up to four parallel places: `manifest_generator.serialize_*` (DB→git-sync wire, typed model), `solutions/capture.py::_*_entries` (DB→install bundle, raw dicts), `manifest_import.py::_resolve_*` + `_*_content_from_manifest` (git-sync wire→DB), and `solutions/deploy.py::_upsert_*` (install bundle→DB). We move the field mapping onto the model as classmethods/methods, then rewrite each of the four call sites to delegate to them. The four families keep their **orchestration** (eager-loading, indexer delegation, read-only-guard Core writes, id remap, role-name resolution, non-destructive upsert) — only the per-field dict-building moves to the model. Two destinations (`git_sync`, `install`) replace the three-tier mental model; `with_data`/`with_secrets` stay envelope-level knobs and are out of scope for the entity methods.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy (async), pytest (Dockerized via `./test.sh`).

## Global Constraints

- **Worktree only.** All work in `/home/jack/GitHub/bifrost/.worktrees/390-manifest-unify-spike` (branch `390-manifest-unify-spike`). Never edit the primary `main` checkout.
- **Byte-identical output this pass.** Every new model-derived dict must equal today's writer output BYTE-FOR-BYTE. No shape cleanups, no field renames, no format-version bump — those are Pass 2. The parity test (Task 2) and the round-trip detector are the proof.
- **Allowlist-only emission, ALWAYS.** `view()` enumerates the keys it emits; never `model_dump(exclude=…)`. This is the secret/org-leak guardrail. A field travels only if its destination policy includes it.
- **No dead code, no unrequested fallbacks** (project rule, CLAUDE.md). When a task swaps a call site to the model method, it DELETES the old field-by-field body in the same commit. No old+new coexistence.
- **Tests run via `./test.sh` only** (Dockerized). Never run pytest on the host. JUnit XML at `/tmp/bifrost-<project>/test-results.xml`. Run `./test.sh stack reset` before a full run.
- **pyright + ruff from `api/`** must pass per task. `tests.roundtrip.*` / `src.*` in-editor import errors are path-resolution FPs; tests run fine.
- **The round-trip e2e detector is the backstop on EVERY task.** `./test.sh tests/e2e/roundtrip/` must stay green. It drives all three real paths and goes red on any field drop/mis-transform. A task is not done until it is green.
- **Committing tests need cleanup.** Any new test that commits rows must register the `cleanup_roundtrip_rows` autouse teardown (or delete its own rows), or it leaks into sibling tests (a global `RoundTrip Agent` once broke `test_agent_router_access`).
- **Solution-deploy tests keep `install_solution_write_guard()`** (prod-faithful read-only guard) — deploy/sync writes to `solution_id`-bearing rows must use Core insert/update/delete, never ORM mutate-then-flush.

---

## The unified-model contract (what every entity task implements)

Each `Manifest*` model gains three surfaces. The exact shapes were proven byte-identical against the live writers by the Agent spike (`api/tests/spike/test_agent_unify_spike.py`, THROWAWAY — delete in Task 1).

### `Destination` enum

```python
class Destination(str, Enum):
    GIT_SYNC = "git_sync"   # same-env: keep ids+org, secrets scrubbed-from-text, whole-model dump
    INSTALL = "install"     # cross-env: drop-none curated subset, org dropped (scope-inherited)
```

### `from_row(cls, row, *, <junction kwargs>) -> Manifest*`

Builds the model from an ORM row plus caller-supplied eager-loaded junction lists (roles, tool_ids, etc.), exactly matching today's `serialize_*` signatures. Junctions are passed in (never lazy-loaded) — the orchestrator already fetches them in bulk. Returns a populated `Manifest*` instance. This **replaces** `serialize_*` (git-sync) AND `_*_entries`'s row-reading (install).

### `view(self, dest: Destination, *, extras: dict | None = None) -> dict`

Returns the exact dict each path emits today:

- **`GIT_SYNC`**: dump the WHOLE model verbatim, every field including unset `None`s and the deprecated `path=None` — this is `serialize_X(...).model_dump()`, NOT a curated subset (spike finding 1). Implemented as `self.model_dump(mode="json", by_alias=True)`. (The git-sync split-file writer applies `exclude_defaults` at the manifest level later; `view` reproduces the per-entity `model_dump()` the parity oracle compares.)
- **`INSTALL`**: the `_drop_none` curated subset capture emits — allowlisted keys, drop `None`, with per-field normalizations (e.g. `role_names` → `[]` not `None`; `tags`/`knowledge_sources`/`system_tools` forced to `[]`) and **transport extras** (`max_run_timeout`, `workflow_path`, `logo_b64`, …) merged from the `extras` arg. The model declares its install allowlist + normalizations; the orchestrator supplies extras it computed (logo bytes, denormalized workflow ref) via `extras=`.

### `to_orm_values(self, dest: Destination) -> ImportFields`

Returns the partitioned field dict the importers apply. **The import side is a three-way partition, not one list** (spike finding 3): for Form/Agent the content flows through a shared indexer that deliberately omits deploy-owned fields, which `_resolve_*`/`_upsert_*` re-stamp afterward. So `to_orm_values` returns a small dataclass:

```python
@dataclass
class ImportFields:
    indexer_content: dict   # the YAML/dict fed to the shared indexer (Form/Agent only; else {})
    direct: dict            # fields the resolver sets on the ORM row directly
    restamp: dict           # fields re-applied AFTER the indexer (org/access/limits) — Form/Agent
```

For the ~16 non-indexer entities, `indexer_content` and `restamp` are empty and `direct` is the whole field set. The **orchestration stays in the family file** — `to_orm_values` only sources the dicts; `_resolve_*`/`_upsert_*` keep doing the upsert-by-natural-key, id realignment, role sync, remap, Core-vs-ORM write, and read-only-guard handling.

### Per-field ownership metadata

Field ownership (`indexer` vs `direct` vs `restamp`) is real domain logic and must live ON the model, declared once, so `to_orm_values` partitions correctly and a future field-add can't silently leak (the `max_run_timeout` bug class). Declared via a new `classify(...)` kwarg `import_owner=`:

```python
classify(FieldClass.CONTENT, import_owner="indexer")   # Form/Agent inline content
classify(FieldClass.CONTENT, import_owner="restamp")   # access_level/max_iterations/... (Form/Agent)
# default (kwarg omitted) == "direct"
```

`import_owner` only matters for Form and Agent. All other entities leave it at the `direct` default.

---

## File Structure

| File | Responsibility | Change |
|------|---------------|--------|
| `api/bifrost/field_classes.py` | Field-class metadata + `classify()` | **Modify**: add `import_owner=` kwarg + `import_owner_of()` introspector |
| `api/bifrost/manifest_codec.py` | NEW: `Destination` enum, `ImportFields` dataclass, the `EntityCodec` mixin base (shared `view` machinery) | **Create** |
| `api/bifrost/manifest.py` | The 20 `Manifest*` models | **Modify** (every entity task): make each model inherit `EntityCodec`, add `from_row`/install-allowlist/`to_orm_values` |
| `api/src/services/manifest_generator.py` | git-sync DB→wire orchestration | **Modify** (per entity): `serialize_X` body → `ManifestX.from_row(...)`; delete old body |
| `api/src/services/solutions/capture.py` | install DB→bundle orchestration | **Modify** (per entity): `_X_entries` row-read → `from_row(...).view(INSTALL, extras=...)`; delete old dict-build |
| `api/src/services/manifest_import.py` | git-sync wire→DB orchestration | **Modify** (per entity): `_resolve_X` / `_X_content_from_manifest` field-build → `to_orm_values(GIT_SYNC)`; keep orchestration |
| `api/src/services/solutions/deploy.py` | install bundle→DB orchestration | **Modify** (per entity): `_upsert_X` field-build → `to_orm_values(INSTALL)`; keep remap/guard/Core writes |
| `api/tests/unit/test_manifest_codec.py` | NEW: per-entity byte-parity + characterization tests | **Create** (Task 2), grow per entity |
| `api/tests/spike/test_agent_unify_spike.py` | the throwaway spike | **Delete** (Task 1) |

---

## Per-entity conversion pattern (every entity task follows this)

Each entity is converted in ONE task with two internal phases, so the parity oracle is never orphaned:

1. **Phase A — add model methods + parity-test against the LIVE old writer.** Write `from_row`/install-allowlist/`to_orm_values` on the model. Write a parity test that calls BOTH the old writer and the new model method on a richly-seeded row and asserts `==` (key-set first, then values). Run it green. This proves byte-identity while the old code still exists.
2. **Phase B — swap the four call sites + delete old bodies + freeze the oracle.** Rewrite `serialize_X`, `_X_entries`, `_resolve_X`/`_X_content_from_manifest`, `_upsert_X` to delegate to the model. Delete the old field-by-field code. The parity test loses its old-writer oracle (after the swap, calling `serialize_X`/`_X_entries` is CIRCULAR — they run the same model code), so convert it to a **golden-file characterization test** via `assert_golden(produced, "<entity>_<dest>", volatile_keys=...)`, comparing against a committed JSON fixture captured from a detector-verified run (see B5 for the capture recipe — the read-only test mount means fixtures are captured to the writable LOG_DIR and harvested). Run golden + roundtrip e2e green. Commit. (Tiny git-sync-only entities Org/Role froze to an inline literal dict instead — acceptable for a 2-3-key dict, but golden-file is the default for everything with an install path or >~4 keys.)

Entities are ordered simplest→hardest so the pattern is established before the indexer-split entities.

---

## Task 1: Foundation — `Destination`, `ImportFields`, `EntityCodec`, `import_owner` metadata

**Files:**
- Create: `api/bifrost/manifest_codec.py`
- Modify: `api/bifrost/field_classes.py`
- Delete: `api/tests/spike/test_agent_unify_spike.py`
- Test: `api/tests/unit/test_manifest_codec.py` (create, foundation cases only)

**Interfaces:**
- Produces: `Destination` enum (`GIT_SYNC`, `INSTALL`); `ImportFields` dataclass (`indexer_content: dict`, `direct: dict`, `restamp: dict`); `EntityCodec` mixin with `view(self, dest, *, extras=None) -> dict` implementing the GIT_SYNC whole-dump branch + an INSTALL branch that reads a per-model `_install_spec()` hook; `classify(..., import_owner="direct"|"indexer"|"restamp")`; `import_owner_of(model, field) -> str`.

- [ ] **Step 1: Write the failing test for `import_owner` metadata**

```python
# api/tests/unit/test_manifest_codec.py
from bifrost.field_classes import classify, import_owner_of, FieldClass
from pydantic import BaseModel, Field

def test_classify_records_import_owner():
    class M(BaseModel):
        a: str = Field(**classify(FieldClass.CONTENT, import_owner="indexer"))
        b: str = Field(**classify(FieldClass.CONTENT))  # default
    assert import_owner_of(M, "a") == "indexer"
    assert import_owner_of(M, "b") == "direct"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/unit/test_manifest_codec.py::test_classify_records_import_owner -v`
Expected: FAIL (`classify() got an unexpected keyword argument 'import_owner'` / `cannot import name 'import_owner_of'`).

- [ ] **Step 3: Add `import_owner` to `classify` + the introspector**

In `api/bifrost/field_classes.py`, add the kwarg to `classify` (after `keep_on_portable`):

```python
def classify(
    field_class: FieldClass,
    *,
    match_key: bool = False,
    predicate: str | None = None,
    keep_on_portable: bool = False,
    import_owner: str = "direct",
) -> dict:
    extra: dict[str, Any] = {"bifrost_field_class": field_class.value}
    if match_key:
        extra["bifrost_match_key"] = True
    if keep_on_portable:
        extra["bifrost_keep_on_portable"] = True
    if predicate is not None:
        assert predicate in PREDICATES, f"unknown predicate key {predicate!r}"
        extra["bifrost_class_predicate"] = predicate
    assert import_owner in ("direct", "indexer", "restamp"), f"bad import_owner {import_owner!r}"
    if import_owner != "direct":
        extra["bifrost_import_owner"] = import_owner
    return {"json_schema_extra": extra}


def import_owner_of(model: type[BaseModel], field: str) -> str:
    return _extra(model, field).get("bifrost_import_owner", "direct")
```

- [ ] **Step 4: Run the metadata test to verify it passes**

Run: `./test.sh tests/unit/test_manifest_codec.py::test_classify_records_import_owner -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for `EntityCodec.view(GIT_SYNC)` whole-dump**

```python
# append to api/tests/unit/test_manifest_codec.py
from bifrost.manifest_codec import Destination, EntityCodec, ImportFields

def test_view_git_sync_dumps_whole_model_including_nones():
    class M(EntityCodec, BaseModel):
        id: str = Field(**classify(FieldClass.IDENTITY))
        path: str | None = Field(default=None, **classify(FieldClass.CONTENT))
    m = M(id="x")
    # GIT_SYNC == model_dump() verbatim: every field present, None included.
    assert m.view(Destination.GIT_SYNC) == {"id": "x", "path": None}

def test_import_fields_shape():
    f = ImportFields(indexer_content={}, direct={"a": 1}, restamp={})
    assert f.direct == {"a": 1} and f.indexer_content == {} and f.restamp == {}
```

- [ ] **Step 6: Run to verify it fails**

Run: `./test.sh tests/unit/test_manifest_codec.py -v`
Expected: FAIL (`cannot import name 'EntityCodec'`).

- [ ] **Step 7: Create `api/bifrost/manifest_codec.py`**

```python
"""Unified per-entity serialization surface for Manifest* models.

Each Manifest* model mixes in EntityCodec to own its serialization across two
destinations (git_sync: same-env whole-model dump; install: cross-env drop-none
subset). This replaces the four hand-written field-by-field writers
(manifest_generator.serialize_*, capture._*_entries, manifest_import._resolve_*,
deploy._upsert_*) with one source of truth per model. Output is byte-identical
to the legacy writers (proven per-entity in test_manifest_codec.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel


class Destination(str, Enum):
    GIT_SYNC = "git_sync"
    INSTALL = "install"


@dataclass
class ImportFields:
    """The three-way import partition (spike finding 3).

    indexer_content: dict fed to the shared Form/Agent indexer (else {}).
    direct:          fields the resolver sets on the ORM row directly.
    restamp:         fields re-applied AFTER the indexer (org/access/limits).
    """
    indexer_content: dict = field(default_factory=dict)
    direct: dict = field(default_factory=dict)
    restamp: dict = field(default_factory=dict)


class EntityCodec:
    """Mixin adding view()/to_orm_values() to a Manifest* model.

    GIT_SYNC view is generic (whole-model dump). INSTALL view + to_orm_values
    are per-model: each model overrides _install_view() / to_orm_values().
    """

    def view(self, dest: Destination, *, extras: dict[str, Any] | None = None) -> dict:
        if dest is Destination.GIT_SYNC:
            # Whole-model verbatim, by alias, None included — matches
            # serialize_X(...).model_dump(). NOT a curated subset.
            return self.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
        if dest is Destination.INSTALL:
            return self._install_view(extras or {})
        raise ValueError(dest)

    def _install_view(self, extras: dict[str, Any]) -> dict:
        # Default install view: drop-none over the model's own fields + extras.
        # Models with forced-[] fields or alias quirks override this.
        data = self.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
        out = {k: v for k, v in data.items() if v is not None}
        out.update({k: v for k, v in extras.items() if v is not None})
        return out

    def to_orm_values(self, dest: Destination) -> ImportFields:  # pragma: no cover - overridden
        raise NotImplementedError
```

- [ ] **Step 8: Run the foundation tests to verify they pass**

Run: `./test.sh tests/unit/test_manifest_codec.py -v`
Expected: PASS (all four cases).

- [ ] **Step 9: Delete the throwaway spike**

```bash
git rm api/tests/spike/test_agent_unify_spike.py
```
If `api/tests/spike/` is now empty, remove it too (`rmdir api/tests/spike` — leave any `__init__.py`/conftest only if other spikes exist; none do).

- [ ] **Step 10: Typecheck + lint**

Run: `cd api && pyright bifrost/manifest_codec.py bifrost/field_classes.py && ruff check bifrost/`
Expected: 0 errors.

- [ ] **Step 11: Commit**

```bash
git add api/bifrost/manifest_codec.py api/bifrost/field_classes.py api/tests/unit/test_manifest_codec.py
git rm api/tests/spike/test_agent_unify_spike.py
git commit -m "feat(manifest): EntityCodec foundation + import_owner metadata (Slice 4 Task 1)"
```

---

## Task 2: Parity-test harness

**Files:**
- Test: `api/tests/unit/test_manifest_codec.py` (add the reusable helpers)

**Interfaces:**
- Produces: `assert_parity(model_dict, legacy_dict)` — asserts key-sets equal first (the drift surface) then values equal, with a readable diff; used by every entity task's Phase-A parity test.

- [ ] **Step 1: Add the parity helper (no test of its own — it's used by entity tasks)**

```python
# append to api/tests/unit/test_manifest_codec.py
def assert_parity(produced: dict, legacy: dict, *, label: str = "") -> None:
    """Byte-parity assertion for entity conversions: key-set first, then values."""
    only_new = set(produced) - set(legacy)
    only_old = set(legacy) - set(produced)
    assert not only_new and not only_old, (
        f"{label} field-set mismatch: only_new={only_new} only_old={only_old}"
    )
    assert produced == legacy, f"{label} values diverge:\n produced={produced}\n legacy={legacy}"
```

- [ ] **Step 2: Add a self-check so the helper is exercised**

```python
def test_assert_parity_passes_on_equal_and_fails_on_diff():
    assert_parity({"a": 1}, {"a": 1}, label="ok")
    import pytest
    with pytest.raises(AssertionError):
        assert_parity({"a": 1}, {"a": 2}, label="bad")
    with pytest.raises(AssertionError):
        assert_parity({"a": 1, "b": 2}, {"a": 1}, label="extra")
```

- [ ] **Step 3: Run to verify it passes**

Run: `./test.sh tests/unit/test_manifest_codec.py::test_assert_parity_passes_on_equal_and_fails_on_diff -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add api/tests/unit/test_manifest_codec.py
git commit -m "test(manifest): parity-test harness for entity conversions (Slice 4 Task 2)"
```

---

## Entity tasks: ordering rationale

Convert simplest→hardest. The hand-written-writer counts and special cases per entity (from the four-family mapping):

| Tier | Entities | Why this tier |
|------|----------|---------------|
| A: leaf, git-sync-only | Organization, Role | 2-3 fields, no install path, no junction, no import split |
| B: simple, both paths | Workflow, Table, CustomClaim, Config | direct columns, drop-none install subset, natural-key upsert; Config has secret predicate |
| C: nested children | Integration (+config_schema/oauth/mappings), EventSource (+schedule/webhook/subscriptions), MCPServer (+connection/tool), SolutionConfigSchema | nested model lists, child upsert reconciliation |
| D: install transport extras | App | logo_b64/src/bin/dist transport extras, install-only |
| E: indexer split | Form, Agent | three-way import partition, `import_owner` metadata, indexer + restamp |

Each entity below is ONE task following the Phase-A/Phase-B pattern. To keep this plan readable without 20× repetition, the two tier-A tasks are written in full step detail as the worked examples; tiers B–E give the entity-specific deltas (the exact `from_row` field map, install allowlist + normalizations + extras, and `to_orm_values` partition) plus the same fixed step sequence. **The step sequence is identical for every entity** — an implementer runs it verbatim, substituting the entity's deltas.

### The fixed step sequence (every entity task)

```
Phase A:
 A1. Make ManifestX inherit EntityCodec; add from_row + (if install) _install_view + to_orm_values per the entity deltas.
 A2. Write Phase-A parity test(s): seed a rich row (+junctions), call the LEGACY writer and the new model method, assert_parity. One test per active path (git_sync always; install where the entity has an install path).
 A3. Run parity test → PASS (proves byte-identity vs live writers).
Phase B:
 B1. Rewrite serialize_X (git-sync) to `return ManifestX.from_row(row, <junctions>)`; delete old body.
 B2. Rewrite _X_entries (install) to `ManifestX.from_row(row, <junctions>).view(Destination.INSTALL, extras=<computed extras>)`; delete old dict-build. (Skip if entity has no install path.)
 B3. Rewrite _resolve_X / _X_content_from_manifest (git-sync import) to source fields from `manifest_x.to_orm_values(Destination.GIT_SYNC)`; KEEP orchestration (upsert, id realign, role sync, indexer call). Delete old field-build.
 B4. Rewrite _upsert_X (install import) to source fields from `manifest_x.to_orm_values(Destination.INSTALL)`; KEEP remap, org/solution stamp, Core writes, guard, role sync. Delete old field-build. (Skip if entity has no install path.)
 B5. Freeze the parity test → GOLDEN-FILE characterization test. After the swap, `serialize_X`/`_X_entries` DELEGATE to the model, so comparing the model output against them is CIRCULAR (a tautology — both run the same code). Convert the test to use the `assert_golden(produced, "<entity>_<dest>", volatile_keys=...)` oracle (added to `test_manifest_codec.py`): it compares `from_row(...).view(dest)` against a committed JSON fixture under `api/tests/unit/golden/manifest_codec/`. **DO NOT keep calling `serialize_X`/`_X_entries` as the oracle.** Capture the fixture this way (the `/app` test mount is READ-ONLY, so the test cannot self-write the fixture):
   1. Seed the test row with DETERMINISTIC non-id content (e.g. a fixed `name`, not `f"rt_{uuid}"`) so the golden is stable; pass per-run-random PK/FK fields (`id`, `roles`, nested ids) in `volatile_keys=` so they're masked to a sentinel.
   2. Capture: `COMPOSE_PROJECT_NAME=<proj> LOG_DIR=/tmp/bifrost-<proj> docker compose -p <proj> -f docker-compose.test.yml --profile test run --rm -e UPDATE_GOLDEN=1 test-runner pytest tests/unit/test_manifest_codec.py -q` (env passthrough needs the direct compose run; `./test.sh` does NOT forward `UPDATE_GOLDEN`). This writes fixtures to `/tmp/bifrost-<proj>/golden/manifest_codec/`.
   3. Harvest: `cp /tmp/bifrost-<proj>/golden/manifest_codec/<entity>_*.json api/tests/unit/golden/manifest_codec/` and INSPECT each (correct key set, org absent on install, no leaked secrets) before committing.
   4. Verify: run `./test.sh tests/unit/test_manifest_codec.py` TWICE in normal mode — both green proves the golden is non-circular AND idempotent across random seeds.
 B6. Run: golden characterization test + `./test.sh tests/e2e/roundtrip/` (the detector) → PASS.
 B7. pyright + ruff from api/ → 0 errors.
 B8. Commit `refactor(manifest): unify <Entity> serialization onto the model (Slice 4)`.
```

**Detector backstop:** after B6 the round-trip e2e MUST be green. If it reds, a field was dropped/mis-transformed — stop, diff the failing entity's before/after, do not adjust the test to pass (test-failure discipline). The detector checks the field round trip across all three real paths; the parity/characterization test checks byte-identity at the writer boundary. Both must pass.

---

## Task 3 (Tier A): Organization — worked example

**Files:**
- Modify: `api/bifrost/manifest.py` (`ManifestOrganization`)
- Modify: `api/src/services/manifest_generator.py:73-75` (`serialize_organization`)
- Modify: `api/src/services/manifest_import.py:997-1035` (`_resolve_organization`)
- Test: `api/tests/unit/test_manifest_codec.py`

**Interfaces:**
- Consumes: `Destination`, `EntityCodec`, `assert_parity` (Tasks 1-2).
- Produces: `ManifestOrganization.from_row(org) -> ManifestOrganization`; `ManifestOrganization.to_orm_values(dest) -> ImportFields` (`direct={"id","name","is_active"}`). Organization has NO install path (not in any bundle).

Entity deltas:
- **from_row**: `id=str(org.id)`, `name=org.name`, `is_active=org.is_active`.
- **install view**: N/A (Organization is identity, never in a solution bundle).
- **to_orm_values**: `direct={"id": self.id, "name": self.name, "is_active": self.is_active}`, `indexer_content={}`, `restamp={}`. (The `_resolve_organization` id-vs-name upsert + realign orchestration STAYS in manifest_import.py.)

- [ ] **Step A1: Make `ManifestOrganization` inherit `EntityCodec` + add methods**

```python
# api/bifrost/manifest.py
from bifrost.manifest_codec import Destination, EntityCodec, ImportFields

class ManifestOrganization(EntityCodec, BaseModel):
    id: str = Field(**classify(FieldClass.IDENTITY))
    name: str = Field(**classify(FieldClass.CONTENT, match_key=True))
    is_active: bool = Field(default=True, **classify(FieldClass.ENVIRONMENT))

    @classmethod
    def from_row(cls, org) -> "ManifestOrganization":
        return cls(id=str(org.id), name=org.name, is_active=org.is_active)

    def to_orm_values(self, dest: Destination) -> ImportFields:
        return ImportFields(direct={"id": self.id, "name": self.name, "is_active": self.is_active})
```

(Pydantic v2: `EntityCodec` is a plain mixin; `class ManifestOrganization(EntityCodec, BaseModel)` is fine — `BaseModel` last so its metaclass wins.)

- [ ] **Step A2: Write the Phase-A git-sync parity test**

```python
# api/tests/unit/test_manifest_codec.py — uses the e2e db_session fixture, so mark e2e
import pytest
pytestmark_org = pytest.mark.e2e

@pytest.mark.e2e
async def test_organization_git_sync_parity(db_session):
    import uuid
    from src.models.orm.organizations import Organization
    from src.services.manifest_generator import serialize_organization
    from bifrost.manifest import ManifestOrganization
    from bifrost.manifest_codec import Destination

    org = Organization(id=uuid.uuid4(), name="RT Org Parity", is_active=True)
    db_session.add(org); await db_session.commit()

    legacy = serialize_organization(org).model_dump()
    produced = ManifestOrganization.from_row(org).view(Destination.GIT_SYNC)
    assert_parity(produced, legacy, label="organization git_sync")
```

(Cleanup: `RT Org %` rows are already swept by `cleanup_roundtrip_rows`; if this test file doesn't import that fixture, add a teardown deleting the row. Simplest: name it `RT Org Parity` and delete it at end of test.)

- [ ] **Step A3: Run parity → PASS**

Run: `./test.sh tests/unit/test_manifest_codec.py::test_organization_git_sync_parity -v`
Expected: PASS. (If it fails, `from_row` diverges from `serialize_organization` — fix `from_row`, not the test.)

- [ ] **Step B1: Swap `serialize_organization` to delegate; delete old body**

```python
# api/src/services/manifest_generator.py
def serialize_organization(org: Organization) -> ManifestOrganization:
    return ManifestOrganization.from_row(org)
```

- [ ] **Step B3: Source `_resolve_organization` fields from `to_orm_values`; keep upsert orchestration**

In `manifest_import.py::_resolve_organization`, replace the inline `{"id":…, "name":…, "is_active":…}` field literals used for the insert/update with `morg.to_orm_values(Destination.GIT_SYNC).direct`. Keep the id-first / name-fallback / realign branching exactly as-is — only the field-source dict changes. Delete the now-duplicated inline field dict.

- [ ] **Step B5: Freeze parity → characterization**

Capture the `produced` dict from B-A3 (run once, copy the printed dict) and replace the `legacy = serialize_organization(...)` line with that frozen literal:

```python
    expected = {"id": str(org.id), "name": "RT Org Parity", "is_active": True}
    produced = ManifestOrganization.from_row(org).view(Destination.GIT_SYNC)
    assert_parity(produced, expected, label="organization git_sync")
```

- [ ] **Step B6: Run characterization + detector → PASS**

Run: `./test.sh tests/unit/test_manifest_codec.py::test_organization_git_sync_parity -v && ./test.sh tests/e2e/roundtrip/ -v`
Expected: both PASS.

- [ ] **Step B7: pyright + ruff**

Run: `cd api && pyright bifrost/manifest.py src/services/manifest_generator.py src/services/manifest_import.py && ruff check bifrost/ src/services/`
Expected: 0 errors.

- [ ] **Step B8: Commit**

```bash
git add api/bifrost/manifest.py api/src/services/manifest_generator.py api/src/services/manifest_import.py api/tests/unit/test_manifest_codec.py
git commit -m "refactor(manifest): unify Organization serialization onto the model (Slice 4)"
```

---

## Task 4 (Tier A): Role

Same fixed sequence. Deltas:
- **from_row**: `id=str(role.id)`, `name=role.name`.
- **install view**: N/A (roles are emitted as id lists on other entities; `ManifestRole` itself is git-sync-only — never in a bundle).
- **to_orm_values**: `direct={"id": self.id, "name": self.name}`.
- Call sites: `manifest_generator.py:78-80` (`serialize_role`), `manifest_import.py:1037-1075` (`_resolve_role`, keep id-vs-name realign). No capture/deploy path.
- Parity test: `test_role_git_sync_parity` seeding `Role(name="rt_role_parity")`; cleanup sweeps `rt_role_%`.

Commit: `refactor(manifest): unify Role serialization onto the model (Slice 4)`.

---

## Task 5 (Tier B): Workflow

Deltas:
- **from_row(wf, *, roles=None)** — mirror `serialize_workflow` exactly incl. fallback defaults: `type or "workflow"`, `access_level or "authenticated"`, `endpoint_enabled or False`, **`timeout_seconds if timeout_seconds is not None else 1800`** (NOT `or 1800` — `0` means "no timeout" and `or` would clobber it; `serialize_workflow:97` uses the `is not None` guard), `public_endpoint or False`, `category or "General"`, `tags or []`, `roles=roles or []`, org→str-or-None.
- **install view** (`_install_view`): allowlist = `id, name, function_name, path, type, description, endpoint_enabled, public_endpoint, timeout_seconds, category, tags(→[] not dropped), access_level, roles, role_names`; drop-none; `tags` forced to `[]`; `role_names` from `extras` (capture computes via `_role_names(roles)`); `organization_id` ABSENT (scope-inherited). Pass `roles`/`role_names` via `extras`. Confirm against `capture._workflow_entries` (lines 383-408) key-for-key.
- **to_orm_values**: all `direct` (no indexer). git_sync direct = the `_resolve_workflow` column set (defaults applied: 1800/False/General/[]/"workflow", `is_active=True`, `name` set-only-when-unset is RESOLVER logic — keep it in `_resolve_workflow`, `to_orm_values` just supplies the value). install direct mirrors `_upsert_workflows` (lines 783-802): same columns, `access_level` present-only. roles handled by resolver/`_sync_entity_roles` (role_names→role_id) — NOT in `to_orm_values`.
- Call sites: `manifest_generator.py:83-101`, `capture.py:383-408`, `manifest_import.py:1119-1189` + `_index_workflows_from_manifest:871` (AST re-index STAYS), `deploy.py:756-809`.
- Parity tests: `test_workflow_git_sync_parity` + `test_workflow_install_parity` (seed a `solution_id`-bearing workflow + a `WorkflowRole`; install test seeds under a Solution and calls `_workflow_entries`).

Commit: `refactor(manifest): unify Workflow serialization onto the model (Slice 4)`.

---

## Task 6 (Tier B): Table

Deltas:
- **from_row(table)**: `id, name, description, organization_id→str-or-None`, `policies` parsed from `table.access["policies"]` via `ManifestPolicy.model_validate` (None if absent — keep the legacy `serialize_table` lines 305-310 logic), `table_schema` via the `schema` alias (construct with `**{"schema": table.schema}`).
- **install view**: allowlist = `id, name, description, schema, policies`; drop-none; `policies` from `(t.access or {}).get("policies")` (capture lines 455-468 — capture reads access directly, NOT via the model's parsed policies; ensure `from_row`→`view(INSTALL)` reproduces the same raw list). `organization_id` ABSENT.
- **to_orm_values**: all direct. git_sync direct mirrors `_resolve_table` (id/name/org/description/`table_schema`→`schema` column/policies→`access` JSONB wrap-and-validate — the wrap+`TablePolicies` validation + default `admin_bypass` seed STAYS in resolver). install direct mirrors `_upsert_tables` (orphan-reattach Core update + policy publish STAY).
- Note the alias: `model_dump(by_alias=True)` emits `schema`, matching both writers. Verify the parity test sees `schema` (not `table_schema`).
- Call sites: `manifest_generator.py:296-319`, `capture.py:455-468`, `manifest_import.py:2014-2127`, `deploy.py:811-974`.

Commit: `refactor(manifest): unify Table serialization onto the model (Slice 4)`.

---

## Task 7 (Tier B): CustomClaim

Deltas:
- **from_row(claim)**: `id, name, description, organization_id→str` (no None fallback — claims always org-bound), `type` (enum/str passthrough as `serialize_custom_claim:291`), `query=ClaimQuery.model_validate(claim.query)`.
- **install view**: allowlist = `id, name, description, type, query`; drop-none; `query` serialized via the model (`mode="json"`); `organization_id` ABSENT. Match `capture._claim_entries` (470-485) — note it emits raw `c.query` (a dict); ensure `view(INSTALL)` produces the same JSON dict (`model_dump(mode="json")` of the ClaimQuery == the stored dict).
- **to_orm_values**: all direct. git_sync direct mirrors `_resolve_custom_claim` (query→`.model_dump(mode="json")`; NO id realign on natural-key match — that branch STAYS in resolver). install direct mirrors `_upsert_claims` (`ClaimQuery.model_validate` re-validate + org/solution stamp STAY).
- Call sites: `manifest_generator.py:284-293`, `capture.py:470-485`, `manifest_import.py:2129-2213`, `deploy.py:976-1011`.

Commit: `refactor(manifest): unify CustomClaim serialization onto the model (Slice 4)`.

---

## Task 8 (Tier B): Config

Deltas:
- **from_row(cfg)**: `id, integration_id→str-or-None, key, config_type` (enum→`.value` else str else `"string"`, per `serialize_config:277`), `description, organization_id→str-or-None`, `value`: **None if config_type is SECRET** (the redaction at line 280) else `cfg.value`. The SECRET predicate is already on the model field — `from_row` still applies the value-redaction explicitly (the predicate governs the round-trip oracle, the redaction governs the actual bytes).
- **install view**: Config has NO standalone install entry — config VALUES travel via the `with_secrets`/`include_values` envelope stream (`_config_values`), NOT `_*_entries`. So **no install view for Config**; `to_orm_values(INSTALL)` is also N/A. Only git_sync + the value-stream (out of scope). Document this in the task: Config's install path is the value stream, not an entity bundle entry — do NOT add an install allowlist.
- **to_orm_values(GIT_SYNC)**: all direct, mirroring `_resolve_config` field set (config_type→ConfigType enum coercion, `config_schema_id` resolution, secret-skip-if-existing, `updated_by="git-sync"` — all STAY in resolver; `to_orm_values` supplies id/key/integration_id/organization_id/config_type/value/description).
- Call sites: `manifest_generator.py:269-281`, `manifest_import.py:1860-1942`. (No `_config_entries` for VALUES in the entity loop; `_config_entries` in capture is for `SolutionConfigSchema` — that's Task 12, a different entity.)

Commit: `refactor(manifest): unify Config serialization onto the model (Slice 4)`.

---

## Task 9 (Tier C): Integration (+ config_schema, oauth_provider, mappings)

Deltas:
- **Nested-model codec**: `ManifestIntegrationConfigSchema`, `ManifestOAuthProvider`, `ManifestIntegrationMapping` each get `from_row`. `ManifestIntegration.from_row(integ, *, config_schema=None, oauth_provider=None, mappings=None)` builds the parent + maps children via their `from_row`, mirroring `serialize_integration` (215-266) incl. the `client_id or "__NEEDS_SETUP__"` fallback and conditional oauth (None if no provider).
- **install view**: Integration is NOT in the standard install bundle as `ManifestIntegration` — capture emits `connection_schemas` (integration TEMPLATES) via `_connection_entries`/`build_integration_template`, a different shape. So **no `view(INSTALL)` for ManifestIntegration**; the install side is integration SHELLS (`_upsert_integration_shells`) sourced from `connection_schemas`. Document: Integration's install representation is the connection-schema template, out of this entity's `view` scope. Only convert the git-sync path + the git-sync importer for the full `ManifestIntegration`.
- **to_orm_values(GIT_SYNC)**: parent `direct` mirrors `_resolve_integration` Integration columns; children are reconciled by the resolver's upsert-by-natural-key (`(integration_id, key)` for schema, `(integration_id, org_id)` for mappings) — that non-destructive reconciliation + cache-refresh-on-id-rewrite + oauth_token_id preservation STAY in the resolver. `to_orm_values` exposes the parent field dict + the child model lists; resolver iterates them.
- Call sites: `manifest_generator.py:215-266`, `manifest_import.py:1632-1858`. (Install: `capture._connection_entries:641-730` + `deploy._upsert_integration_shells:1396-1464` + `_upsert_connection_declarations:1466-1531` are the SolutionConnectionSchema path — Task 12-adjacent; keep them out of this task to avoid conflating two entity shapes.)
- Parity test seeds an Integration with ≥1 config_schema row, an OAuthProvider, ≥1 mapping; asserts git_sync parity on the whole nested structure.

Commit: `refactor(manifest): unify Integration serialization onto the model (Slice 4)`.

---

## Task 10 (Tier C): EventSource (+ schedule, webhook, subscriptions)

Deltas — **note events are already half-unified**: `capture._event_entries` (410-453) ALREADY delegates to `serialize_event_source(...).model_dump(mode="json")`. So the install view for events == the git_sync model dump. This is the cleanest tier-C entity.
- **from_row(es, *, schedule=None, webhook=None, subscriptions=None)**: mirror `serialize_event_source` (322-370) incl. all schedule/webhook fallbacks (rate_limit 60/60/True defaults, overlap_policy `.value`, source_type str-or-`.value`) and the nested `ManifestEventSubscription` list (each via `from_row`).
- **install view**: EventSource MUST override `_install_view` to return the FULL `model_dump(mode="json", by_alias=True)` — i.e. `view(INSTALL) == view(GIT_SYNC)`, Nones INCLUDED — because `_event_entries` emits `serialize_event_source(...).model_dump(mode="json")` verbatim, which carries `adapter_name`/`webhook_integration_id`/`webhook_config`/etc. as `None` when absent. The default `EntityCodec._install_view` drops None and would diverge. Override:
  ```python
  def _install_view(self, extras):  # EventSource: capture dumps the whole model, Nones kept
      return self.model_dump(mode="json", by_alias=True)
  ```
  Confirm parity by asserting `from_row(...).view(INSTALL) == serialize_event_source(...).model_dump(mode="json")`. (EventSource.organization_id is NOT absent on install — capture emits it and deploy stamps it; keep it in the view.)
- **to_orm_values**: all direct (no indexer). git_sync `_resolve_event_source` (2215-2383) splits parent/schedule/webhook/subscription rows — `to_orm_values` supplies the parent field dict; the resolver keeps building child rows + the workflow-ref resolution (`_resolve_workflow_ref`, portable path::func) + imported-wf gate. install `_upsert_events` (1533-1651) full-replace Core writes + subscription remap STAY; `to_orm_values(INSTALL)` supplies parent fields.
- Call sites: `manifest_generator.py:322-370`, `capture.py:410-453` (becomes `from_row(...).view(INSTALL)`), `manifest_import.py:2215-2383`, `deploy.py:1533-1651`.

Commit: `refactor(manifest): unify EventSource serialization onto the model (Slice 4)`.

---

## Task 11 (Tier C): MCPServer (+ connection, connection_tool)

Deltas:
- **Nested codec**: `ManifestMCPConnectionTool.from_row`, `ManifestMCPConnection.from_row(conn, *, tools=None)` (org→str, `service_oauth_token_id→str-or-None`, `encrypted_client_secret` NEVER emitted), `ManifestMCPServer.from_row(server, *, connections_by_id=None, tools_by_connection=None)` mirroring `serialize_mcp_server` (409-440).
- **install view**: MCP servers have NO install bundle path (capture.py has no `_mcp_*_entries` — confirmed by the capture mapping). So git-sync only. No `view(INSTALL)`, no `_upsert` for MCP in deploy.
- **to_orm_values(GIT_SYNC)**: parent + connection + tool `direct` dicts; the resolver's UUID-only upsert + parent-not-imported skip + secret-placeholder + tool-catalog reconcile STAY.
- Call sites: `manifest_generator.py:373-440`, `manifest_import.py:2387-2529`.

Commit: `refactor(manifest): unify MCPServer serialization onto the model (Slice 4)`.

---

## Task 12 (Tier C): SolutionConfigSchema

Deltas — this is an **install-only** entity (`ManifestSolutionConfigSchema`); it has no git-sync `serialize_*` (it's solution-scoped). Source paths: `capture._config_entries` (620-639) → `deploy._upsert_config_schemas` (1356-1394).
- **from_row(cs)**: `id, key, type, required, description, default, position` (mirror `_config_entries`).
- **install view**: allowlist = those 7 keys; drop-none. `solution_id`/org ABSENT (stamped at deploy).
- **to_orm_values(INSTALL)**: all direct (`key/type/required/description/default/position`); the `solution_id` stamp + key-uniqueness 409 STAY in `_upsert_config_schemas`.
- Call sites: `capture.py:620-639`, `deploy.py:1356-1394`. No manifest_generator/manifest_import path.
- Since there's no git-sync path, this task's parity test is install-only.

Commit: `refactor(manifest): unify SolutionConfigSchema serialization onto the model (Slice 4)`.

---

## Task 13 (Tier D): App (install transport extras)

Deltas — App is where install transport extras concentrate.
- **from_row(app, *, roles=None)**: mirror `serialize_app` (199-212): `path=app.repo_path.rstrip("/")`, `dependencies or {}`, `access_level or "authenticated"`, `app_model or "inline_v1"`, org→str-or-None, roles passed in.
- **install view** (`_install_view`): allowlist (model fields) = `id, name, slug, description, dependencies, app_model, access_level, roles, role_names`; drop-none. **`path` is NOT in the install allowlist** — the model field is `path` but capture emits the key `repo_path` (capture.py:541), and `tests/roundtrip/paths.py:161` already classifies `repo_path` as a transport extra. So `repo_path` rides in via `extras=` alongside the other transport extras, and the model OMITS `path` from the install allowlist (this reproduces capture's bytes and matches `EXTRA_FIELD_POLICY`). **Transport extras via `extras=`** (capture computes them, lines 495-553): `repo_path`, `logo_b64`, `logo_content_type`, `src_files`, `bin_files`, `dist_files`, `bin_dist_files` — all declared in `EXTRA_FIELD_POLICY`. The orchestrator (`_app_entries`) computes them and passes via `extras=`; `from_row`'s `path=app.repo_path.rstrip("/")` is for the GIT_SYNC view only. `organization_id` ABSENT. Verify in the parity test that the install dict keys `repo_path` (not `path`).
- **to_orm_values**: all direct (no indexer). git_sync `_resolve_app` (1944-2012) slug-natural-key upsert STAYS. install `_upsert_apps` (1013-1157): `access_level` restamp-if-present, logo decode-and-stamp, app_model `standalone_v2` validation, route-collision guard, build-spec return — ALL STAY; `to_orm_values(INSTALL)` supplies the column dict, the orchestration consumes the extras (logo bytes, src/dist files) directly as it does today.
- Call sites: `manifest_generator.py:199-212`, `capture.py:487-555`, `manifest_import.py:1944-2012`, `deploy.py:1013-1157`.
- Parity test for install MUST seed an app with logo_data + source files so the extras path is exercised, and assert the extra keys are present (and accounted by `EXTRA_FIELD_POLICY`).

Commit: `refactor(manifest): unify App serialization onto the model (Slice 4)`.

---

## Task 14 (Tier E): Form (indexer split)

Deltas — first indexer-split entity. **Set `import_owner` on fields.**
- **Field ownership** on `ManifestForm`: `description, workflow_id, launch_workflow_id, default_launch_params, allowed_query_params, form_schema` → `import_owner="indexer"` (these flow through FormIndexer); `organization_id, access_level` → `import_owner="restamp"` (re-stamped after the indexer by both `_index_forms_from_manifest:856-867` and `_upsert_forms:1264-1272`); `id, name` → `import_owner="indexer"` (they're seeded into the indexer YAML unconditionally — see `to_orm_values` below; NOT `direct`). `roles`/`role_names` are junction (handled by role sync, not in ImportFields).
- **from_row(form, *, roles=None, fields=None)**: mirror `serialize_form` (134-161): `access_level.value or "role_based"`, `form_schema={"fields":[...]}` from `fields` via `_form_field_to_schema_dict` (the field→dict helper has its own drop-none — keep that helper, call it from `from_row` or pass pre-built `form_schema` via a param; simplest is `from_row` accepts `fields` and builds the schema using the existing helper).
- **install view**: allowlist matches `capture._form_entries` (557-588): `id, name, description, workflow_id, launch_workflow_id, default_launch_params, allowed_query_params, access_level, roles, role_names, form_schema`; drop-none; `role_names`→[]; transport extras via `extras=`: `workflow_path`, `workflow_function_name` (capture lines 580-581, denormalized workflow ref). `organization_id` ABSENT. `form_schema.fields[]` built via the existing `_form_field_entry` drop-none helper (keep it).
- **to_orm_values(dest)**: partition by `import_owner_of`: `indexer_content` = `{id (always), name (always, `or ""`)}` + the indexer-owned fields drop-none (`description, workflow_id, launch_workflow_id, default_launch_params, allowed_query_params, form_schema`) — EXACTLY matching `_form_content_from_manifest:271-286` which unconditionally seeds `{"id": mform.id, "name": mform.name or ""}` then drop-none-adds the rest. **`id` and `name` go in `indexer_content`, NOT `direct`** — the indexer is their first consumer (it builds the form_schema YAML from them, `_index_forms_from_manifest:835-846`). So `direct = {}` for Form (id is the match key the resolver reads off the manifest entry; `is_active=True`/`created_by` are RESOLVER constants — keep in resolver). `restamp` = `organization_id, access_level`. The FormIndexer call, workflow-ref resolution, and the post-index re-stamp + `updated_at` STAY in `_index_forms_from_manifest`/`_upsert_forms`.
- Call sites: `manifest_generator.py:134-161` + `_form_field_to_schema_dict:104-131`, `capture.py:557-588` + `_form_field_entry:966-987`, `manifest_import.py:2531-2575` + `_form_content_from_manifest:271-286` + `_index_forms_from_manifest:809-869`, `deploy.py:1234-1281`.
- Two parity tests (git_sync, install) seeding a form with ≥2 fields + a workflow binding + a role. Plus a `to_orm_values` partition test asserting `description` lands in `indexer_content`, `access_level` in `restamp`, `name` in `direct`.

Commit: `refactor(manifest): unify Form serialization onto the model (Slice 4)`.

---

## Task 15 (Tier E): Agent (indexer split — the hardest, spike-proven)

Deltas — the spike's target; reproduce its proven shapes exactly.
- **Field ownership** on `ManifestAgent`: `description, channels, tool_ids, delegated_agent_ids, knowledge_sources, system_tools, mcp_connection_ids, llm_model, llm_max_tokens` → `import_owner="indexer"`; `access_level, max_iterations, max_token_budget` → `import_owner="restamp"`; `id, name, system_prompt` → `direct`. (`max_run_timeout` is a transport extra, not a model field — handled like App's extras; it's restamp-owned at deploy. The Slice-2 fix that added it to the deploy re-stamp STAYS; `to_orm_values` exposes it via the extras/restamp seam, matching `tests/roundtrip/paths.py` `EXTRA_FIELD_POLICY` `("ManifestAgent","max_run_timeout")`.)
- **from_row(agent, *, roles=None, tool_ids=None, delegated_agent_ids=None, mcp_connection_ids=None)**: EXACTLY the spike's `from_row` (lines 77-113) minus the `path` literal already on the model — `access_level.value or "role_based"`, list-copies for channels/knowledge_sources/system_tools, junctions passed in.
- **install view**: the spike's `INSTALL_FIELDS` allowlist (lines 131-136) — `id, name, description, system_prompt, channels, access_level, knowledge_sources, system_tools, llm_model, llm_max_tokens, max_iterations, max_token_budget, tool_ids, delegated_agent_ids, roles, role_names`; drop-none; `role_names`→[] (spike line 184-188); `knowledge_sources`/`system_tools` forced [] ; `max_run_timeout` via `extras=` (capture line 610). NOTE: install does NOT carry `mcp_connection_ids` (capture `_agent_entries` omits it) but git_sync DOES — confirm the install allowlist excludes it. `organization_id` ABSENT.
- **to_orm_values(dest)**: the spike's import partition (lines 145-173): `indexer_content` = the `INDEXER_CONTENT_FIELDS` drop-none dict (name forced `""`, id always present, non-empty lists only — matching `_agent_content_from_manifest:289-316`); `direct` = `id, name, system_prompt` (+ resolver constants `is_active`/`created_by` stay in resolver); `restamp` = `access_level, max_iterations, max_token_budget` (+ `max_run_timeout` via extras). The AgentIndexer call, tool-ref resolution, MCP-grant sync (`set_mcp_connection_grants`), and the deploy Core re-stamp of access/limits/timeout STAY in `_index_agents_from_manifest`/`_upsert_agents`.
- Call sites: `manifest_generator.py:164-196`, `capture.py:590-618`, `manifest_import.py:2579-2626` + `_agent_content_from_manifest:289-316` + `_index_agents_from_manifest:903-995`, `deploy.py:1283-1354`.
- **IN-SCOPE PROD BUG FIX (verifier-found, `max_run_timeout` bug class).** `deploy._upsert_agents:1346-1354` unconditionally calls `_sync_agent_mcp_connections(agent_id, self._parse_uuids(magent.get("mcp_connection_ids")))`. Since `_agent_entries` does NOT emit `mcp_connection_ids` on the install bundle, `_parse_uuids` returns `[]` → the full-replace **WIPES every MCP grant on each redeploy**. The git-sync path already guards this (`_index_agents_from_manifest:969` only syncs when non-empty). Mirror that guard in deploy: only sync when the bundle actually carries connection ids. Add this as a step in Phase B (B4) with its own e2e assertion (seed an agent with an MCP grant, redeploy, assert the grant survives). This is a real silent-data-loss fix, intentionally folded in because this task owns the agent install path.
  ```python
  # deploy.py _upsert_agents — replace the unconditional sync:
  mcp_ids = self._parse_uuids(magent.get("mcp_connection_ids") or [])
  if mcp_ids:  # absent on install bundle → don't full-replace-to-empty (mirrors git-sync :969)
      await self._sync_agent_mcp_connections(agent_id, mcp_ids)
  ```
- Parity tests = the four spike tests, ported into `test_manifest_codec.py` against the real model methods (git_sync parity, install parity, indexer-content parity vs `_agent_content_from_manifest`, deploy-owned partition incl. the `max_run_timeout` canary) + the new MCP-grant-survives-redeploy e2e. These are the strongest guards in the suite — they were what validated the whole approach.

Commit: `refactor(manifest): unify Agent serialization onto the model (Slice 4)`.

---

## Task 16: Invert the round-trip detector framing + full verification

**Files:**
- Modify: `api/tests/roundtrip/paths.py` (comments only — the per-path config is now the model's conformance spec, not a babysitter of 4 writers)
- Modify: `api/tests/e2e/roundtrip/*` (only if a driver references a deleted writer internal — likely none, they call public entry points)
- Test: full suite

**Interfaces:** none new.

- [ ] **Step 1: Confirm no roundtrip driver imports a deleted internal**

Run: `cd api && rg -n 'serialize_|_entries|_content_from_manifest' tests/roundtrip tests/e2e/roundtrip`
Expected: only public-entry references (`generate_manifest`, `bundle_for`, `_import_all_entities`, `_regenerate_manifest_to_dir`) remain. If any test reaches into a deleted field-builder, repoint it to the model method.

- [ ] **Step 2: Update `paths.py` header comment**

Reword the module docstring + the `FIELD_OVERRIDES`/`EXTRA_FIELD_POLICY` comments from "the per-path divergences the 4 writers must obey" to "the per-path config the unified model carries; this file is now the conformance spec for one model, not a drift-babysitter for four writers." No code change.

- [ ] **Step 3: Full backend suite on a clean reset**

Run: `./test.sh stack reset && ./test.sh all`
Expected: green. Parse `/tmp/bifrost-<project>/test-results.xml`. The detector (`tests/e2e/roundtrip/`) + all 15 entity parity/characterization tests + the manifest unit tests (`test_manifest.py`, `test_dto_flags.py`, `test_contract_version.py`) pass.

- [ ] **Step 4: Loop the indexer-split e2e pair 10× (state-pollution guard)**

Run: `for i in $(seq 10); do ./test.sh tests/e2e/roundtrip/test_roundtrip_solution.py -v || break; done`
Expected: 10/10 green (the committing roundtrip tests + cleanup teardown don't leak across runs).

- [ ] **Step 5: pyright + ruff clean across all touched files**

Run: `cd api && pyright && ruff check .`
Expected: 0 errors. (Full pyright, since four service files + the models changed.)

- [ ] **Step 6: Contract-version tripwire**

Run: `./test.sh tests/unit/test_contract_version.py -v`
Expected: PASS. (No DTO shape changed — byte-identical output — so no `CONTRACT_VERSION` bump. If it reds, a serialization shape drifted; that's a BUG this pass, not a bump-and-move-on. Fix the entity.)

- [ ] **Step 7: Commit**

```bash
git add api/tests/roundtrip/paths.py
git commit -m "test(manifest): invert detector framing to model-conformance + full Slice 4 verification"
```

---

## Self-Review (run before handoff)

**Spec coverage:** all 20 manifest models have a conversion task — Organization(3), Role(4), Workflow(5), Table(6), CustomClaim(7), Config(8), Integration+3 nested(9), EventSource+3 nested(10), MCPServer+2 nested(11), SolutionConfigSchema(12), App(13), Form(14), Agent(15). Both halves (view + to_orm_values) covered. Allowlist-only enforced in `_install_view`. Byte-identity enforced by parity→characterization + detector on every task.

**Entities with NO install path** (git-sync only, documented in-task): Organization, Role, Integration (install = connection-schema template, separate shape), MCPServer (no `_mcp_entries` in capture), Config (install = value stream). **Install-only:** SolutionConfigSchema. This asymmetry is real and called out per task so an implementer doesn't invent a missing path.

**Indexer-split entities** (Form, Agent) carry `import_owner` metadata; `to_orm_values` partitions into indexer/direct/restamp; the `max_run_timeout` canary (Slice-2 fix) is explicitly preserved via the restamp/extras seam.

**Type consistency:** `from_row`/`view(Destination)`/`to_orm_values(Destination)->ImportFields` signatures are uniform across all entity tasks; `Destination` enum + `ImportFields` dataclass defined once in Task 1; `assert_parity` defined once in Task 2; `import_owner_of` defined in Task 1.

**Orchestration preserved (not moved):** every import task explicitly KEEPS the resolver/upsert orchestration (natural-key upsert, id realign, role-name resolution, ref remap, Core-vs-ORM writes, read-only guard, indexer delegation, non-destructive child reconciliation). `to_orm_values` only sources field dicts.
