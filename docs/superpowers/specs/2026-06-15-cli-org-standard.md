# Spec: Unified `--org` standard across all CLI commands

**Date:** 2026-06-15
**Status:** design → writing-plans
**Branch:** `solutions/connection-references` · worktree `solutions-success-criteria`
**Requested by:** Jack — "lock in the CLI standard for `--org` on ALL commands: `none`, ID, or `--global`. The solution must not recommend a kind; that's entirely the installer's call."

## 1. Problem

Org-targeting is spelled three different ways across the CLI, with inconsistent "global" semantics:

| Command(s) | Flag | Value | "global" | Omit means |
|---|---|---|---|---|
| `workflows create` | `--org` | UUID-or-name | omit | global |
| `configs create` | `--organization` | UUID-or-name | omit | global |
| `claims` | `--scope` | UUID only | — | home org |
| `solution init` | `--scope` | `org`/`global` choice | `global` | — (writes descriptor) |
| `solution deploy/pull/start` | `--org` | UUID-or-name | omit | home org |
| `solution install` | `--org` | UUID id | omit | global |
| `tables/forms/agents` | body `organization_id` / `--scope` query | UUID | — | home org |

Three flag names (`--org`, `--organization`, `--scope`), and "omit" means *global* in some commands but *home org* in others — a footgun (accidental global writes) and a memory burden.

Separately, `bifrost solution init` writes `scope:` into `bifrost.solution.yaml`, baking an install-kind recommendation into the definition. But install kind (org vs global) is the **installer's** decision at deploy time — and the server already DERIVES scope from `organization_id` (NULL == global) and does not store it on the ORM row (`contracts/solutions.py:78`). So the descriptor's `scope` is a near-redundant hint.

## 2. The standard (locked with Jack)

**One flag, everywhere:** `--org`.

- `--org <uuid|name>` → that org.
- `--org none` or `--org global` → **global** (org = NULL). Synonyms.
- `--global` → boolean **alias** for `--org global`. Mutually exclusive with a non-global `--org` value (error if both a real org and `--global` are given).
- **Omit entirely → caller's HOME org.** Never an accidental global write.

Applies to every org-targeting command: `tables`, `forms`, `agents`, `configs`, `claims`, `workflows`, `events`, and the `solution` subcommands (`deploy`, `pull`, `start`, `install`).

**Descriptor:** remove `scope` from `bifrost.solution.yaml` and from `bifrost solution init`. The descriptor is pure definition (`slug`, `name`, `version`, `global_repo_access`, git fields, `logo`). Install kind is chosen ONLY at deploy via `--org`/`--global`/omit. `solution init` loses its `--scope` option.

## 3. Design

### 3.1 A shared `--org` option + resolver (single source of truth)

Add one reusable Click option + a resolver helper in `api/bifrost/` (e.g. `org_option.py`):

```
ORG_SENTINELS_GLOBAL = {"none", "global"}

@org_option              # decorator adding --org and --global (mutually exclusive)
def resolve_org(org: str | None, is_global: bool, client) -> OrgTarget:
    # returns one of: HOME (omit), GLOBAL (org=NULL), or a resolved org UUID.
    # --global  => GLOBAL
    # --org none|global => GLOBAL
    # --org <uuid|name>  => resolve via RefResolver -> UUID
    # neither   => HOME (caller's own org)
```

`OrgTarget` collapses to what each command needs to send the server: a concrete `organization_id` UUID, an explicit `None` (global), or "unset → server uses caller org." Because the wire contract already distinguishes "explicit None (global)" from "unset (home org)" for some entities, the resolver must preserve that three-state distinction (use a sentinel for UNSET, real `None` for GLOBAL).

### 3.2 Per-command migration

Replace each command's bespoke org option with the shared one:
- `workflows`: `--org` (omit==global) → standard (omit==home, `--global`/`--org global` for global). **Behavior change:** omit now = home org, not global.
- `configs`: rename `--organization` → `--org`; same omit-semantics change.
- `claims`: `--scope` → `--org`; gains `--global`. Its `_scope_params` maps to the new resolver output.
- `solution install`: `--org` already exists; extend to accept `none`/`global` + `--global`. (Today omit==global; new omit==home — but install is inherently a placement choice, so confirm whether install should default home or require explicit. See Open Questions.)
- `tables/forms/agents/events`: add the standard `--org` where today org is only settable via request body / `--scope` query.

Keep the old flags as **hidden deprecated aliases** for one release ONLY IF Jack wants a grace period; default per his "lock it in" is a hard switch (no alias). Decide in the plan.

### 3.3 Descriptor: drop `scope`

- Remove `scope` from `SolutionDescriptor` (`api/bifrost/solution_descriptor.py`).
- Remove `--scope` from `solution init`; stop writing `scope:` into the scaffolded `bifrost.solution.yaml`.
- `solution deploy` stops sending `descriptor.scope`; it sends the resolved org target (`organization_id` or None-for-global) to `POST /api/solutions`. The server already keys create on `organization_id` (NULL==global), so `SolutionCreate.scope` can be dropped or ignored.
- Export (`export.py:86`) already recomputes scope from the install's org for the *exported* descriptor — that line is removed too (no scope in the descriptor at all).
- **Back-compat:** an existing `bifrost.solution.yaml` that still has `scope:` must not crash. `SolutionDescriptor` should ignore unknown/legacy `scope` (pydantic `extra="ignore"`), so old descriptors load; the value is simply unused.

### 3.4 Server

- `SolutionCreate`: `scope` becomes derived/optional — kind is `organization_id is None`. Keep accepting `organization_id` (None == global). If `scope` is still a field, make it advisory/ignored, or remove it and update the deploy/install callers.
- No ORM change (scope was never stored).

## 4. Blast radius / tests

- **DTO-parity** (`test_dto_flags.py`): the new `--org` flag must map to the DTOs' `organization_id`; update `DTO_EXCLUDES`/flag mapping as needed.
- **Contract-version tripwire** (`test_contract_version.py`): removing `scope` from `SolutionCreate` / changing `SolutionDeployRequest` is a **breaking** DTO change → bump `CONTRACT_VERSION` in BOTH `api/shared/contract_version.py` and `api/bifrost/contract_version.py`, refresh `EXPECTED_CONTRACT_FINGERPRINT`.
- **MCP tools**: any tool taking org/scope must follow the same none/id/global convention (thin-wrapper tools inherit it via the endpoints).
- **Manifest/portable**: descriptor no longer carries scope — update `manifest.py`/`portable.py` if they reference it.
- **Skill appendices**: regenerate `generated/cli-reference.md` (flag changes) via the skill-truth generator; CI enforces freshness.
- **Skill docs**: rewrite the `--org` guidance in `references/solutions.md` + `references/entities.md` to the single standard; DELETE the descriptor-`scope` discussion and the "scope = kind switch" framing (now obsolete). The "One definition, many installs" section stays but drops scope.
- **e2e**: `solution init` no longer writes scope; deploy/install kind via `--org`. Update `test_solution_*` e2e that asserts scope in the descriptor or passes `--scope`.

## 5. Testing

- Unit: `resolve_org` truth table — omit→HOME, `--global`→GLOBAL, `--org none|global`→GLOBAL, `--org <uuid>`→that UUID, `--org <name>`→resolved UUID, `--org x --global`→error.
- Unit: each migrated command sends the right `organization_id`/None/unset for each `--org` form.
- Unit: `SolutionDescriptor` loads a legacy descriptor WITH `scope:` (ignored) and a new one WITHOUT it.
- e2e: create the same table `--org acme`, `--org none`, and omitted (home) → lands in the right scope each time.
- e2e: `solution init` (no scope) → `solution deploy --global` makes a global install; `solution deploy --org acme` makes an org install; both from the SAME descriptor.

## 6. Decisions (locked with Jack 2026-06-15)

1. **`solution install` default = caller's HOME org** when `--org`/`--global` omitted — uniform with every other command (was global-on-omit; this is a deliberate behavior change toward consistency).
2. **Permanent aliases, not deprecation.** `--org`, `--organization`, and `--scope` are all accepted on every org-targeting command and resolve identically through the shared resolver. `--org` is the canonical, documented form; the other two are permanent synonyms (no warning, no removal). This means the CLI flag surface is **additive** (we add `--org`/`--global` where missing and route the existing `--organization`/`--scope` to the same resolver) — no flag is ever removed.
3. **`none`/`global` are reserved sentinels:** the resolver checks `value in {"none","global"}` → GLOBAL BEFORE attempting org-name resolution. An org literally named "none"/"global" resolves to global (reserved names; documented).

### Consequence for the contract gate
Because the CLI flags are additive (aliases kept), the **CLI-side** change is non-breaking. The **breaking** part is server/descriptor: dropping `scope` from `SolutionDescriptor` and making `SolutionCreate.scope` derived/ignored. That still requires the `CONTRACT_VERSION` bump + fingerprint refresh (§4). `solution init` keeps NO `--scope` (the descriptor field is gone), but for symmetry `--scope`-as-org-alias does not apply to `init` (init never targeted an org — it only wrote the descriptor kind, which we're removing).

## 7. Constraints carried in

- Worktree only; full pre-completion verification; contract-version tripwire MUST be addressed.
- No client specifics in the public repo.
- This supersedes the recent `references/solutions.md` `--org`/scope guidance written during the A4–A6 validation loop — those sections get rewritten, not appended to.
