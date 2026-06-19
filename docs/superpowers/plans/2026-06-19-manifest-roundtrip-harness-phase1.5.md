# Manifest Field-Class Contract + Round-Trip Test Harness — Implementation Plan (Phase 1.5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build field-class metadata on every manifest entity field + a generated round-trip test harness that drives the REAL serialization paths and fails when any field violates its per-path contract — the regression oracle that makes the upcoming convergence refactor safe.

**Architecture:** Per the spec `docs/superpowers/specs/2026-06-19-manifest-field-class-contract.md` (v2, Codex-reviewed). A `classify()` helper attaches a `FieldClass` (+ optional `match_key`/`when=`/`keep_on_portable`) to each `Manifest*` field via Pydantic `Field(json_schema_extra=...)`. `RoundTripPath` objects drive the real `_repo`, shareable, and full-backup paths; generators produce per-entity fixtures; an assertion reads the field metadata and checks each field obeys the path's policy. Tripwires enforce that every field is tagged and every field is populated with a non-default sentinel.

**Tech Stack:** Python 3.11 / Pydantic / SQLAlchemy (async) / pytest via `./test.sh` (Dockerised). No new deps (no Hypothesis).

## Global Constraints

- Work in the worktree `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-deadcode-audit` (branch `worktree-solutions-deadcode-audit`). Never edit the primary checkout.
- Tests via `./test.sh` only — never raw pytest. Unit filterable by `::name`. E2E (`./test.sh e2e`) runs the whole suite; read the PER-WORKTREE JUnit XML at `/tmp/bifrost-<project>/test-results.xml` (get `<project>` from `./test.sh stack status`) — NOT `/tmp/bifrost/`.
- Datetime: always `datetime.now(timezone.utc)`.
- No dead code / no unrequested fallbacks (CLAUDE.md).
- **The harness drives REAL code** (spec §8 Risk 2): `RoundTripPath.run` MUST call `generate_manifest` / `ManifestResolver` / the real `_collect_*` + `deploy` / the real export-zip + decrypt + install. NO reimplementation of a path inside the test.
- **The contract is what the code does TODAY** (spec §4). This harness pins CURRENT behavior; it is not where we change scrub rules. Where a field's class is ambiguous (spec §7), tag it to match what the code does now; a wrong-but-current tag is fixed when the harness goes red against an intended behavior, not pre-emptively.
- Field metadata mechanism is `Field(**classify(...))` (spec §2) — NOT `typing.Annotated`, NOT a decorator.
- Solution-deploy paths hit the always-on read-only `before_flush` guard in prod; any test that deploys MUST install `install_solution_write_guard()` (autouse fixture) to be prod-faithful.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `api/bifrost/field_classes.py` | `FieldClass` enum + `classify()` + introspection (`field_class_of`, `match_keys`, `iter_manifest_models`) | Create |
| `api/bifrost/manifest.py` | The 20 `Manifest*` models | Add `**classify(...)` to every field |
| `api/tests/unit/test_field_class_tripwire.py` | Every field tagged + valid metadata | Create |
| `api/tests/roundtrip/__init__.py` | Package marker | Create |
| `api/tests/roundtrip/generators.py` | Per-entity fixture generators (all-populated / isolated / tricky) + completeness | Create |
| `api/tests/roundtrip/paths.py` | `RoundTripPath` drivers over REAL code (repo/shareable/full) + envelope checks | Create |
| `api/tests/roundtrip/assertions.py` | `assert_roundtrip(model, before, after, path)` + canonical-JSON + leak check | Create |
| `api/tests/roundtrip/test_generator_completeness.py` | Sentinel-completeness tripwire | Create |
| `api/tests/roundtrip/test_roundtrip_repo.py` | `_repo` git-sync round trips | Create |
| `api/tests/roundtrip/test_roundtrip_solution.py` | shareable + full-backup round trips + envelopes | Create |

---

## Task 1: The mechanism — `field_classes.py` + the tagging tripwire

Create the metadata vocabulary and a tripwire that fails until every manifest field is tagged. The tripwire is written FIRST and is expected to FAIL (no fields tagged yet) — it goes green only after Task 2.

**Files:**
- Create: `api/bifrost/field_classes.py`
- Create: `api/tests/unit/test_field_class_tripwire.py`

**Interfaces:**
- Produces: `FieldClass` (enum: IDENTITY/CONTENT/ENVIRONMENT/SECRET/REFERENCE); `classify(field_class, *, match_key=False, when=None, keep_on_portable=False) -> dict`; `iter_manifest_models() -> list[type[BaseModel]]`; `field_class_of(model, field_name, row=None) -> FieldClass`; `match_keys(model) -> tuple[str, ...]`.

- [ ] **Step 1: Write `field_classes.py`**

```python
"""Field-class metadata for Manifest* models + introspection helpers.

A field's class declares how it behaves across the serialization round-trip paths
(see docs/superpowers/specs/2026-06-19-manifest-field-class-contract.md). Carried in
the Pydantic Field() via json_schema_extra; read back through model_fields.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable

import bifrost.manifest as _manifest_mod
from pydantic import BaseModel


class FieldClass(str, Enum):
    IDENTITY = "identity"
    CONTENT = "content"
    ENVIRONMENT = "environment"
    SECRET = "secret"
    REFERENCE = "reference"


def classify(
    field_class: FieldClass,
    *,
    match_key: bool = False,
    when: Callable[[Any], FieldClass] | None = None,
    keep_on_portable: bool = False,
) -> dict:
    """Return a Field(json_schema_extra=...) payload tagging a manifest field."""
    extra: dict[str, Any] = {"bifrost_field_class": field_class.value}
    if match_key:
        extra["bifrost_match_key"] = True
    if keep_on_portable:
        extra["bifrost_keep_on_portable"] = True
    if when is not None:
        extra["bifrost_class_predicate"] = when
    return {"json_schema_extra": extra}


def iter_manifest_models() -> list[type[BaseModel]]:
    """Every Manifest* pydantic model in bifrost.manifest, EXCEPT the top-level
    Manifest container (which holds only lists of the others)."""
    out: list[type[BaseModel]] = []
    for name in dir(_manifest_mod):
        obj = getattr(_manifest_mod, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and name.startswith("Manifest")
            and name != "Manifest"
        ):
            out.append(obj)
    return out


def _extra(model: type[BaseModel], field_name: str) -> dict:
    info = model.model_fields[field_name]
    return info.json_schema_extra or {}  # type: ignore[return-value]


def field_class_of(model: type[BaseModel], field_name: str, row: Any | None = None) -> FieldClass:
    """Resolve a field's class, calling its when= predicate against `row` if present."""
    extra = _extra(model, field_name)
    pred = extra.get("bifrost_class_predicate")
    if pred is not None and row is not None:
        return pred(row)
    return FieldClass(extra["bifrost_field_class"])


def match_keys(model: type[BaseModel]) -> tuple[str, ...]:
    """Field names flagged match_key=True (the natural key for Solution-install pairing)."""
    return tuple(
        f for f in model.model_fields if _extra(model, f).get("bifrost_match_key")
    )
```

- [ ] **Step 2: Write the failing tripwire test**

Create `api/tests/unit/test_field_class_tripwire.py`:

```python
"""Every Manifest* field MUST carry a field-class tag. Prevents silent drift when
new fields are added (the convergence safety net depends on total coverage)."""
import pytest

from bifrost.field_classes import FieldClass, field_class_of, iter_manifest_models


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_every_field_is_classified(model):
    missing = []
    for fname in model.model_fields:
        extra = model.model_fields[fname].json_schema_extra or {}
        if "bifrost_field_class" not in extra:
            missing.append(fname)
    assert not missing, f"{model.__name__} has untagged fields: {missing}"


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_field_class_values_are_valid(model):
    for fname in model.model_fields:
        extra = model.model_fields[fname].json_schema_extra or {}
        if "bifrost_field_class" in extra:
            FieldClass(extra["bifrost_field_class"])  # raises if not a valid class
```

- [ ] **Step 3: Run to verify it fails**

Run: `./test.sh tests/unit/test_field_class_tripwire.py -v`
Expected: FAIL — every model reports untagged fields (nothing tagged yet). This proves the tripwire works.

- [ ] **Step 4: Commit**

```bash
git add api/bifrost/field_classes.py api/tests/unit/test_field_class_tripwire.py
git commit -m "feat(manifest): field-class mechanism + tagging tripwire (red until tagged)"
```

---

## Task 2: Tag all 20 manifest entities

Apply `**classify(...)` to every field of every `Manifest*` model per spec §6. This is mechanical but large; do it in one task (one file) so the tripwire flips green atomically. Use the EXACT classifications in spec §6, with the §7-flagged rows tagged to the recommended (code-matching) call noted below.

**Files:**
- Modify: `api/bifrost/manifest.py` (every `Manifest*` model)
- (test: the Task-1 tripwire flips green)

**Interfaces:**
- Consumes: `classify`, `FieldClass` from `bifrost.field_classes`.
- Produces: every manifest field carries `bifrost_field_class`; match-key fields carry `bifrost_match_key`; `Config.value` carries a `when=` predicate; `role_names` carries `keep_on_portable`.

**§7 ambiguous rows — tag as follows (code-matching defaults; flagged for plan-review override):**
- State flags `is_active`/`schedule_enabled` → `ENVIRONMENT`; `endpoint_enabled`/`public_endpoint` → `CONTENT`.
- `Config.value` → `classify(FieldClass.SECRET, when=lambda row: FieldClass.SECRET if getattr(row, "config_type", None) in ("secret",) else FieldClass.CONTENT)`.
- `oauth_token_id` / `service_oauth_token_id` → `REFERENCE` (code emits the id; not a plaintext secret).
- `client_id` → `REFERENCE`.
- Blob fields (`webhook_config`/`discovery_metadata`/`table_schema`/`query`/`app_model`/`tool_schema`/`default_launch_params`/`form_schema`) → `CONTENT`.
- Deprecated `path` (Workflow/Form/Agent) → `CONTENT`.
- Nested container fields (`config_schema`/`oauth_provider`/`mappings`/`subscriptions`/`policies`/`tools`/`connections`) → `classify(FieldClass.CONTENT)` (structural; real assertion recurses into children).

- [ ] **Step 1: Tag every model per spec §6**

For each `Manifest*` model, add `**classify(...)` to each field's `Field(...)`. Import at top: `from bifrost.field_classes import FieldClass, classify`. Example (the import must not create a cycle — `field_classes` imports `manifest` lazily inside `iter_manifest_models`, so tag fields by importing `classify`/`FieldClass` which do NOT import manifest at module load):

```python
class ManifestWorkflow(BaseModel):
    id: str = Field(description="Agent UUID", **classify(FieldClass.IDENTITY))
    name: str = Field(default="", description="Agent display name", **classify(FieldClass.CONTENT))
    path: str = Field(default="", description="...", **classify(FieldClass.CONTENT, match_key=True))
    function_name: str = Field(..., **classify(FieldClass.CONTENT, match_key=True))
    organization_id: str | None = Field(default=None, **classify(FieldClass.ENVIRONMENT))
    role_names: list[str] | None = Field(default=None, **classify(FieldClass.ENVIRONMENT, keep_on_portable=True))
    # ...every remaining field tagged per §6
```

> CYCLE NOTE: `field_classes.py` imports `bifrost.manifest` at module top for `iter_manifest_models`. `manifest.py` will import `classify`/`FieldClass` from `field_classes`. To avoid a circular import, move the `import bifrost.manifest as _manifest_mod` INSIDE `iter_manifest_models()` (lazy), so `field_classes` has no module-load dependency on `manifest`. Apply that change in this task.

- [ ] **Step 2: Run the tripwire — now green**

Run: `./test.sh tests/unit/test_field_class_tripwire.py -v`
Expected: PASS for all 20 models (every field tagged, all values valid).

- [ ] **Step 3: Sanity-check match keys + predicate**

Run: `./test.sh tests/unit/test_field_class_tripwire.py -v` then a quick assertion file or `python -c` inside the api container is NOT needed; instead add two assertions to the tripwire test file:

```python
def test_known_match_keys():
    from bifrost.field_classes import match_keys
    import bifrost.manifest as m
    assert set(match_keys(m.ManifestWorkflow)) == {"path", "function_name"}
    assert set(match_keys(m.ManifestConfig)) == {"key", "integration_id", "organization_id"}
    assert match_keys(m.ManifestForm) == ()  # id-only entity, no natural-key matcher

def test_config_value_predicate_resolves():
    from types import SimpleNamespace
    from bifrost.field_classes import field_class_of, FieldClass
    import bifrost.manifest as m
    secret_row = SimpleNamespace(config_type="secret")
    plain_row = SimpleNamespace(config_type="string")
    assert field_class_of(m.ManifestConfig, "value", secret_row) == FieldClass.SECRET
    assert field_class_of(m.ManifestConfig, "value", plain_row) == FieldClass.CONTENT
```

Run again; expected PASS.

- [ ] **Step 4: Pyright + ruff + commit**

```bash
cd api && pyright bifrost/manifest.py bifrost/field_classes.py && ruff check bifrost/ && cd ..
git add api/bifrost/manifest.py api/bifrost/field_classes.py api/tests/unit/test_field_class_tripwire.py
git commit -m "feat(manifest): tag all 20 entities with field classes + match keys + value predicate"
```

---

## Task 3: Generators + the completeness tripwire

Per-entity fixture builders and the Risk-1 defense: a tripwire asserting the generator sets a non-default sentinel for EVERY field, so a newly-added untagged-or-unpopulated field fails loudly.

**Files:**
- Create: `api/tests/roundtrip/__init__.py` (empty)
- Create: `api/tests/roundtrip/generators.py`
- Create: `api/tests/roundtrip/test_generator_completeness.py`

**Interfaces:**
- Produces: `all_fields_populated(model) -> dict` (every field a non-default sentinel; nested children built recursively); `each_field_isolated(model) -> list[dict]` (one dict per field set, rest at model defaults); `sentinel_for(field_info) -> Any` (type-appropriate non-default value).
- Consumes: `iter_manifest_models`, `match_keys` from `bifrost.field_classes`.

- [ ] **Step 1: Write `generators.py`**

```python
"""Deterministic fixture generators for manifest entities. No randomness, no Hypothesis.

sentinel_for() returns a non-default, type-appropriate value so a field left at its
default is visibly distinct from a field the round-trip dropped (Risk-1 defense)."""
from __future__ import annotations

import uuid
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

# Deterministic UUIDs (no Math.random / Date — pure)
def _uuid(seed: int) -> str:
    return str(uuid.UUID(int=seed))


def sentinel_for(name: str, info: FieldInfo) -> Any:
    """A non-default value matching the field's annotation. Distinct per field name
    so a mis-paired field is caught."""
    ann = info.annotation
    origin = get_origin(ann)
    # Optional[X] -> X
    args = [a for a in get_args(ann) if a is not type(None)]
    base = args[0] if origin is not None and args else ann
    if base is bool:
        return True
    if base is int:
        return 4242
    if base is str:
        return f"SENT::{name}"
    if get_origin(base) in (list,) or base is list:
        inner = get_args(base)
        if inner and isinstance(inner[0], type) and issubclass(inner[0], BaseModel):
            return [all_fields_populated(inner[0], salt=1)]
        return [f"SENT::{name}::0"]
    if get_origin(base) in (dict,) or base is dict:
        return {f"SENT_K::{name}": f"SENT_V::{name}"}
    if isinstance(base, type) and issubclass(base, BaseModel):
        return all_fields_populated(base, salt=1)
    # fallback: stringify
    return f"SENT::{name}"


def all_fields_populated(model: type[BaseModel], salt: int = 0) -> dict:
    """Every field set to a non-default sentinel. id/uuid-looking fields get a real UUID."""
    out: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        if name in ("id",) or name.endswith("_id"):
            out[name] = _uuid(1000 + salt + hash(name) % 9000)
        else:
            out[name] = sentinel_for(name, info)
    return out


def each_field_isolated(model: type[BaseModel]) -> list[dict]:
    """One fixture per field: that field at a sentinel, the rest at model defaults
    (or minimal required)."""
    base = _minimal_required(model)
    fixtures = []
    for name, info in model.model_fields.items():
        f = dict(base)
        f[name] = sentinel_for(name, info) if not (name == "id" or name.endswith("_id")) else _uuid(7000 + hash(name) % 2000)
        fixtures.append(f)
    return fixtures


def _minimal_required(model: type[BaseModel]) -> dict:
    out: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        if info.is_required():
            out[name] = sentinel_for(name, info) if not (name == "id" or name.endswith("_id")) else _uuid(5000 + hash(name) % 2000)
    return out
```

- [ ] **Step 2: Write the completeness tripwire**

Create `api/tests/roundtrip/test_generator_completeness.py`:

```python
"""Risk-1 defense: the generator must set a NON-DEFAULT sentinel for every field, so
a dropped field is distinguishable from a defaulted one. A new field the generator
doesn't populate fails here."""
import pytest

from bifrost.field_classes import iter_manifest_models
from tests.roundtrip.generators import all_fields_populated


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_all_fields_populated_covers_every_field(model):
    fixture = all_fields_populated(model)
    missing = [f for f in model.model_fields if f not in fixture]
    assert not missing, f"{model.__name__}: generator did not populate {missing}"
    # And every populated value validates against the model
    model.model_validate(fixture)
```

- [ ] **Step 3: Run — green**

Run: `./test.sh tests/roundtrip/test_generator_completeness.py -v`
Expected: PASS for all 20 models. If a model fails validation, fix `sentinel_for` for that field's type (do NOT special-case the field away — the point is total coverage).

- [ ] **Step 4: Commit**

```bash
git add api/tests/roundtrip/__init__.py api/tests/roundtrip/generators.py api/tests/roundtrip/test_generator_completeness.py
git commit -m "feat(roundtrip): per-entity generators + completeness tripwire (Risk-1 defense)"
```

---

## Task 4: The assertion + canonical-JSON + leak check

The oracle that reads field metadata and checks a before/after pair against a path's policy, plus the Risk-3 defenses.

**Files:**
- Create: `api/tests/roundtrip/assertions.py`

**Interfaces:**
- Produces: `canonical(value) -> str` (sorted-key JSON for blob comparison); `assert_field_roundtrip(model, field, before, after, policy, row)`; `assert_no_secret_leak(serialized_text, sentinels)`; `pair_rows(model, before_rows, after_rows, strategy) -> list[tuple]`.
- Consumes: `field_class_of`, `match_keys`, `FieldClass`.

- [ ] **Step 1: Write `assertions.py`**

```python
"""Round-trip assertions. Reads field-class metadata and checks each field obeys the
path's per-class policy (keep / scrub / stamp / remap). Risk-3 defenses: canonical JSON
for blobs, explicit secret handling, sentinel-leak scan."""
from __future__ import annotations

import json
from typing import Any, Callable

from bifrost.field_classes import FieldClass, field_class_of, match_keys
from pydantic import BaseModel


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


# A path policy maps a FieldClass -> one of: "keep" | "scrub" | "stamp" | "remap"
Policy = dict[FieldClass, str]


def assert_field_roundtrip(model, field, before, after, policy: Policy, row) -> None:
    cls = field_class_of(model, field, row)
    action = policy[cls]
    bval, aval = before.get(field), after.get(field)
    if action == "keep":
        assert canonical(aval) == canonical(bval), f"{model.__name__}.{field} ({cls}) changed: {bval!r} -> {aval!r}"
    elif action == "scrub":
        assert aval in (None, [], {}, ""), f"{model.__name__}.{field} ({cls}) leaked on scrub: {aval!r}"
    elif action == "stamp":
        # environment stamped from target — just assert it is SET to the target value (caller checks target)
        assert aval is not None, f"{model.__name__}.{field} ({cls}) not stamped"
    elif action == "remap":
        # reference remapped — value may differ but must be present iff before was present
        assert (aval is None) == (bval is None), f"{model.__name__}.{field} ({cls}) presence changed on remap"
    else:
        raise AssertionError(f"unknown policy action {action!r} for {cls}")


def assert_no_secret_leak(serialized_text: str, sentinels: list[str]) -> None:
    for s in sentinels:
        assert s not in serialized_text, f"secret sentinel {s!r} leaked into a non-secret envelope"


def pair_rows(model, before_rows, after_rows, strategy: str):
    """strategy: 'by_id' (_repo) | 'by_match_key' (solution) | 'by_remap' (id-only solution)."""
    if strategy == "by_id":
        idx = {r["id"]: r for r in after_rows}
        return [(b, idx[b["id"]]) for b in before_rows if b["id"] in idx]
    if strategy == "by_match_key":
        keys = match_keys(model)
        # match on surviving (non-scrubbed) key parts — caller passes already-stamped after rows
        def k(r): return tuple(r.get(f) for f in keys)
        idx = {k(r): r for r in after_rows}
        return [(b, idx[k(b)]) for b in before_rows if k(b) in idx]
    if strategy == "by_remap":
        # caller supplies the remap fn via after_rows already keyed by expected id
        idx = {r["id"]: r for r in after_rows}
        return [(b, idx[b["_expected_id"]]) for b in before_rows if b.get("_expected_id") in idx]
    raise AssertionError(f"unknown pairing strategy {strategy!r}")
```

- [ ] **Step 2: Write a unit test for the assertion logic itself (no DB)**

Create within `assertions.py`'s sibling test file `api/tests/roundtrip/test_assertions_unit.py`:

```python
"""The assertion helpers are themselves logic — test keep/scrub/remap + leak + canonical."""
import pytest
from bifrost.field_classes import FieldClass
from tests.roundtrip.assertions import assert_field_roundtrip, assert_no_secret_leak, canonical
import bifrost.manifest as m

KEEP_ALL = {c: "keep" for c in FieldClass}

def test_keep_passes_on_equal():
    assert_field_roundtrip(m.ManifestWorkflow, "description", {"description": "x"}, {"description": "x"}, KEEP_ALL, None)

def test_keep_fails_on_drop():
    with pytest.raises(AssertionError):
        assert_field_roundtrip(m.ManifestWorkflow, "description", {"description": "x"}, {"description": None}, KEEP_ALL, None)

def test_scrub_passes_when_absent():
    pol = {**KEEP_ALL, FieldClass.SECRET: "scrub"}
    assert_field_roundtrip(m.ManifestConfig, "value", {"value": "s", "config_type": "secret"}, {"value": None, "config_type": "secret"}, pol, _row(config_type="secret"))

def test_canonical_ignores_key_order():
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})

def test_leak_detector():
    with pytest.raises(AssertionError):
        assert_no_secret_leak('{"x":"SENT::secret"}', ["SENT::secret"])

class _R:
    def __init__(self, **k): self.__dict__.update(k)
def _row(**k): return _R(**k)
```

- [ ] **Step 3: Run — green**

Run: `./test.sh tests/roundtrip/test_assertions_unit.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add api/tests/roundtrip/assertions.py api/tests/roundtrip/test_assertions_unit.py
git commit -m "feat(roundtrip): field-policy assertion + canonical-JSON + secret-leak detector (Risk-3)"
```

---

## Task 5: `_repo` git-sync round-trip path (drives REAL generate_manifest + ManifestResolver)

The first real path: DB -> manifest -> DB, same-env, pairs by id, keeps environment, scrubs secret.

**Files:**
- Create: `api/tests/roundtrip/paths.py` (the `REPO_SYNC` driver + policy; shareable/full added in Task 6)
- Create: `api/tests/roundtrip/test_roundtrip_repo.py`

**Interfaces:**
- Produces: `REPO_SYNC` (a `RoundTripPath` with policy `{identity:keep, content:keep, environment:keep, secret:scrub, reference:keep}`, pairing `by_id`, and `.run(db, seeded_rows)` that writes rows to DB, calls `generate_manifest`, then `ManifestResolver` back into a clean DB and reads rows out).
- Consumes: real `generate_manifest` (`src.services.manifest_generator`), `ManifestResolver` / the resolver entry (`src.services.manifest_import`).

- [ ] **Step 1: Write the `RoundTripPath` base + `REPO_SYNC` in `paths.py`**

(Driver MUST call real `generate_manifest` and the real resolver — spec §8 Risk 2. Read `manifest_generator.py::generate_manifest` and `manifest_import.py::ManifestResolver` signatures first and wire the real calls; do not reimplement serialization.)

```python
"""RoundTripPath drivers. Each .run() drives the REAL serialization code end-to-end.
NO reimplementation of any path (spec Risk-2)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from bifrost.field_classes import FieldClass

REPO_POLICY = {
    FieldClass.IDENTITY: "keep", FieldClass.CONTENT: "keep",
    FieldClass.ENVIRONMENT: "keep", FieldClass.SECRET: "scrub",
    FieldClass.REFERENCE: "keep",
}

@dataclass
class RoundTripPath:
    name: str
    policy: dict
    pairing: str  # 'by_id' | 'by_match_key' | 'by_remap'
    run: Callable  # async (db, seeded) -> after_rows

# REPO_SYNC.run is implemented in the test (it needs DB fixtures); paths.py exports the
# policy + pairing, and a helper that calls generate_manifest + the resolver.
```

(Implementation detail: because the real paths need the Dockerised DB, the `.run` body lives in the test module using the e2e `db_session`. `paths.py` holds the policy/pairing constants + thin wrappers around the real functions.)

- [ ] **Step 2: Write the `_repo` round-trip test (drives real code, one entity to start: Workflow)**

Create `api/tests/roundtrip/test_roundtrip_repo.py`. Start with Workflow end-to-end to prove the harness wiring, then parametrize across entities once green. (Full code: seed a Workflow row with `all_fields_populated`, call real `generate_manifest`, wipe + call real resolver, read back, `assert_field_roundtrip` per field with `REPO_POLICY`, pairing `by_id`. Mark `@pytest.mark.e2e`; include the solution write-guard autouse fixture if any deploy occurs — for `_repo` it does not.)

- [ ] **Step 3: Run (e2e) — expect it to surface REAL drops**

Run: `./test.sh e2e` ; read `/tmp/bifrost-<project>/test-results.xml`.
Expected: PASS for fields the `_repo` path already round-trips; **RED for any field `_repo` actually drops** (these are real findings — record them; some are the spec §9 known drops). Do NOT loosen the assertion to make them pass — a red here is the harness doing its job. Triage: if a red is an INTENDED scrub the policy doesn't model, fix the policy/tag; if it's an unintended drop, record it for the §9 fix phase.

- [ ] **Step 4: Parametrize across all entities + commit**

Expand the test to parametrize over `iter_manifest_models()` (skip the id-only-on-solution distinction — `_repo` pairs all by id). Commit with the red findings documented in the report.

```bash
git add api/tests/roundtrip/paths.py api/tests/roundtrip/test_roundtrip_repo.py
git commit -m "feat(roundtrip): _repo git-sync round-trip over real generate_manifest + resolver"
```

---

## Task 6: Solution shareable + full-backup paths + the envelopes

The two Solution paths (drive real export-zip + install) and the separate envelope checks (table_data, secrets-leak, connection-declaration skeleton).

**Files:**
- Modify: `api/tests/roundtrip/paths.py` (add `SOLUTION_SHAREABLE`, `SOLUTION_FULL` policies + real-code wrappers)
- Create: `api/tests/roundtrip/test_roundtrip_solution.py`

**Interfaces:**
- Produces: `SOLUTION_SHAREABLE` (policy `{identity:remap, content:keep, environment:scrub, secret:scrub, reference:remap}`, pairing `by_match_key` / `by_remap` for id-only entities); `SOLUTION_FULL` (same but `environment:stamp`, `secret` via encrypted envelope); envelope checks `assert_table_data_roundtrips`, `assert_connection_decl_scrubbed`, `assert_secret_envelope_roundtrips`.
- Consumes: real `_collect_*` (`bifrost.commands.solution`), real `deploy` (`src.services.solutions.deploy`), real export-zip + `zip_install` + `secrets_blob`, `integration_template`.

- [ ] **Step 1: Add the two solution policies + real-code wrappers to `paths.py`**

(Read the real export/install entry points first: the export command path that builds the zip, `zip_install.py`, `secrets_blob.py` encrypt/decrypt, and `deploy`. Wire `.run` to call them. For id-only entities, compute the expected post-install id via the real `solution_entity_id(install_id, manifest_id)` and pair `by_remap`.)

- [ ] **Step 2: Write the shareable round-trip test (real export -> real install)**

Create `api/tests/roundtrip/test_roundtrip_solution.py`. Seed entities, run the REAL shareable export to a zip, install into a fresh target org via the REAL installer, read rows back. Assert with `SOLUTION_SHAREABLE` policy. Pair by match key (or by remap for Form/Agent/EventSource/MCPServer). Autouse solution write-guard fixture. Include the **leak check**: scan the shareable zip's manifest text for secret sentinels — must be absent.

- [ ] **Step 3: Write the full-backup test + envelope checks**

Add to the same file: full-backup export (password) -> install -> read back, asserting `SOLUTION_FULL` (environment stamped to target; secret survives via decrypt). Plus:
- `assert_table_data_roundtrips`: seed a table with rows, full-backup with `include_data=True`, install, assert rows match; AND assert a shareable export carries NO rows.
- `assert_connection_decl_scrubbed`: a solution declaring an integration connection exports a skeleton with NO `client_id`/secret/token/org — assert those are absent and the schema shape survives.
- `assert_secret_envelope_roundtrips`: a secret config value survives full-backup encrypt->decrypt, and is absent from the shareable/`_repo` outputs.

- [ ] **Step 4: Run (e2e), triage reds, commit**

Run: `./test.sh e2e`. Expect reds for real drops (record them). Same discipline as Task 5 Step 3: a red is a finding, not a thing to silence. Commit with findings documented.

```bash
git add api/tests/roundtrip/paths.py api/tests/roundtrip/test_roundtrip_solution.py
git commit -m "feat(roundtrip): solution shareable + full-backup paths + table_data/secret/connection-decl envelopes"
```

---

## Task 7: Triage + fix the drops the harness surfaced (the §9 known drops)

The harness is now the oracle. Collect every RED from Tasks 5-6, separate "intended scrub the policy/tag mis-models" (fix the metadata) from "unintended field drop" (fix the writer). Fix the unintended drops — these include the prior plan's deferred checklist (`auto_fill`, agent-delegation order, `max_run_timeout`/`event_type`/`display_name` parity).

**Files:**
- Modify: whichever writer/importer/model drops a field (per each red).
- Modify: tags/policies where a red is actually an intended behavior the harness mis-modeled.

**Interfaces:**
- Produces: the full harness green; each fixed drop has a one-line note in the task report (entity.field, path, root cause, fix).

- [ ] **Step 1: Enumerate the reds**

Collect all failing `(model, field, path)` from the Task 5-6 runs into a list (the report). For each, classify: (A) metadata/policy wrong → fix tag/policy; (B) real writer drop → fix the writer.

- [ ] **Step 2: Fix class-A (metadata) reds**

Adjust the field tag or path policy ONLY where the code's CURRENT behavior is correct and the harness mis-modeled it. Re-run; that red goes green. (Do not use this to paper over a real drop.)

- [ ] **Step 3: Fix class-B (real drop) reds one at a time**

For each real drop, fix the writer/importer (mirroring the Phase-1 `tool_description` fix pattern: add the field to the values dict / serializer / resolver). Re-run the harness after each. These are real bugs the harness caught.

- [ ] **Step 4: Full harness green + full sweep**

Run:
```bash
cd api && pyright && ruff check . && cd ..
./test.sh all
```
Expected: harness fully green; no regressions. Document every fix in the report.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "fix(manifest): close the field drops the round-trip harness surfaced (auto_fill, delegation order, field parity, …)"
```

---

## Task 8: Final verification + plan completion

- [ ] **Step 1: Full sweep**

Run:
```bash
cd api && pyright && ruff check . && cd ..
cd client && npm run tsc && npm run lint && cd ..   # (no client changes expected; confirm clean)
./test.sh all
```
Expected: all green; the field-class tripwire, generator-completeness tripwire, and all three round-trip paths pass.

- [ ] **Step 2: Confirm the tripwires actually bite (meta-check)**

Temporarily (in a scratch edit, NOT committed) remove one field's `**classify(...)` and confirm `test_field_class_tripwire` fails; remove one field from a generator and confirm `test_generator_completeness` fails. Revert both. This proves the safety net is armed. Document in the report.

- [ ] **Step 3: Commit any report + mark plan complete**

```bash
git add docs/superpowers/plans/2026-06-19-manifest-roundtrip-harness-phase1.5.md
git commit -m "docs(plan): mark round-trip harness Phase 1.5 complete"
```

---

## Self-Review notes

- **Spec coverage:** Task 1-2 = mechanism + tags (spec §2, §6). Task 3 = generators + Risk-1 (spec §5, §8). Task 4 = assertion + Risk-3 (spec §8). Task 5 = `_repo` path (spec §4 row 1, §3 by_id). Task 6 = solution paths + all envelopes (spec §4 rows 2-3, §5). Task 7 = the §9 fix phase. Task 8 = verification + meta-check the tripwires bite.
- **Codex corrections embedded:** path-dependent pairing (`by_id`/`by_match_key`/`by_remap`, Task 5-6); full-backup stamps env not preserves (SOLUTION_FULL policy); id-only entities have no match key (Task 2 Step 3 asserts `match_keys(ManifestForm) == ()`); table_data + connection-decl envelopes (Task 6 Step 3).
- **Risk defenses embedded:** Risk-1 = generator-completeness tripwire (Task 3); Risk-2 = "drives REAL code" global constraint + paths.py wrappers call real functions (Task 5-6); Risk-3 = canonical JSON + secret-decrypt + sentinel-leak (Task 4, Task 6 Step 2-3).
- **§7 ambiguous rows:** tagged to code-current defaults in Task 2 with the list called out for plan-review override. A wrong-but-current tag surfaces as a red in Task 7 and is fixed there.
- **Out of scope:** the convergence refactor itself (Phases 2-4); changing any scrub rule (this harness pins current behavior); adding natural-key matchers to id-only entities (a convergence decision, spec §10).
