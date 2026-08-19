# Builder capability parity — Phase 2 COMPLETE, Phase 3 handoff

**Prepared:** 2026-08-19
**Supersedes:** [`2026-08-19-builder-parity-phase2-handoff.md`](2026-08-19-builder-parity-phase2-handoff.md)
**Branch:** `codex/code-builder-pydantic-integration-20260816`

**Phase 2 is done.** Every MCP tool is now either cataloged or explicitly
dispositioned: `missing_surface` is **zero**. Phases 3–8 remain and are a
different order of magnitude — treat each as its own session with its own plan.

## Start here

```bash
cd /home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816
git status --short --branch     # expect clean
git log -5 --oneline
./test.sh stack status          # see "Stack state"
```

Do **not** create a new worktree. This one is the work location.

### Stack state

- **Test stack** (`bifrost-test-70494e39`): UP and healthy.
  If the API stops serving (a large stash/checkout can trigger a uvicorn reload
  that does not come back), restart the app containers:
  ```bash
  COMPOSE_PROJECT_NAME=bifrost-test-70494e39 \
    docker compose -f docker-compose.test.yml up -d api worker scheduler
  ```
- **Debug stack** (`bifrost-debug-70494e39`): DOWN. Boot only for a live CLI drive.
- **Alembic head:** `20260819_policy_rule_names`. Chain any new migration to this.
- **Catalog size:** 93 operations (82 at the start of this session).

## What shipped this session

| Commit | What | Catalog |
|---|---|---|
| `5772043c6` | Config `value` DTOs: `dict` → `str`, workarounds deleted | 82 |
| `6aeb32631` | Configs slice canonicalized + `GET /api/config/{config_id}` | 82 → 87 |
| `b7e575da0` | Policy Rules slice canonicalized + `GET /api/policy-rules/{domain}/{name}` | 87 → 93 |
| `7577d4dc7` | Fix: non-canonical org cascade on the config by-ID read | 93 |
| `bbed4a24a` | Fix: top-level CLI help omitted registered groups | 93 |

### Surface counts

| Surface | Before | After | Why |
|---|---|---|---|
| catalog | 82 | **93** | configs.* (5) + policy.rules.* (6) |
| REST | 660 | **662** | two genuinely new by-key/by-ID GETs |
| MCP | 101 | **104** | policy-rule get / update / list-usages |
| CLI | 140 | **140** | `usages` → `list-usages` is a rename, not an addition |
| MCP `missing_surface` | 8 | **0** | Phase 2 objective met |

## Corrections to the previous handoff — read before trusting it

1. **Policy rules were NOT fully REST-backed.** The old handoff said `get` /
   `update` / `usages` were "already REST-backed, so this is thin-wrapper work."
   `update` and `usages` were; **`get` was not** — there was no
   `GET /api/policy-rules/{domain}/{name}` and the CLI listed every rule and
   filtered client-side. The endpoint was added this session.
2. **`MIN_CLI_VERSION` was NOT raised** for the `policy-rule` → `policy-rules`
   rename, and should not be. It already sits at an unreleased `1.2.3` (latest
   tag is `v1.2.0`), the same floor the App/Event surfaces are staged behind, so
   the rename lands inside that existing window. The contract fingerprint also
   did not move, correctly: it covers DTO schemas, and the tripwire deliberately
   excludes command names because a renamed group 404s loudly.
3. **Operation IDs cannot be `policy_rules.*`.** Two separate grammars reject an
   underscore in a resource segment — the `OperationDefinition.operation_id`
   pattern AND the authorization-scope pattern. Use dotted segments:
   `policy.rules.get`, scopes `policy.rules.read` / `policy.rules.write`.
   The catalog validator catches both, and a bad ID fails at import, taking the
   API down on startup.

## Pitfalls paid for this session (additive to the previous list)

- **The MCP naming validator derives tool names from CLI verbs.** A bare verb
  like `usages` yields `bifrost_usages_policy_rule`. Name multi-word leaves
  `<action>-<subresource>` (`list-usages` → `bifrost_list_policy_rule_usages`),
  matching the `list-executions` / `get-dependencies` precedent.
- **`OrgScopedRepository.get()` applies NO cascade to ID lookups** ("IDs are
  globally unique"). Adding one to a by-ID read makes it inconsistent with the
  by-ID update/delete beside it — an admin could delete a row they could not
  read. See `7577d4dc7`.
- **Hardcoded surface counts live in `test_operation_inventory.py`.** Adding an
  endpoint or MCP tool requires updating them; they are not derived.
- **Two generators, not one.** `scripts/operation_catalog/generate.py` refreshes
  the inventory; `scripts/skill-truth/generate.py` refreshes the CLI reference
  and openapi digest. A CLI rename needs the second one, then
  `scripts/sync-codex-skills.sh` for the `plugins/bifrost/` mirrors (CI gates it).
- **`git stash` on a large working tree can knock the test API over** via a
  uvicorn reload that does not recover. Prefer baselining a suspected
  pre-existing failure on a scratch clone, or restart the app containers after.

## Known failures — pre-existing, NOT introduced here

`./test.sh unit` reports these 6 (of 5991 collected; 5985 pass). All were
verified failing with this session's changes stashed, and none are touched by any
commit above. Each needs an owner; none is a legitimate "flaky."

| Test | Cause | Owner |
|---|---|---|
| `test_mcp_tools_file_index.py` (3 tests) | `ImportError: cannot import name '_read_from_s3'` — `3b3f9a868` ("make workspace file operations canonical") removed the helper without updating its test | dedicated repair; the tests are stale, not the code |
| `test_org_scoping_enforcement.py` (2 tests) | Inline org filters in `routers/integrations.py:118`; plus stale allow-list entries for `routers/workflows.py` and `routers/agents.py` whose text no longer matches | the allow-list notes say "phase 6 migrates" |
| `test_application_create_commit.py` | `TypeError: 'MagicMock' object can't be awaited` — an async mock not updated after a signature change | dedicated repair |

`./test.sh unit` went from 7 failures to these 6 (5985 passed). The one that
disappeared is `test_cli_surface_smoke.py::test_top_level_help_lists_every_entity_group`,
fixed in `bbed4a24a` because the policy-rules rename made that list mine to touch.
A second drift of the same class — the stale `ENTITY_GROUPS` set in
`tests/unit/cli/test_cli_base.py` — was fixed earlier in `6aeb32631`, so it had
already cleared before the 7-failure baseline was taken.

## Verification run this session

- `./test.sh quality api` → **0 errors, 0 warnings** (run after every slice)
- `tests/e2e/api/test_config.py` → **34/34**
- `tests/e2e/mcp/test_mcp_parity.py` → **67/67** (full file)
- `tests/e2e/test_cli_policy_rules.py` + 3 sibling policy-rule e2e → **25/25**
- Unit: thin-wrapper, catalog, inventory, both migrations, dto_flags,
  dto_body_assembly, contract_version, `cli/`, surface smoke → **all green**
- Catalog validated at import: 93 operations, every derived MCP name accepted
- CLI surface confirmed live: group `policy-rules`, six leaves incl.
  `list-usages`, old name absent, every group listed in help
- Both new migrations applied cleanly to the live test DB

**NOT run:** `./test.sh pre-pr` (the mandatory pre-PR gate), backend e2e in
full, client `npm run tsc` / lint / Vitest / Playwright. No client source
changed, but the OpenAPI schema gained two endpoints and new operation
identities, so a `npm run generate:types` check is worth doing before any PR.

## Tests worth knowing about

New coverage that pins contracts a future refactor could silently break:

- `TestConfigValueTypeRoundTrip` — store-then-read for all five `ConfigType`s.
  Every type transports a **string** and is coerced on read (`int`/`bool` cast,
  `json` parsed, `secret` decrypted). `int` and `bool` had **no** coverage
  before. This is what makes `value: str` the correct DTO annotation.
- `TestConfigGetById` — by-ID read incl. secret masking, list-payload parity,
  cross-org admin read, 404/422/401/403.
- `TestMcpParityPolicyRules` — the six tools, cross-verified against the REST
  body, plus the `new_name` rename path and `(domain, name)` scoping.

Both fix commits were validated by deliberately re-introducing the defect and
confirming the new test failed — worth doing for any behavioral fix here.

## Source-control boundary

Checkpoint commits and pushes to this integration branch are authorized.
Atomic commits with detailed messages, one slice per commit.

**Opening a PR, enabling auto-merge, or merging is NOT authorized** and requires
Jack's explicit approval. Before any eventual PR, `./test.sh pre-pr` must pass
for the exact `HEAD`.

## Deferred findings — now committed

- `docs/plans/2026-08-18-config-read-verb-defect.md` — `POST /api/sdk/config/get`
  is a pure read behind a POST. Still open; deliberately out of scope.
- `docs/plans/2026-08-18-manifest-binding-export-only-gap.md` — the catalog's
  `manifest` binding conflates "serialized into" with "reconciled from."
  **Updated this session:** Configs is a third export-only instance, and it took
  the *opposite* choice from Claims (exclusion vs binding). That divergence is
  deliberate and documented, and is the strongest argument yet for splitting the
  field. Also records that `action_scopes` is validated for shape but never for
  membership in `AUTHORIZATION_SCOPE_CATALOG` — a well-formed but nonexistent
  scope passes today (Phase 5 territory).

## After Phase 2

Phases 3–8: Agent Skill hydration, the transport-neutral `bifrost-build` Skill
rewrite, the maintained coding profile with dynamic authorization, native Builder
target parity, Builder UX/governance, and final delivery QA. Phase 5 (the
authorization intersection) and Phase 6 (Builder target parity) are the large
ones.

**On parallelizing:** the previous handoff's Sonnet-executor notes still apply,
and its conclusion held again this session — slices that add an endpoint or
change a public contract are better done directly, because verifying a
subagent's claims means reading the same code twice. Phases 5 and 6 have
genuinely parallelizable surface (broad audits, many-file sweeps) and are the
better delegation candidates.
