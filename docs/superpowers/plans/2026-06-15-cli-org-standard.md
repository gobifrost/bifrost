# Unified `--org` Standard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every org-targeting CLI command one uniform `--org` standard (`--org <id|name|none|global>` + `--global` alias; omit = caller's home org), keep `--organization`/`--scope` as permanent synonyms, and remove `scope` from the Solution descriptor so install kind is the installer's deploy-time choice.

**Architecture:** A single shared resolver + Click option (`api/bifrost/org_target.py`) is the source of truth for org targeting; each command's bespoke org option routes through it. The descriptor drops `scope` (server already derives kind from `organization_id is None`). Aliases make the CLI flag surface additive (non-breaking); the breaking part is the descriptor/`SolutionCreate` change, gated by a `CONTRACT_VERSION` bump.

**Tech Stack:** Python (Click CLI in `api/bifrost/`, FastAPI server, pydantic DTOs, pytest), the skill-truth generator.

**Spec:** `docs/superpowers/specs/2026-06-15-cli-org-standard.md`

---

## Conventions for every task

- **Worktree only** (`solutions-success-criteria`).
- **Run tests in-container** (the `./test.sh` api-exit flake): `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest <path> -v`.
- **CLI unit tests** use `click.testing.CliRunner` and run in-container (the `bifrost` package resolves there).
- **Restart the test-stack API container** after server-path changes: `docker restart bifrost-test-75bc0d9c-api-1`.
- **Commit** at the end of each task with the message shown.

---

## File Structure

**Create:**
- `api/bifrost/org_target.py` — `OrgTarget` result + `resolve_org_target()` + the shared `org_option` decorator.
- `api/tests/unit/test_org_target.py` — resolver truth-table + decorator tests.

**Modify:**
- `api/bifrost/commands/workflows.py` — route `--org` through the shared resolver; omit now = home (was global).
- `api/bifrost/commands/configs.py` — add `--org` (canonical) aliasing the existing `--organization`.
- `api/bifrost/commands/claims.py` — add `--org`/`--global` aliasing the existing `--scope`.
- `api/bifrost/commands/tables.py`, `forms.py`, `agents.py`, `events.py` — add the standard `--org`/`--global` option.
- `api/bifrost/commands/solution.py` — `deploy`/`pull`/`start`/`install` accept `none`/`global`/`--global`; `init` drops `--scope` and stops writing `scope:`.
- `api/bifrost/solution_descriptor.py` — remove `scope` field; `extra="ignore"` so legacy descriptors load.
- `api/src/services/solutions/export.py:86` — stop writing `scope` into the exported descriptor.
- `api/src/models/contracts/solutions.py` — `SolutionCreate.scope` becomes derived/ignored.
- `api/shared/contract_version.py` + `api/bifrost/contract_version.py` — bump `CONTRACT_VERSION` 3 → 4.
- `api/tests/unit/test_contract_version.py` — refresh `EXPECTED_CONTRACT_FINGERPRINT`.
- `.claude/skills/bifrost-build/references/solutions.md` + `entities.md` — rewrite `--org` guidance to the single standard; delete descriptor-`scope` discussion.
- `.claude/skills/bifrost-build/generated/cli-reference.md` — regenerate (flag changes).

---

## Task 1: Shared `OrgTarget` resolver

**Files:**
- Create: `api/bifrost/org_target.py`
- Test: `api/tests/unit/test_org_target.py`

- [ ] **Step 1: Write the failing test (truth table)**

```python
# api/tests/unit/test_org_target.py
import pytest
from bifrost.org_target import OrgTarget, resolve_org_target


class _FakeResolver:
    async def resolve(self, kind, value):
        assert kind == "org"
        return f"uuid-for-{value}"


@pytest.mark.asyncio
@pytest.mark.parametrize("org,is_global,expected", [
    (None,     False, OrgTarget.home()),                 # omit -> home
    (None,     True,  OrgTarget.global_()),               # --global
    ("none",   False, OrgTarget.global_()),               # --org none
    ("global", False, OrgTarget.global_()),               # --org global
    ("acme",   False, OrgTarget.org("uuid-for-acme")),    # --org name
])
async def test_resolve_org_target(org, is_global, expected):
    got = await resolve_org_target(org, is_global, _FakeResolver())
    assert got == expected


@pytest.mark.asyncio
async def test_org_and_global_conflict():
    with pytest.raises(ValueError, match="mutually exclusive"):
        await resolve_org_target("acme", True, _FakeResolver())


def test_org_target_wire_forms():
    # GLOBAL -> explicit None; HOME -> UNSET sentinel; ORG -> the uuid.
    assert OrgTarget.global_().organization_id is None
    assert OrgTarget.global_().is_set is True
    assert OrgTarget.home().is_set is False
    assert OrgTarget.org("u").organization_id == "u"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_org_target.py -v`
Expected: FAIL — `ModuleNotFoundError: bifrost.org_target`.

- [ ] **Step 3: Implement the resolver**

```python
# api/bifrost/org_target.py
"""One standard for org targeting across all CLI commands.

`--org <id|name|none|global>` + `--global` alias; omit => caller's home org.
`--organization` and `--scope` are permanent synonyms (added per command).
"""
from __future__ import annotations

from dataclasses import dataclass

import click

_GLOBAL_SENTINELS = {"none", "global"}


@dataclass(frozen=True)
class OrgTarget:
    """Resolved org target. Three states:
    - HOME  (is_set=False)            -> send nothing; server uses caller's org.
    - GLOBAL(is_set=True, id=None)    -> explicit global (organization_id NULL).
    - ORG   (is_set=True, id=<uuid>)  -> that org.
    """
    is_set: bool
    organization_id: str | None

    @staticmethod
    def home() -> "OrgTarget":
        return OrgTarget(is_set=False, organization_id=None)

    @staticmethod
    def global_() -> "OrgTarget":
        return OrgTarget(is_set=True, organization_id=None)

    @staticmethod
    def org(uuid: str) -> "OrgTarget":
        return OrgTarget(is_set=True, organization_id=uuid)


async def resolve_org_target(org: str | None, is_global: bool, resolver) -> OrgTarget:
    """Map (--org value, --global flag) to an OrgTarget.

    `none`/`global` are reserved sentinels checked BEFORE org-name resolution.
    """
    if is_global and org is not None and org.lower() not in _GLOBAL_SENTINELS:
        raise ValueError("--org <org> and --global are mutually exclusive")
    if is_global:
        return OrgTarget.global_()
    if org is None:
        return OrgTarget.home()
    if org.lower() in _GLOBAL_SENTINELS:
        return OrgTarget.global_()
    uuid = await resolver.resolve("org", org)
    return OrgTarget.org(uuid)


def org_option(fn):
    """Add the standard `--org` + `--global` to a command. `--organization` and
    `--scope` are added as permanent synonyms targeting the same `org` param."""
    fn = click.option("--org", "org", default=None,
                      help="Org UUID/name, or 'none'/'global' for global. Omit = your org.")(fn)
    fn = click.option("--organization", "org", help="Synonym for --org.")(fn)
    fn = click.option("--scope", "org", help="Synonym for --org.")(fn)
    fn = click.option("--global", "is_global", is_flag=True, default=False,
                      help="Target global scope (org=NULL). Alias for --org global.")(fn)
    return fn
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_org_target.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/org_target.py api/tests/unit/test_org_target.py
git commit -m "feat(cli): shared OrgTarget resolver + org_option (--org/--global standard)"
```

---

## Task 2: Multi-flag-to-one-param wiring test (alias collision guard)

Click maps `--org`, `--organization`, `--scope` all to the same `org` dest. Verify Click accepts that and last-wins, and `--global` is separate.

**Files:**
- Test: `api/tests/unit/test_org_target.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# append to api/tests/unit/test_org_target.py
import click
from click.testing import CliRunner
from bifrost.org_target import org_option


def test_aliases_map_to_one_param():
    captured = {}

    @click.command()
    @org_option
    def cmd(org, is_global):
        captured["org"] = org
        captured["is_global"] = is_global

    r = CliRunner()
    assert r.invoke(cmd, ["--org", "acme"]).exit_code == 0
    assert captured == {"org": "acme", "is_global": False}
    assert r.invoke(cmd, ["--organization", "beta"]).exit_code == 0
    assert captured["org"] == "beta"
    assert r.invoke(cmd, ["--scope", "gamma"]).exit_code == 0
    assert captured["org"] == "gamma"
    assert r.invoke(cmd, ["--global"]).exit_code == 0
    assert captured == {"org": None, "is_global": True}
```

- [ ] **Step 2: Run to verify**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_org_target.py::test_aliases_map_to_one_param -v`
Expected: PASS if Click allows 3 options → 1 dest (it does). If Click rejects duplicate dest, FIX `org_option` to declare the synonyms via a single `click.option("--org/--organization/--scope", ...)` secondary-name form instead; re-run.

- [ ] **Step 3: Commit**

```bash
git add api/tests/unit/test_org_target.py
git commit -m "test(cli): --org/--organization/--scope alias to one param; --global separate"
```

---

## Task 3: Migrate `claims` to the standard (the `--scope` case)

`claims` uses `_SCOPE_OPT` (`--scope`, UUID, → `params={"scope": ...}`). Add `--org`/`--global` via the shared option; map to the same server `scope` query param.

**Files:**
- Modify: `api/bifrost/commands/claims.py`
- Test: `api/tests/unit/test_cli_org_flags.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_cli_org_flags.py
import pytest
from click.testing import CliRunner


def _params_sent(monkeypatch, group, argv):
    """Invoke a command, capture the params/json the client would send."""
    sent = {}

    class _FakeClient:
        organization = {"id": "home-org"}
        async def get(self, path, params=None):
            sent["get"] = (path, params); return _Resp()
        async def post(self, path, json=None, params=None):
            sent["post"] = (path, json, params); return _Resp()
        async def put(self, path, json=None, params=None):
            sent["put"] = (path, json, params); return _Resp()
    class _Resp:
        status_code = 200
        def json(self): return []
        text = ""
    # patch the client + resolver factory used by the command group
    import bifrost.client as bc
    monkeypatch.setattr(bc.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient()))
    import bifrost.refs as rf
    async def _resolve(self, kind, value): return f"uuid-{value}"
    monkeypatch.setattr(rf.RefResolver, "resolve", _resolve)
    CliRunner().invoke(group, argv, catch_exceptions=False)
    return sent


def test_claims_list_global_via_org(monkeypatch):
    from bifrost.commands.claims import claims_group
    sent = _params_sent(monkeypatch, claims_group, ["list", "--org", "global"])
    # global -> scope param explicitly "none"/null marker the server reads as global
    path, params = sent["get"]
    assert params.get("scope") in (None, "none", "global")  # global marker
```

(Adjust the exact assertion to the server's global-scope query convention for claims — read `get_solution`/claims router for how `scope` query encodes global. If claims has no "global" concept, scope it to org-only and assert `--org acme` → `scope=uuid-acme`.)

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py::test_claims_list_global_via_org -v`
Expected: FAIL — `--org` not recognized by claims yet.

- [ ] **Step 3: Migrate claims**

Replace `_SCOPE_OPT` usage with `org_option`; in each command body, resolve the target and build the `scope` query param:

```python
# claims.py — at top
from bifrost.org_target import org_option, resolve_org_target

# replace @_SCOPE_OPT with @org_option, and the signature param `scope` with `org, is_global`
# inside each command body (after the resolver/client exist):
target = await resolve_org_target(org, is_global, resolver)
params = {}
if target.is_set:
    params["scope"] = target.organization_id if target.organization_id is not None else "none"
# (match "none"/null to how the claims router encodes global scope; if it uses
#  organization_id IS NULL, send the param the router maps to NULL.)
```

Keep `_scope_params` only if still used elsewhere; otherwise inline. Verify against `api/src/routers/` claims handler how `scope` maps to `organization_id` (NULL for global).

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/claims.py api/tests/unit/test_cli_org_flags.py
git commit -m "feat(cli): claims uses --org standard (--scope kept as synonym)"
```

---

## Task 4: Migrate `configs` and `workflows`

`configs` uses `--organization` (omit==global); `workflows` uses `--org` (omit==global). Both change to omit==home + the shared resolver. `--organization` stays as a synonym on configs.

**Files:**
- Modify: `api/bifrost/commands/configs.py`, `api/bifrost/commands/workflows.py`
- Test: `api/tests/unit/test_cli_org_flags.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# append to test_cli_org_flags.py
def test_configs_omit_is_home(monkeypatch):
    from bifrost.commands.configs import configs_group
    sent = _params_sent(monkeypatch, configs_group, ["set", "K", "--value", "v"])
    # omit -> HOME: no explicit organization_id sent (server uses caller org)
    payload = sent.get("post") or sent.get("put")
    body = payload[1] or {}
    assert "organization_id" not in body or body["organization_id"] is None and False  # home == unset
    # i.e. assert organization_id key is absent (UNSET), not None (which would be global)

def test_configs_global_via_flag(monkeypatch):
    from bifrost.commands.configs import configs_group
    sent = _params_sent(monkeypatch, configs_group, ["set", "K", "--value", "v", "--global"])
    payload = sent.get("post") or sent.get("put")
    body = payload[1] or {}
    assert body.get("organization_id", "MISSING") is None  # explicit global

def test_workflows_omit_is_home(monkeypatch):
    from bifrost.commands.workflows import workflows_group
    sent = _params_sent(monkeypatch, workflows_group, ["create", "--path", "functions/x.py", "--function-name", "main"])
    # adapt argv to the real required flags of `workflows create`; assert omit -> no organization_id key
```

(Read the real `configs set` / `workflows create` required args and adjust argv. The KEY assertion: **omit → `organization_id` absent (UNSET/home)**; `--global` → `organization_id: null`; `--org acme` → `organization_id: "uuid-acme"`.)

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py -k "configs or workflows" -v`
Expected: FAIL (omit currently sends global, not home).

- [ ] **Step 3: Migrate both**

Route both through `org_option` + `resolve_org_target`. Build the request body conditionally:

```python
target = await resolve_org_target(org, is_global, resolver)
body = {...}
if target.is_set:
    body["organization_id"] = target.organization_id   # None == global; uuid == org
# UNSET (home): do NOT set organization_id -> server uses caller org
```

For configs, declare `--organization` as the synonym via `org_option` (already included). Update the docstrings ("Omit for global" → "Omit = your org; --global for global").

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/configs.py api/bifrost/commands/workflows.py api/tests/unit/test_cli_org_flags.py
git commit -m "feat(cli): configs+workflows use --org standard (omit=home, --global for global)"
```

---

## Task 5: Add `--org` to `tables`, `forms`, `agents`, `events`

These set org via request body `organization_id` (or had no CLI org flag). Add the standard option to their create/update commands.

**Files:**
- Modify: `api/bifrost/commands/tables.py`, `forms.py`, `agents.py`, `events.py`
- Test: `api/tests/unit/test_cli_org_flags.py` (extend)

- [ ] **Step 1: Write the failing test (tables representative)**

```python
def test_tables_create_org_forms(monkeypatch):
    from bifrost.commands.tables import tables_group
    # omit -> home (no organization_id key)
    s = _params_sent(monkeypatch, tables_group, ["create", "--name", "t1"])
    body = (s.get("post") or (None, {}, None))[1] or {}
    assert "organization_id" not in body
    # --global -> explicit null
    s = _params_sent(monkeypatch, tables_group, ["create", "--name", "t2", "--global"])
    body = (s.get("post") or (None, {}, None))[1] or {}
    assert body.get("organization_id", "X") is None
    # --org acme -> uuid
    s = _params_sent(monkeypatch, tables_group, ["create", "--name", "t3", "--org", "acme"])
    body = (s.get("post") or (None, {}, None))[1] or {}
    assert body["organization_id"] == "uuid-acme"
```

(Repeat the pattern for forms/agents/events in the same test file. Adjust required create args per command.)

- [ ] **Step 2: Run to verify fail**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py -k "tables or forms or agents or events" -v`
Expected: FAIL — `--org` unknown on these commands.

- [ ] **Step 3: Add the option + body wiring to each create/update command**

For each: add `@org_option`, signature `org, is_global`, and:

```python
target = await resolve_org_target(org, is_global, resolver)
if target.is_set:
    body["organization_id"] = target.organization_id
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/tables.py api/bifrost/commands/forms.py api/bifrost/commands/agents.py api/bifrost/commands/events.py api/tests/unit/test_cli_org_flags.py
git commit -m "feat(cli): tables/forms/agents/events gain the --org standard"
```

---

## Task 6: `solution deploy/pull/start/install` accept `none`/`global`/`--global`

These already have `--org` (UUID/name). Extend to the full standard. `install` default flips global→home.

**Files:**
- Modify: `api/bifrost/commands/solution.py` (`deploy_cmd`, `pull_cmd`, `start`, `install_cmd`)
- Test: `api/tests/unit/test_cli_org_flags.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
def test_solution_install_omit_is_home(monkeypatch):
    from bifrost.commands.solution import solution_group
    # Build a minimal zip path arg; install resolves org. Omit -> home org id.
    # Assert the create/install call carries the caller's home org, NOT global.
    # (If install reads org via deployer_org_id, assert it resolved to "home-org".)
    ...
```

(Implement concretely: install today does `--org` as id with omit==global. Change `_run` so omit → `client.organization["id"]` (home). For `--org none|global`/`--global` → global (org=None). Assert via the captured POST body's `organization_id`.)

- [ ] **Step 2: Run to verify fail**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py -k solution -v`
Expected: FAIL.

- [ ] **Step 3: Migrate the four solution commands**

For `deploy`/`pull`/`start`: they pass `org_ref` into `_resolve_target_install(... deployer_org_id)`. Replace the `--org` plumbing with `org_option` + `resolve_org_target`; map:
- HOME → `deployer_org_id = client.organization["id"]`
- GLOBAL → resolve against the global install (`organization_id is None`)
- ORG → `deployer_org_id = target.organization_id`

For `install_cmd`: omit → home org id (was None/global); `--global`/`--org none|global` → None (global install); `--org <id|name>` → resolved id.

- [ ] **Step 4: Run to verify pass**

```bash
docker restart bifrost-test-75bc0d9c-api-1
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_cli_org_flags.py -k solution -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_cli_org_flags.py
git commit -m "feat(cli): solution deploy/pull/start/install adopt the --org standard (install omit=home)"
```

---

## Task 7: Drop `scope` from the descriptor + `solution init`

**Files:**
- Modify: `api/bifrost/solution_descriptor.py`, `api/bifrost/commands/solution.py` (`init`), `api/src/services/solutions/export.py:86`
- Test: `api/tests/unit/test_solution_descriptor.py` (create or extend)

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/test_solution_descriptor.py
from bifrost.solution_descriptor import SolutionDescriptor


def test_descriptor_has_no_scope_field():
    assert "scope" not in SolutionDescriptor.model_fields


def test_legacy_descriptor_with_scope_loads_ignored():
    # A pre-change descriptor still has scope: — it must load, scope ignored.
    d = SolutionDescriptor.model_validate(
        {"slug": "s", "name": "n", "scope": "org", "version": "1.0.0"}
    )
    assert d.slug == "s"
    assert not hasattr(d, "scope") or getattr(d, "scope", None) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_solution_descriptor.py -v`
Expected: FAIL — `scope` still a field.

- [ ] **Step 3: Make the edits**

- `solution_descriptor.py`: remove the `scope: Literal[...]` field; add `model_config = ConfigDict(extra="ignore")` so legacy `scope:` keys load harmlessly.
- `solution.py` `init`: remove the `--scope` option and stop writing `scope:` into the scaffolded yaml. Update the deploy create-install call (`POST /api/solutions`) to derive kind from the resolved org target instead of `descriptor.scope` (send `organization_id`; None == global).
- `export.py:86`: delete the `descriptor["scope"] = ...` line.

- [ ] **Step 4: Run to verify pass + the descriptor round-trip tests**

```bash
docker restart bifrost-test-75bc0d9c-api-1
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_solution_descriptor.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_descriptor.py api/bifrost/commands/solution.py api/src/services/solutions/export.py api/tests/unit/test_solution_descriptor.py
git commit -m "feat(solutions): drop scope from descriptor + solution init (install kind is deploy-time)"
```

---

## Task 8: Server — `SolutionCreate.scope` derived/ignored + contract bump

**Files:**
- Modify: `api/src/models/contracts/solutions.py` (`SolutionCreate`/`SolutionBase`)
- Modify: `api/shared/contract_version.py`, `api/bifrost/contract_version.py`
- Modify: `api/tests/unit/test_contract_version.py`

- [ ] **Step 1: Run the tripwire to see the failure first**

Run: `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_contract_version.py -v`
Expected: After editing `SolutionCreate` (next step) this FAILS with a fingerprint mismatch — that's the gate working.

- [ ] **Step 2: Make `scope` non-authoritative on the server**

In `SolutionBase`/`SolutionCreate`: drop the `scope` field (kind is `organization_id is None`), OR keep it accepted-but-ignored with a deprecation comment. Update `create_solution` (`routers/solutions.py:80`) so it no longer branches on `body.scope` — kind is purely `organization_id is None` (global) vs set. Update `deploy`/`install` callers that sent `scope`.

- [ ] **Step 3: Bump the contract version (both files) + refresh fingerprint**

Set `CONTRACT_VERSION = 4` in BOTH `api/shared/contract_version.py` and `api/bifrost/contract_version.py`. Then refresh `EXPECTED_CONTRACT_FINGERPRINT` in `test_contract_version.py` to the new value the test prints on failure.

- [ ] **Step 4: Run to verify pass**

```bash
docker restart bifrost-test-75bc0d9c-api-1
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/test_contract_version.py tests/unit/test_dto_flags.py -v
```
Expected: PASS (both versions agree; fingerprint matches; DTO parity holds).

- [ ] **Step 5: Commit**

```bash
git add api/src/models/contracts/solutions.py api/shared/contract_version.py api/bifrost/contract_version.py api/tests/unit/test_contract_version.py
git commit -m "feat(solutions): scope derived from organization_id; bump CONTRACT_VERSION 3->4"
```

---

## Task 9: e2e — same descriptor, install kind chosen at deploy

**Files:**
- Create/extend: `api/tests/e2e/platform/test_org_standard_e2e.py`

- [ ] **Step 1: Write the e2e**

```python
# Using the e2e_client + platform_admin pattern:
# 1. solution init-equivalent: POST a descriptor WITHOUT scope.
# 2. deploy --global  -> install with organization_id IS NULL (global).
# 3. (fresh slug) deploy --org <org> -> install with that organization_id.
# 4. create a table --org none -> global table; --org <org> -> org table; omit -> home org.
# Assert each lands in the right scope via GET.
```

(Implement concretely against the real endpoints, mirroring `test_solution_roundtrip.py` fixtures. Reset DB state for a clean e2e session.)

- [ ] **Step 2: Run**

```bash
docker restart bifrost-test-75bc0d9c-api-1
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/e2e/platform/test_org_standard_e2e.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_org_standard_e2e.py
git commit -m "test(cli): e2e — one descriptor, install kind via --org/--global at deploy"
```

---

## Task 10: Regenerate appendices + rewrite skill docs

**Files:**
- Regenerate: `.claude/skills/bifrost-build/generated/cli-reference.md` (+ `openapi-digest.md` if the server DTO changed)
- Modify: `.claude/skills/bifrost-build/references/solutions.md`, `entities.md`, `sources.yaml`
- Re-sync the Codex/public mirror.

- [ ] **Step 1: Rewrite the `--org` guidance**

In `solutions.md`: replace the "When `--org` is needed" + "scope = kind switch" sections with the single standard: `--org <id|name|none|global>` + `--global`; omit = home; `--organization`/`--scope` are synonyms. Remove the descriptor-`scope` discussion (the descriptor no longer has scope). Keep "One definition, many installs" but state kind is chosen at deploy via `--org`/`--global`. In `entities.md`: note the standard `--org` applies to entity commands too.

- [ ] **Step 2: Regenerate appendices (in-container → stdout → write host-side)**

```bash
for f in cli-reference openapi-digest; do
  docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner \
    python -c "import sys; sys.path.insert(0,'/app/scripts/skill-truth'); import generate as g; sys.stdout.write(g.GENERATORS['${f}.md']())" \
    > .claude/skills/bifrost-build/generated/${f}.md 2>/dev/null
done
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner python /app/scripts/skill-truth/generate.py --check
```
Expected: no "STALE" line.

- [ ] **Step 3: Lint claims + re-sync mirror + bump sha**

```bash
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner \
  python -c "import sys; sys.path.insert(0,'/app/scripts/skill-truth'); import lint_claims as l; from pathlib import Path; print('FINDINGS', len(l.lint_paths([Path('/.claude/skills/bifrost-build/references/solutions.md'), Path('/.claude/skills/bifrost-build/references/entities.md')])))"
./scripts/sync-codex-skills.sh
```
Expected: FINDINGS 0; mirror synced. Bump `verified_at_sha` for the touched files in `sources.yaml`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/bifrost-build/ plugins/bifrost/skills/bifrost-build/ .codex/skills/
git commit -m "docs(build-skill): document the unified --org standard; drop descriptor scope"
```

---

## Task 11: Full pre-completion verification

- [ ] **Step 1: Backend checks**

```bash
cd api && pyright && ruff check . && cd ..
```
Expected: pyright 0 errors, ruff clean.

- [ ] **Step 2: Full unit + the changed e2e (fresh DB session)**

```bash
# reset DB (per the worktree's manual-reset pattern), then:
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit -v
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/e2e/platform/test_org_standard_e2e.py tests/e2e/platform/test_capture_roundtrip.py -v
```
Expected: green.

- [ ] **Step 3: Skill-accuracy gates**

```bash
docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner python /app/scripts/skill-truth/generate.py --check
./scripts/sync-codex-skills.sh && git diff --exit-code -- plugins/bifrost/skills .codex/skills
```
Expected: generate --check clean, mirror diff clean.

- [ ] **Step 4: Resume the validation loop**

The `--org`/scope skill sections are now final. Resume Track A/B validation (RESUME pointer) against the corrected docs to the 3-consecutive-clean bar.

---

## Self-review notes (for the executor)

- **Click 3-flags-to-1-dest:** Task 2 verifies it; if Click rejects it, declare synonyms as secondary names on a single `click.option("--org", "--organization", "--scope", ...)`.
- **HOME vs GLOBAL on the wire is the crux:** HOME = omit the `organization_id` key (server uses caller org); GLOBAL = send `organization_id: null`. Never conflate them — that's the footgun the whole change fixes.
- **`claims` global encoding:** verify how the claims router encodes "global" in its `scope` query before asserting (Task 3).
- **Contract bump is mandatory:** the descriptor/`SolutionCreate` change is breaking; Task 8 bumps both version files + fingerprint or the tripwire stays red.
- **Legacy descriptors must not crash:** `extra="ignore"` (Task 7) lets old `scope:`-bearing yaml load.
- **Restart the api container** after Tasks 6/7/8 server-path changes before endpoint/e2e tests.
