# Manifest Field-Class Contract + Round-Trip Test Harness — Implementation Plan (Phase 1.5) — v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **v2 (2026-06-19): rewritten after a SECOND Codex adversarial pass found 8 code-verified defects in v1 (4 oracle-breaking).** All folded in: (1) Solution pairing is `by_remap` via `solution_entity_id`, NOT natural-key; (2) `pair_rows` skips scrubbed key-parts AND fails on missing/duplicate pairs; (3) Solution policies use `environment:stamp` (not scrub); (4) `ManifestMCPServer` is `_repo`-ONLY (not a Solution entity — `SolutionBundle` has no `mcp_servers`); (5) `sentinel_for` correctly handles `list[X]`/`dict[K,V]`/`Literal`/nested-model containers; (6) the conditional-class predicate is a STRING key + registry, never a callable in `json_schema_extra` (a callable raises `PydanticSerializationError` on schema gen — verified); (7) the `_repo` driver calls `GitHubSyncService._import_all_entities()` (the real wrapper that runs form/agent indexer side-effects), not bare `plan_import`; (8) Task 7 is constrained so it cannot neuter the oracle.

**Goal:** Build field-class metadata on every manifest entity field + a generated round-trip test harness that drives the REAL serialization paths and fails when any field violates its per-path contract — the regression oracle that makes the upcoming convergence refactor safe.

**Architecture:** Per spec `docs/superpowers/specs/2026-06-19-manifest-field-class-contract.md` (v2). A `classify()` helper attaches a `FieldClass` (+ optional `match_key` / `predicate` key / `keep_on_portable`) to each `Manifest*` field via Pydantic `Field(json_schema_extra=...)`. `RoundTripPath` objects drive the REAL `_repo`, shareable, and full-backup paths; generators produce per-entity fixtures; an assertion reads field metadata and checks each field obeys the path's policy. Tripwires enforce every field is tagged and populated with a non-default sentinel.

**Tech Stack:** Python 3.11 / Pydantic 2.13.3 / SQLAlchemy (async) / pytest via `./test.sh`. No new deps.

## Global Constraints

- Work in worktree `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-deadcode-audit` (branch `worktree-solutions-deadcode-audit`). Never edit the primary checkout.
- Tests via `./test.sh` only. Unit filterable by `::name`. E2E runs whole suite; read PER-WORKTREE JUnit at `/tmp/bifrost-<project>/test-results.xml` (`<project>` from `./test.sh stack status`).
- Datetime: always `datetime.now(timezone.utc)`. No dead code / unrequested fallbacks.
- **The harness drives REAL code (spec §8 Risk 2).** `RoundTripPath.run` MUST call the named real entry points (below). NO reimplementation of any path.
- **The contract pins CURRENT behavior (spec §4).** Where a field's class is ambiguous (spec §7), tag to match what the code does today. This harness is not where scrub rules change.
- Metadata mechanism is `Field(**classify(...))` — NOT `typing.Annotated`, NOT a decorator. **No callables in `json_schema_extra`** (breaks pydantic schema gen) — conditional class uses a string `predicate` key resolved through a registry in `field_classes.py`.
- Solution-deploy tests hit the always-on read-only `before_flush` guard; any deploying test installs `install_solution_write_guard()` (autouse fixture).

### Real entry points the drivers MUST call (Codex-pinned — do not reimplement)
- **`_repo` export (DB→manifest):** `src.services.manifest_generator.generate_manifest(db, ...)`.
- **`_repo` import (manifest→DB):** `src.services.github_sync.GitHubSyncService._import_all_entities(...)` — NOT bare `ManifestResolver.plan_import`; the indexer side-effects for inline form/agent CONTENT run in the wrapper (`github_sync.py:1229`). This is the path that drops `auto_fill`, so the harness MUST exercise it.
- **Solution shareable/full export:** the real export-zip builder (`src.services.solutions.export`) → a zip.
- **Solution install:** `src.services.solutions.zip_install.install_zip(...)` → builds a `SolutionBundle` → `SolutionDeployer.deploy(...)`.
- **Solution per-install id remap:** `src.services.solutions.deploy.solution_entity_id(install_id, manifest_id)` = `uuid5(install_id, str(manifest_id))`; `install_id == solution.id`. The harness computes the EXPECTED post-install id with this exact function to pair rows.
- **Secrets envelope:** `src.services.solutions.secrets_blob` (encrypt/decrypt).
- **Connection declarations:** `src.services.solutions.integration_template.build_integration_template` (the scrubbed skeleton).

### The Solution entity set (Codex #4 — authoritative)
`SolutionBundle` carries: workflows, tables, apps, forms, agents, claims, config_schemas, events, connection_schemas. **It does NOT carry `mcp_servers`.** `ManifestMCPServer` / `ManifestMCPConnection` / `ManifestMCPConnectionTool` are `_repo`-manifest entities ONLY; Solutions carry integration *connection declarations* (scrubbed skeletons in `connections.yaml`), a separate envelope (§5.4 of spec). The Solution round-trip tests MUST NOT assert MCP-server entities through the Solution path.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `api/bifrost/field_classes.py` | `FieldClass` + `classify()` + predicate registry + introspection | Create |
| `api/bifrost/manifest.py` | The 20 `Manifest*` models | Tag every field |
| `api/tests/unit/test_field_class_tripwire.py` | Every field tagged; metadata valid; schema still generates | Create |
| `api/tests/roundtrip/__init__.py` | marker | Create |
| `api/tests/roundtrip/generators.py` | per-entity generators (correct container/Literal/nested handling) + completeness | Create |
| `api/tests/roundtrip/paths.py` | `RoundTripPath` drivers over REAL code + policies + pairing | Create |
| `api/tests/roundtrip/assertions.py` | policy assertion + canonical-JSON + leak + strict pair_rows | Create |
| `api/tests/roundtrip/test_generator_completeness.py` | sentinel-completeness tripwire | Create |
| `api/tests/roundtrip/test_roundtrip_repo.py` | `_repo` round trips | Create |
| `api/tests/roundtrip/test_roundtrip_solution.py` | shareable + full + envelopes | Create |

---

## Task 1: Mechanism — `field_classes.py` (with string-key predicate registry) + tripwire

**Files:** Create `api/bifrost/field_classes.py`, `api/tests/unit/test_field_class_tripwire.py`.

**Interfaces:**
- Produces: `FieldClass`; `classify(field_class, *, match_key=False, predicate=None, keep_on_portable=False) -> dict` (where `predicate` is a STRING key registered in `PREDICATES`); `PREDICATES: dict[str, Callable[[Any], FieldClass]]`; `iter_manifest_models()`; `field_class_of(model, field, row=None)`; `match_keys(model)`; `is_keep_on_portable(model, field)`.

- [ ] **Step 1: Write `field_classes.py`** (predicate is a string key — NEVER a callable in the field metadata)

```python
"""Field-class metadata for Manifest* models + introspection.

A field's class declares its round-trip behavior (see the spec). Carried in Pydantic
Field(json_schema_extra=...). CONDITIONAL classes (e.g. Config.value is secret only when
config_type=='secret') use a STRING predicate key resolved through PREDICATES — never a
callable in json_schema_extra, which breaks pydantic schema generation."""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel


class FieldClass(str, Enum):
    IDENTITY = "identity"
    CONTENT = "content"
    ENVIRONMENT = "environment"
    SECRET = "secret"
    REFERENCE = "reference"


def _config_value_class(row: Any) -> FieldClass:
    ct = getattr(row, "config_type", None) if not isinstance(row, dict) else row.get("config_type")
    return FieldClass.SECRET if ct in ("secret",) else FieldClass.CONTENT


# String key -> resolver. The ONLY place callables live. Add new conditionals here.
PREDICATES: dict[str, Callable[[Any], FieldClass]] = {
    "config_value": _config_value_class,
}


def classify(
    field_class: FieldClass,
    *,
    match_key: bool = False,
    predicate: str | None = None,
    keep_on_portable: bool = False,
) -> dict:
    extra: dict[str, Any] = {"bifrost_field_class": field_class.value}
    if match_key:
        extra["bifrost_match_key"] = True
    if keep_on_portable:
        extra["bifrost_keep_on_portable"] = True
    if predicate is not None:
        assert predicate in PREDICATES, f"unknown predicate key {predicate!r}"
        extra["bifrost_class_predicate"] = predicate  # a STRING, schema-safe
    return {"json_schema_extra": extra}


def iter_manifest_models() -> list[type[BaseModel]]:
    import bifrost.manifest as _m  # lazy — avoid import cycle
    out = []
    for name in dir(_m):
        obj = getattr(_m, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and name.startswith("Manifest") and name != "Manifest":
            out.append(obj)
    return out


def _extra(model: type[BaseModel], field: str) -> dict:
    return model.model_fields[field].json_schema_extra or {}  # type: ignore[return-value]


def field_class_of(model: type[BaseModel], field: str, row: Any | None = None) -> FieldClass:
    extra = _extra(model, field)
    pred_key = extra.get("bifrost_class_predicate")
    if pred_key is not None and row is not None:
        return PREDICATES[pred_key](row)
    return FieldClass(extra["bifrost_field_class"])


def match_keys(model: type[BaseModel]) -> tuple[str, ...]:
    return tuple(f for f in model.model_fields if _extra(model, f).get("bifrost_match_key"))


def is_keep_on_portable(model: type[BaseModel], field: str) -> bool:
    return bool(_extra(model, field).get("bifrost_keep_on_portable"))
```

- [ ] **Step 2: Failing tripwire** — `api/tests/unit/test_field_class_tripwire.py`:

```python
"""Every Manifest* field MUST be classified; metadata must be schema-safe."""
import pytest
from bifrost.field_classes import FieldClass, iter_manifest_models


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_every_field_is_classified(model):
    missing = [f for f in model.model_fields if "bifrost_field_class" not in (model.model_fields[f].json_schema_extra or {})]
    assert not missing, f"{model.__name__} untagged: {missing}"


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_metadata_is_schema_safe(model):
    # A callable left in json_schema_extra raises PydanticSerializationError here.
    model.model_json_schema()
```

- [ ] **Step 3: Run — FAIL** (`test_every_field_is_classified` red; nothing tagged). `./test.sh tests/unit/test_field_class_tripwire.py -v`.

- [ ] **Step 4: Commit**
```bash
git add api/bifrost/field_classes.py api/tests/unit/test_field_class_tripwire.py
git commit -m "feat(manifest): field-class mechanism (string-key predicate registry) + tripwire"
```

---

## Task 2: Tag all 20 manifest entities (per spec §6; MCP entities tagged but Solution-excluded)

**Files:** Modify `api/bifrost/manifest.py`. Tripwire flips green.

**§7 tags (code-current defaults; flagged for override):** state flags `is_active`/`schedule_enabled`→ENVIRONMENT, `endpoint_enabled`/`public_endpoint`→CONTENT; `Config.value`→`classify(FieldClass.CONTENT, predicate="config_value")` (base CONTENT, predicate promotes to SECRET when config_type==secret); `oauth_token_id`/`service_oauth_token_id`/`client_id`→REFERENCE; blob fields→CONTENT; deprecated `path`→CONTENT; nested containers→`classify(FieldClass.CONTENT)` (recurse asserts children).

- [ ] **Step 1: Add `from bifrost.field_classes import FieldClass, classify` and tag every field per spec §6.** Workflow match keys = `path`+`function_name` (both `match_key=True`); Config = `key`+`integration_id`+`organization_id`; App=`slug`; Table=`name`+`organization_id`; Claim=`name`+`organization_id`; Integration=`name`; nested keys (IntegrationConfigSchema.key, MCPConnectionTool.tool_name) `match_key=True`. Form/Agent/EventSource/MCPServer carry NO `match_key` (id-only). `role_names` carries `keep_on_portable=True`.

- [ ] **Step 2: Tripwire green + schema-safe.** `./test.sh tests/unit/test_field_class_tripwire.py -v` → PASS (both tests; `model_json_schema()` proves no callable leaked).

- [ ] **Step 3: Add match-key + predicate assertions** to the tripwire file:
```python
def test_known_match_keys():
    from bifrost.field_classes import match_keys
    import bifrost.manifest as m
    assert set(match_keys(m.ManifestWorkflow)) == {"path", "function_name"}
    assert set(match_keys(m.ManifestConfig)) == {"key", "integration_id", "organization_id"}
    assert match_keys(m.ManifestForm) == ()           # id-only
    assert match_keys(m.ManifestMCPServer) == ()       # id-only / _repo-only

def test_config_value_predicate():
    from types import SimpleNamespace as NS
    from bifrost.field_classes import field_class_of, FieldClass
    import bifrost.manifest as m
    assert field_class_of(m.ManifestConfig, "value", NS(config_type="secret")) == FieldClass.SECRET
    assert field_class_of(m.ManifestConfig, "value", NS(config_type="string")) == FieldClass.CONTENT
```
Run → PASS.

- [ ] **Step 4: pyright + ruff + commit**
```bash
cd api && pyright bifrost/manifest.py bifrost/field_classes.py && ruff check bifrost/ && cd ..
git add api/bifrost/manifest.py api/tests/unit/test_field_class_tripwire.py
git commit -m "feat(manifest): tag all 20 entities with field classes + match keys + value predicate"
```

---

## Task 3: Generators (correct container/Literal/nested handling) + completeness tripwire

**Files:** Create `api/tests/roundtrip/__init__.py`, `generators.py`, `test_generator_completeness.py`.

**Codex #5 fixes:** `sentinel_for` must inspect the FULL annotation (handle `list[X]`, `dict[K,V]`, `Literal[...]`, `Optional`, nested `BaseModel`, and `dict[str, ModelType]`), and use a DETERMINISTIC per-(model,field) uuid — NOT `hash()` (process-randomized).

- [ ] **Step 1: Write `generators.py`** (verified type handling):

```python
"""Deterministic fixture generators. No randomness, no Hypothesis."""
from __future__ import annotations
import typing
import uuid
from typing import Any, Literal, get_args, get_origin
from pydantic import BaseModel
from pydantic.fields import FieldInfo

_NS = uuid.UUID("00000000-0000-0000-0000-0000000000ff")  # fixed namespace

def _det_uuid(model_name: str, field: str) -> str:
    return str(uuid.uuid5(_NS, f"{model_name}.{field}"))

def _unwrap_optional(ann):
    if get_origin(ann) is typing.Union:
        args = [a for a in get_args(ann) if a is not type(None)]
        return args[0] if args else ann
    return ann

def sentinel_for(model_name: str, name: str, info: FieldInfo) -> Any:
    if name == "id" or name.endswith("_id"):
        return _det_uuid(model_name, name)
    ann = _unwrap_optional(info.annotation)
    origin = get_origin(ann)
    if ann is bool: return True
    if ann is int: return 4242
    if ann is str: return f"SENT::{model_name}.{name}"
    if origin is Literal:
        return get_args(ann)[0]                      # a VALID literal member
    if origin is list:
        (inner,) = get_args(ann) or (str,)
        inner = _unwrap_optional(inner)
        if get_origin(inner) is Literal:
            return [get_args(inner)[0]]
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return [all_fields_populated(inner)]
        return [f"SENT::{model_name}.{name}.0"]
    if origin is dict:
        kt, vt = (get_args(ann) + (str, str))[:2]
        vt = _unwrap_optional(vt)
        if isinstance(vt, type) and issubclass(vt, BaseModel):
            return {f"SENT_K::{name}": all_fields_populated(vt)}
        return {f"SENT_K::{name}": f"SENT_V::{name}"}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return all_fields_populated(ann)
    return f"SENT::{model_name}.{name}"

def all_fields_populated(model: type[BaseModel]) -> dict:
    return {n: sentinel_for(model.__name__, n, i) for n, i in model.model_fields.items()}

def each_field_isolated(model: type[BaseModel]) -> list[dict]:
    base = {n: sentinel_for(model.__name__, n, i) for n, i in model.model_fields.items() if i.is_required()}
    out = []
    for n, i in model.model_fields.items():
        f = dict(base); f[n] = sentinel_for(model.__name__, n, i); out.append(f)
    return out
```

- [ ] **Step 2: Completeness tripwire** — `test_generator_completeness.py`:

```python
"""Risk-1: generator sets a non-default sentinel for EVERY field, and the result validates."""
import pytest
from bifrost.field_classes import iter_manifest_models
from tests.roundtrip.generators import all_fields_populated

@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_all_fields_populated(model):
    fx = all_fields_populated(model)
    assert set(fx) == set(model.model_fields), f"{model.__name__} missing {set(model.model_fields)-set(fx)}"
    model.model_validate(fx)   # alias-aware: uses populate_by_name where set
```

- [ ] **Step 3: Run — green for ALL 20.** `./test.sh tests/roundtrip/test_generator_completeness.py -v`. If a model fails validation (e.g. an aliased field like `ManifestTable.table_schema` alias `schema`, or a `ClaimQuery` nested type), fix `sentinel_for`/validation call to handle that type — do NOT skip the field. Note `populate_by_name` models accept the python name; for alias-only models pass `by_alias` appropriately.

- [ ] **Step 4: Commit**
```bash
git add api/tests/roundtrip/__init__.py api/tests/roundtrip/generators.py api/tests/roundtrip/test_generator_completeness.py
git commit -m "feat(roundtrip): per-entity generators (containers/Literal/nested/alias-safe) + completeness tripwire"
```

---

## Task 4: Assertion + canonical-JSON + leak + STRICT pair_rows

**Files:** Create `api/tests/roundtrip/assertions.py`, `test_assertions_unit.py`.

**Codex #2 fixes:** `pair_rows` (a) SKIPS match-key parts the path scrubs/stamps (pass the path's policy so it knows which classes are dropped), and (b) FAILS on a missing or duplicate pair — never silently filters.

- [ ] **Step 1: Write `assertions.py`**:

```python
"""Round-trip assertions. Reads field-class metadata; checks each field obeys the path policy.
Risk-3: canonical JSON for blobs, explicit secret handling, sentinel-leak scan.
Risk-2(pairing): strict pairing that skips scrubbed key-parts and fails on missing/dupes."""
from __future__ import annotations
import json
from typing import Any
from bifrost.field_classes import FieldClass, field_class_of, match_keys

Policy = dict[FieldClass, str]  # action per class: keep | scrub | stamp | remap

def canonical(v: Any) -> str:
    return json.dumps(v, sort_keys=True, default=str)

def assert_field_roundtrip(model, field, before, after, policy: Policy, row) -> None:
    cls = field_class_of(model, field, row)
    action = policy[cls]
    bval, aval = before.get(field), after.get(field)
    if action == "keep":
        assert canonical(aval) == canonical(bval), f"{model.__name__}.{field} ({cls.value}) changed {bval!r}->{aval!r}"
    elif action == "scrub":
        assert aval in (None, [], {}, ""), f"{model.__name__}.{field} ({cls.value}) leaked on scrub: {aval!r}"
    elif action == "stamp":
        assert aval is not None, f"{model.__name__}.{field} ({cls.value}) not stamped"
    elif action == "remap":
        assert (aval is None) == (bval is None), f"{model.__name__}.{field} ({cls.value}) presence changed on remap"
    else:
        raise AssertionError(f"unknown action {action!r}")

def assert_no_secret_leak(text: str, sentinels: list[str]) -> None:
    for s in sentinels:
        assert s not in text, f"secret sentinel {s!r} leaked into a non-secret envelope"

def _surviving_key(model, policy: Policy):
    """Match-key fields whose class the path does NOT scrub/stamp (Codex #2: org is stamped on
    solution paths -> excluded so we match on the stable parts)."""
    out = []
    for f in match_keys(model):
        cls = field_class_of(model, f)            # static class (predicate not needed for keys)
        if policy.get(cls) in ("scrub", "stamp"):
            continue
        out.append(f)
    return tuple(out)

def pair_rows(model, before_rows, after_rows, strategy: str, policy: Policy,
              expected_id=None) -> list[tuple[dict, dict]]:
    """STRICT: returns one pair per before-row; raises on missing or duplicate matches.
    strategy: 'by_id' (_repo) | 'by_remap' (solution; expected_id(before)->post-install id) |
              'by_match_key' (only where a stable natural key exists)."""
    pairs = []
    if strategy == "by_id":
        idx = _index(after_rows, lambda r: r["id"], model)
        for b in before_rows:
            pairs.append((b, _take(idx, b["id"], model, b)))
    elif strategy == "by_remap":
        assert expected_id is not None, "by_remap needs expected_id(before)->id"
        idx = _index(after_rows, lambda r: r["id"], model)
        for b in before_rows:
            pairs.append((b, _take(idx, expected_id(b), model, b)))
    elif strategy == "by_match_key":
        keys = _surviving_key(model, policy)
        assert keys, f"{model.__name__}: no surviving match key under this policy"
        kf = lambda r: tuple(r.get(f) for f in keys)
        idx = _index(after_rows, kf, model)
        for b in before_rows:
            pairs.append((b, _take(idx, kf(b), model, b)))
    else:
        raise AssertionError(f"unknown strategy {strategy!r}")
    return pairs

def _index(rows, keyfn, model):
    idx: dict[Any, list] = {}
    for r in rows:
        idx.setdefault(keyfn(r), []).append(r)
    return idx

def _take(idx, key, model, before):
    hits = idx.get(key, [])
    assert len(hits) == 1, f"{model.__name__}: expected exactly 1 match for {key!r}, got {len(hits)} (before id={before.get('id')!r})"
    return hits[0]
```

- [ ] **Step 2: Unit test the assertion + STRICT pairing** — `test_assertions_unit.py` (must include: keep-pass, keep-fail-on-drop, scrub-pass, leak-detector, canonical-order, **pair_rows raises on missing match**, **pair_rows raises on duplicate**, **by_match_key skips a stamped org part**). Run → PASS.

- [ ] **Step 3: Commit**
```bash
git add api/tests/roundtrip/assertions.py api/tests/roundtrip/test_assertions_unit.py
git commit -m "feat(roundtrip): policy assertion + canonical-JSON + leak + STRICT pair_rows (skip scrubbed key-parts, fail on missing/dupe)"
```

---

## Task 5: `_repo` git-sync round-trip (drives generate_manifest + GitHubSyncService._import_all_entities)

**Codex #7 fix:** import side runs through `GitHubSyncService._import_all_entities` (the wrapper that runs the form/agent indexers — where `auto_fill` is dropped), NOT bare `plan_import`.

**Files:** Create `paths.py` (`REPO_SYNC` policy + real-code wrappers), `test_roundtrip_repo.py`.

**`REPO_POLICY`** = `{identity:keep, content:keep, environment:keep, secret:scrub, reference:keep}`, pairing `by_id`.

- [ ] **Step 1: `paths.py`** — `RoundTripPath` dataclass + `REPO_SYNC` constants + thin wrappers that call `generate_manifest` and `GitHubSyncService._import_all_entities`. (Read both real signatures first; wire them. No reimplementation.)

- [ ] **Step 2: `test_roundtrip_repo.py`** — seed each entity with `all_fields_populated`, run real export→import, read back, pair `by_id`, `assert_field_roundtrip` per field with `REPO_POLICY` (predicate rows passed for `Config.value`). Start with Workflow to prove wiring; then parametrize over `iter_manifest_models()`. `@pytest.mark.e2e`. Include the **secret-leak check**: the generated `_repo` manifest text must not contain a secret sentinel.

- [ ] **Step 3: Run (e2e) — reds are REAL findings.** `./test.sh e2e`; read per-worktree XML. Record every `(model, field)` red. Do NOT loosen assertions. (`auto_fill` is expected red here — that's the harness working.)

- [ ] **Step 4: Parametrize + commit** with findings documented in the report.
```bash
git add api/tests/roundtrip/paths.py api/tests/roundtrip/test_roundtrip_repo.py
git commit -m "feat(roundtrip): _repo round-trip over real generate_manifest + GitHubSyncService import"
```

---

## Task 6: Solution shareable + full-backup (by_remap pairing) + the envelopes

**Codex #1/#3/#4 fixes baked in:** pairing is `by_remap` (compute `solution_entity_id(solution.id, manifest_id)`); policies use `environment:stamp`; secret scrubbed from the manifest on BOTH solution modes; MCP-server entities are NOT asserted through the solution path.

**Files:** Modify `paths.py` (add solution policies + real export/install wrappers), create `test_roundtrip_solution.py`.

- **`SOLUTION_SHAREABLE`** = `{identity:remap, content:keep, environment:stamp, secret:scrub, reference:remap}`, pairing `by_remap`. (`keep_on_portable` env fields — `role_names` — are the one exception: asserted `keep`, checked separately.)
- **`SOLUTION_FULL`** = same policy for the MANIFEST envelope (env stamped, secret scrubbed from manifest); secrets travel only in the encrypted envelope (separate check).

- [ ] **Step 1: Add solution policies + real wrappers to `paths.py`** — wrappers call: real export-zip builder → `zip_install.install_zip` → `SolutionDeployer.deploy`; `secrets_blob` for the full-mode secret envelope; `build_integration_template` for connection decls. Provide `expected_id(before) = solution_entity_id(solution.id, UUID(before["id"]))` for `by_remap`.

- [ ] **Step 2: Shareable round-trip test** — seed the Solution-entity subset ONLY (workflows/tables/apps/forms/agents/claims/config_schemas/events/connection_schemas — NOT mcp_servers), real shareable export→install into a fresh target org, read back, pair `by_remap`, assert `SOLUTION_SHAREABLE`. Autouse write-guard. **Leak check:** the shareable zip's manifest text contains NO secret sentinel. **Env-stamp check:** `organization_id` on imported rows == target org (not the source).

- [ ] **Step 3: Full-backup + envelope checks** — full export (password)→install→read back, assert `SOLUTION_FULL` (manifest env stamped; manifest secret scrubbed). Plus:
  - `table_data`: seed table rows, full export `include_data=True`→install→rows match; AND shareable export carries NO rows.
  - `secret envelope`: a secret config value survives full encrypt→decrypt; absent from shareable/`_repo` outputs (leak check).
  - `connection declaration`: a declared integration exports a skeleton via `build_integration_template` with NO client_id/secret/token/org; assert those absent, schema shape present.

- [ ] **Step 4: Run (e2e), triage reds, commit** with findings documented.
```bash
git add api/tests/roundtrip/paths.py api/tests/roundtrip/test_roundtrip_solution.py
git commit -m "feat(roundtrip): solution shareable + full (by_remap) + table_data/secret/connection-decl envelopes"
```

---

## Task 7: Triage + fix the drops — CONSTRAINED so it cannot neuter the oracle

**Codex #8 guardrails (binding):**
- A `content` or `reference` field that shows up dropped **MUST be fixed in the writer/importer** — it may NOT be relabeled to `environment`/`secret` to silence the red.
- A tag/policy change is permitted ONLY to correct a genuine mis-model, and ONLY with (a) a one-line spec citation of the CURRENT code behavior proving the scrub/stamp is intentional, AND (b) the spec §6 row updated in the same commit. No silent downgrades.
- Pairing/assertion code may not be weakened; any change there must ADD a test that fails on an omitted/duplicate pair.
- Every class-B (real) drop is fixed in the serializer/resolver/deploy values, mirroring the Phase-1 `tool_description` fix.

**Files:** the writer/importer/model per each red; tags only under the guardrail above.

- [ ] **Step 1: Enumerate reds** from Tasks 5-6 into the report; classify A (mis-model) vs B (real drop), each with a code citation.
- [ ] **Step 2: Fix class-A** under the guardrail (spec citation + §6 update in the commit). Re-run.
- [ ] **Step 3: Fix class-B real drops one at a time** in the writer path; re-run harness after each. (Includes `auto_fill`, agent-delegation order, `max_run_timeout`/`event_type`/`display_name` parity — the prior plan's deferred checklist, now failing tests.)
- [ ] **Step 4: Full harness green + sweep** — `cd api && pyright && ruff check . && cd .. && ./test.sh all`. Document every fix.
- [ ] **Step 5: Commit**
```bash
git add -A
git commit -m "fix(manifest): close field drops the round-trip harness surfaced (constrained — writers fixed, no oracle downgrades)"
```

---

## Task 8: Final verification + prove the tripwires bite

- [ ] **Step 1: Full sweep** — `cd api && pyright && ruff check . && cd .. && ./test.sh all` + `./test.sh client unit` (no client changes; confirm clean).
- [ ] **Step 2: Meta-check (scratch, NOT committed):** (a) drop one field's `**classify(...)` → confirm `test_every_field_is_classified` fails; (b) put a callable in one field's extra → confirm `test_metadata_is_schema_safe` fails; (c) remove one field from a generator → confirm `test_all_fields_populated` fails; (d) make `pair_rows` return on a missing match → confirm an assertion test fails. Revert all four. Document — this proves the net is armed.
- [ ] **Step 3: Mark plan complete + commit.**
```bash
git add docs/superpowers/plans/2026-06-19-manifest-roundtrip-harness-phase1.5.md
git commit -m "docs(plan): mark round-trip harness Phase 1.5 complete"
```

---

## Self-Review notes
- **Codex v1 (spec) corrections** → spec v2 §3/§4/§5. **Codex v2 (plan) corrections** → all 8 folded here: remap pairing (Task 6 `by_remap`); strict pair_rows skip-scrubbed-key + fail-on-missing (Task 4); env:stamp policies (Task 6); MCPServer Solution-excluded (Global Constraints "Solution entity set" + Task 6 Step 2); generator container/Literal/nested/alias handling (Task 3); string-key predicate registry, no callable in extra (Task 1, +schema-safe tripwire test); real `_import_all_entities` entry point (Task 5); constrained Task 7.
- **Risk defenses:** Risk-1 = completeness tripwire (Task 3); Risk-2 = "drives REAL code" + named entry points (Global Constraints) + strict pairing (Task 4); Risk-3 = canonical JSON + secret-decrypt + sentinel-leak (Tasks 4/6).
- **Out of scope:** the convergence refactor; changing scrub rules; adding natural-key matchers to id-only entities (convergence decision, spec §10).
