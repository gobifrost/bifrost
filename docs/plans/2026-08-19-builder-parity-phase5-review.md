# Builder capability parity — status and Phase 5 proposal (for review)

**Prepared:** 2026-08-19
**Branch:** `codex/code-builder-pydantic-integration-20260816`
**Purpose:** record what shipped, and put the Phase 5 design in front of a
reviewer BEFORE more code is written. Phases 0–4 are done and pushed. Phase 5
is **not started**; what exists is one uncommitted change described below that
should be accepted, revised, or reverted as part of this review.

Companion documents:
[execution plan](2026-08-17-builder-capability-parity-execution.md) ·
[Phase 3 handoff](2026-08-19-builder-parity-phase3-handoff.md)

---

## 1. Status

**42 of 74 plan items complete.** Phases 0, 1, 2, 3, and 4 are closed.

| Surface | Session start | Now |
|---|---:|---:|
| Catalogued operations | 73 | **93** |
| REST endpoints | 660 | **663** |
| MCP tools | 101 | **104** |
| CLI leaves | 140 | **140** |
| MCP tools reporting `missing_surface` | 28 | **0** |

Verification at the last full run: backend unit suite **6015 passed, 0 failed**;
client Vitest **1976 passed across 300 files, 0 failed**; `./test.sh quality api`
**0 errors, 0 warnings**. `./test.sh pre-pr`, full backend e2e, and Playwright
have **not** been run this session.

### Commits (oldest first, all pushed)

| Commit | What |
|---|---|
| `5772043c6` | Config `value` DTOs `dict` → `str`; three workarounds deleted |
| `6aeb32631` | Configs operation slice + `GET /api/config/{config_id}` |
| `b7e575da0` | Policy Rules slice + `GET /api/policy-rules/{domain}/{name}`; CLI group renamed `policy-rule` → `policy-rules`, leaf `usages` → `list-usages` |
| `7577d4dc7` | Fix: non-canonical org cascade on the config by-ID read |
| `bbed4a24a` | Fix: top-level CLI help omitted registered entity groups |
| `9f6ed7c86` | Fix: the six pre-existing unit-test failures |
| `ae9df80d2` | Phase 2 cross-cutting items closed; removed dead `tools/db.py` |
| `c9ade7c5b` | Agent Skill content `revision` + migration |
| `c53ce2f35` | `read_skill_asset` → `bifrost_read_agent_skill_file` across four runtimes |
| `e366ad349` | Skill descriptor returned from `bifrost_get_agent` |
| `d91aa80ee` | Fix: dynamic gateway was silently dropping the Skill file reader |
| `3aba6e2f7` | Deterministic `.skill` export through `ArtifactRef` |
| `a16eee902` | Agent Skill UI driven by the descriptor |
| `709c7b502` | `bifrost-build` Skill made transport-neutral |

### Defects found while executing (not additions)

- **The dynamic MCP gateway silently dropped the Skill file reader.** It is
  planner-injected, matched no classifier branch, fell through to a workflow
  lookup that missed, and was discarded. Progressive Skill loading did not work
  over MCP at all. Dispatch also never populated the Skill context fields, so
  even a classified reader would have failed closed.
- **`ConfigCreate.value` was typed `dict`** while every endpoint and every
  released CLI sent a string. Both the CLI and the MCP tool carried explicit
  workarounds to route around their own DTO.
- **A by-ID read applied an org cascade its sibling writes do not**, so a
  platform admin could delete a config the new endpoint answered 404 for.
- **Two CLI-surface drifts**: `knowledge` and `platform-jobs` were registered
  but invisible in `bifrost --help`, and a stale hardcoded group list.
- **Archive determinism was real but unpinned** — nothing tested it.

### Corrections to the earlier handoff

1. Policy rules were **not** fully REST-backed. `get` had no endpoint; the CLI
   listed everything and filtered client-side.
2. `MIN_CLI_VERSION` was **not** raised for the CLI rename and should not be —
   it already sits at an unreleased 1.2.3.
3. Operation IDs **cannot** contain an underscore in a resource segment; two
   separate grammars reject it. Hence `policy.rules.*`, not `policy_rules.*`.

---

## 2. Phase 5 — the finding that reorders it

Phase 5 is "maintained coding profile and dynamic authorization". Reading the
code rather than the plan's prose changed the picture twice.

### 2a. The plan's framing of item 1 does not match the code

The plan says to implement the coding experience as "a versioned built-in
profile, **not a normal Agent with a permanently copied list of privileged
tools**."

What actually exists: `ensure_builder_agent()` writes one Agent row per private
Solution (`builder_agent_id(solution_id) = uuid5(solution_id, key)`) and copies
`BUILDER_AGENT_SYSTEM_TOOLS` into `system_tools`.

But that list is **not privileged**. It is:

```
list_files, read_file, search_text, write_file, apply_patch,
delete_file, make_directory, validate_solution, test_solution_build
```

Nine sandbox file operations plus a build check, confined to one Builder
session's temp directory by `WorkspaceRoot`, which resolves every path beneath
its root. They grant no authority over any platform resource. `solutions.build`
already gates entry to the Builder; `WorkspaceRoot` gates reach.

**Therefore moving that list from the row into code buys nothing.** An earlier
draft of this work proposed exactly that; it is withdrawn.

### 2b. The real gap

Two things are actually missing:

1. **The Builder has no platform operations at all.** Its whole toolbox is the
   nine sandbox tools plus `test_solution_build`. It cannot create a workflow,
   table, form, or agent — none of the 93 catalogued operations.
2. **`action_scopes` has zero consumers.** The catalog declares which scope each
   of the 93 operations requires (populated during Phases 2–3). Nothing reads
   them anywhere in `api/src`.

So "a coding agent whose tools follow from the user's permissions" has no
mechanism today, in any form.

### 2c. The blocker under that

Comparing the two catalogs:

- `AUTHORIZATION_SCOPE_CATALOG` (grantable through roles) defined **9** scopes.
- The operation catalog declared **32** distinct scopes.
- Overlap: **4** — `executions.read`, `files.content.read`,
  `files.content.write`, `workflows.execute`.

**81 of 93 operations declared a scope no role could grant.**

This matters beyond tidiness: enforcing `action_scopes` in that state would have
denied 81 operations to every non-superuser, and **the test suite would not have
caught it, because tests run as platform admin** and `has_scope()` short-circuits
for superusers. That is a platform-wide outage that presents as a green suite.

The cause is a known seam, logged earlier this session: the catalog validator
checks scope **shape** (`resource.verb`) but never **membership**, so
`agents.write` validated cleanly while existing nowhere.

---

## 3. Uncommitted change — for accept / revise / revert

One change is in the working tree, **not committed**, and is the reason this
document exists. It is inert: nothing enforces scopes yet, so no request behaves
differently.

| File | Change |
|---|---|
| `api/shared/authorization_scopes.py` | +28 scope definitions (13 entity domains × read/write, plus `knowledge.read` and `apps.publish`). Total 9 → 37. |
| `api/src/services/operation_catalog.py` | Validator now rejects a declared scope with no definition, at import. |
| `api/tests/unit/test_authorization_scopes.py` | Two tests: every declared operation scope is grantable; entity scopes are assignable to custom roles. |

Result: **zero** declared-but-ungrantable scopes.

**The reviewable part is that these 28 scopes are user-visible.**
`GET /api/roles/scopes` feeds the role-management UI, so they appear as
assignable permissions to operators. Names and grouping are a product decision,
not a mechanical one:

`agents.read/write`, `apps.read/write`, `apps.publish`, `claims.read/write`,
`configs.read/write`, `events.read/write`, `files.policies.read/write`,
`forms.read/write`, `integrations.read/write`, `organizations.read/write`,
`policy.rules.read/write`, `roles.read/write`, `tables.read/write`,
`workflows.read/write`, `knowledge.read`

Open questions for the reviewer:

- Are read/write the right granularity, or do some domains need finer
  (e.g. `agents.execute` distinct from `agents.write`)?
- Are the categories right for how the role UI groups them?
- `organizations.write` and `roles.write` are effectively administrative. Should
  they be `is_privileged=True` rather than ordinary?

---

## 4. Proposed Phase 5 sequence

Ordered so each step is independently useful and testable.

1. **Define the missing scopes** (the uncommitted change above) — nothing
   permission-based can work without a vocabulary to grant.
2. **Validate membership at import** (also above) — closes the seam so a future
   operation cannot declare an ungrantable scope.
3. **Enforce `action_scopes`** at capability discovery (do not offer what the
   caller cannot use) and again at execution (do not trust a stale discovery).
   This is the load-bearing step and the one that must not ship before 1–2.
4. **Give the coding experience platform operations**, filtered by step 3, with
   Solution reach decided by the existing `can_access_solution()` gate.
5. **Make it built-in and settings-enabled**, so nobody hand-assembles a coding
   agent.

Requirements taken from Jack this session:

- A hand-built MCP-only coding agent **keeps working unchanged**. It is an
  ordinary Agent with hand-picked tools; nothing here alters that path.
- The built-in agent is **enabled in settings** and its tools follow from the
  user's permissions.
- It should reach the regular workspace and non-Solution entities as well as
  Solutions.
- Builder users edit only Solutions they own or have access to.

### Undecided — needs a call

**How Solution access is expressed.** `can_access_solution()` is already the
single central gate and already admits owners, platform admins, and support
principals. The gap is the third leg: grants today are **per user**
(`SolutionBuilderCollaborator`, a `(solution_id, user_id, access)` row with
`view`/`edit`), not **per role**.

| Option | Cost |
|---|---|
| Keep per-user | Ships as-is; matches the model's "working team" framing; no migration |
| Add role grants alongside | New table + migration; matches how the rest of Bifrost expresses access; scales past naming individuals |
| Defer | Steps 1–5 do not depend on it; adding roles later is additive, not breaking |

Recommendation: **defer**, unless role-based Solution access is already
intended — in which case deciding now costs one migration instead of two.

---

## 5. Explicitly not done

- `./test.sh pre-pr`, full backend e2e, and Playwright have not run this session.
- No PR opened. Opening, queueing, or merging requires Jack's explicit approval.
- Phases 5–8 remain: 32 items. Phase 5 and Phase 6 (native Builder target
  parity) are the two large ones. Phase 8 alone carries 11 verification items
  including live local-Worker and Cloudflare proofs.

## 6. Note on process

Two framing errors are recorded above rather than quietly fixed, because a
reviewer should know the plan's own wording produced them:

- The "privileged tool list" framing in Phase 5 item 1 describes something the
  code does not do, and reasoning from that wording rather than the source
  produced a proposed refactor with no benefit.
- Phase 5 assumes an authorization mechanism (`action_scopes` enforcement) that
  does not exist and could not have worked if written, because 81 of 93
  operations referenced ungrantable scopes.

Both were caught by reading the source. The plan remains sound in intent; its
Phase 5 prose needs the corrections in section 2.
