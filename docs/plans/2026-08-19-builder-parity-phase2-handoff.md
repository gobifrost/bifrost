# Builder capability parity — Phase 2 handoff (slices 3 and 4 remain)

**Prepared:** 2026-08-19
**Supersedes:** [`2026-08-18-builder-parity-orchestration-handoff.md`](2026-08-18-builder-parity-orchestration-handoff.md)
**Branch:** `codex/code-builder-pydantic-integration-20260816`
**Remote head at handoff:** `65e266d0c` (pushed; local and remote in sync)

Read this first, then
[`2026-08-17-builder-capability-parity-resume.md`](2026-08-17-builder-capability-parity-resume.md)
for the full state ledger and
[`2026-08-17-builder-capability-parity-execution.md`](2026-08-17-builder-capability-parity-execution.md)
for the phase plan.

**Three of five Phase 2 slices are done.** Claims, File policies, and the
Builder-local dispositions are committed and pushed. Configs and Policy rules
remain, and Jack has already decided both — the decisions are recorded below,
so the next session should not re-ask them.

## Start here

```bash
cd /home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816
git status --short --branch     # expect: only the two findings docs untracked
git log -3 --oneline            # expect: 65e266d0c on top
./test.sh stack up              # test stack; see "Stack state" below
```

Do **not** create a new worktree. This one is the work location.

### Stack state (differs from the previous handoff)

- **Test stack** (`bifrost-test-70494e39`): infra containers are up, but app
  containers may be stopped. `./test.sh stack up` does **not** restart already-
  exited app containers when the project looks up. If the API is not ready:
  ```bash
  COMPOSE_PROJECT_NAME=bifrost-test-70494e39 \
    docker compose -f docker-compose.test.yml up -d api worker scheduler
  ```
- **Debug stack** (`bifrost-debug-70494e39`): **DOWN**. The previous handoff
  said UP; that is stale. Boot it only if you need a live CLI drive.
- **Alembic head:** `20260818_file_policy_names`. Chain any new migration to
  exactly this.
- **Catalog size:** 82 operations (was 73 at the start of this session).

## What shipped

| Commit | Slice | Catalog |
|---|---|---|
| `7d1d63195` | Custom Claims | 73 → 78 |
| `81e9a16fb` | File Policies | 78 → 82 |
| `65e266d0c` | Builder-local dispositions | 82 (no change) |

Surface counts (CLI 140, MCP 101, REST 660) are unchanged across all three.
These slices gave existing operations a canonical identity; they did not add
capability. Do not "fix" an unchanged count — it is the expected outcome.

**Erratum in `65e266d0c`'s commit message.** It describes the 8 remaining
`missing_surface` MCP tools as "four configs, four policy-rule". The correct
split is **5 configs** (`list`, `get`, `create`, `update`, `delete`) and
**3 policy-rule** (`list`, `create`, `delete`). The total of 8 and the code
itself are correct; only that parenthetical is wrong. Verify against the live
inventory rather than the message.

## Jack's decisions — already made, do not re-ask

1. **Configs read.** Add `GET /api/config/{config_id}`; CLI `configs get` and
   MCP `get_config` become thin by-ID readers. Today each independently fetches
   the whole `GET /api/config` list and filters client-side (there is a code
   comment in `tools/configs.py` admitting it).
2. **The `POST /config/get` wart is out of scope.** Logged separately at
   `docs/plans/2026-08-18-config-read-verb-defect.md` (untracked — commit it
   with the Configs slice). Do not fix it here: it is on the workflow runtime
   read path.
3. **`ConfigCreate.value: dict` vs `SetConfigRequest.value: str`.** Flag with
   concrete options once the slice exposes which surfaces actually break.
   Do **not** pick a side unprompted — it is a typed contract change.
4. **Policy rules.** MCP gains the 3 missing tools (`get`, `update`, `usages`).
   All three are already REST-backed, so this is thin-wrapper work, not new
   endpoint design.
5. **Rename the CLI group `policy-rule` → `policy-rules`.** Breaking CLI
   change; see the gating note below.

## Slice 3 — Configs

Measured, not estimated:

- REST `/api/config` has GET-list, POST, PUT, DELETE — **no per-ID GET**. That
  is the endpoint to add.
- MCP tools in `tools/configs.py` (`TOOLS` at line 222): `list_configs`,
  `get_config`, `create_config`, `update_config`, `delete_config` — **5**
  uncataloged, all reporting `missing_surface` (correctly).
- The SDK does **not** use `/api/config`. `bifrost.config.get(key)` POSTs to
  `/api/sdk/config/get` (handler `cli_get_config` at `routers/cli.py:412`), a by-key
  resolver with cascade and secret decryption. Leave it alone. It serves a
  different need (workflow value lookup) than by-ID entity inspection.

## Slice 4 — Policy rules

- CLI group: `policy_rule_group = entity_group("policy-rule", ...)` at
  `api/bifrost/commands/policy_rules.py:36`, with 6 leaves: list, get, create,
  update, delete, usages.
- MCP has only 3: `list_policy_rules`, `create_policy_rule`,
  `delete_policy_rule`.
- REST (`api/src/routers/policy_rules.py`) already backs all six.
- Rename blast radius is small — `"policy-rule"` appears in exactly three
  Python files: `commands/policy_rules.py:36`, `commands/__init__.py:54`, and
  `tests/unit/cli/test_cli_base.py:231`. Generated skill references
  (`.claude/skills/.../cli-reference.md`, `plugins/bifrost/skills/...`) pick it
  up from regeneration.

### CLI-contract gating — the previous handoff is out of date here

This worktree's `CLAUDE.md` now describes a different mechanism than the older
docs. Verified in the tree:

- `MIN_CLI_VERSION = "1.2.3"` in `api/shared/version.py` is the **live runtime
  gate**. For a breaking CLI change, raise this to the release containing the
  compatible CLI, then refresh `EXPECTED_CONTRACT_FINGERPRINT`.
- `CONTRACT_VERSION = 10` (in both `api/shared/contract_version.py` and
  `api/bifrost/contract_version.py`) is a **frozen one-release bridge**. Its own
  docstring says not to keep bumping it. Do not bump it out of habit.

The `policy-rule` → `policy-rules` rename is a CLI-visible break, so it needs
the `MIN_CLI_VERSION` path, not the `CONTRACT_VERSION` path.

## The 10-step slice recipe

`git show 7d1d63195` (Claims) is the cleanest complete example. `git show
81e9a16fb` (File policies) shows what it looks like when the slice also has to
fix shared machinery.

1. Rename MCP tools to `bifrost_<verb>_<noun>` — function defs, `TOOLS`,
   `tool_funcs`, and `__all__`.
2. Update each `logger.info` to the **new** canonical name; leave
   `error_result` prefixes on the short name. Both are house convention
   (`tools/roles.py` is the reference).
3. Add `OperationDefinition` entries. IDs must match
   `^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$` — no underscores in the first
   segment. A bad ID fails validation at import and takes the API down on
   startup.
4. Add `**operation_route("<id>")` to each backing REST route.
5. Every optional surface (`cli`, `mcp`, `native_builder`, `manifest`, `sdk`)
   needs a binding or an explicit exclusion reason.
6. Set `authorization_resolver` and `action_scopes` from what the routes
   actually do — read the dependencies, do not guess.
7. Forward-only Alembic migration for persisted tool-ID renames, chained to
   `20260818_file_policy_names`, plus a migration unit test.
8. Update `PARITY_HANDLERS` in `tests/unit/test_mcp_thin_wrapper.py` and add an
   operations block to `tests/unit/services/test_operation_catalog.py`.
9. **Grep the whole test tree for old names before finishing.**
   `tests/e2e/mcp/test_mcp_parity.py` has a `SIGNATURE_PARITY_SPECS` list that
   references tools by dotted path. Both shipped slices hit this.
10. Regenerate, then add live MCP e2e coverage (CRUD roundtrip cross-verified
    against REST with `try/finally` cleanup, plus a denial/not-found path).

Regen command:

```bash
cd /home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816
timeout 300 docker run --rm -v "$PWD":/repo -w /repo/api --network none \
  -e BIFROST_SECRET_KEY=operation-catalog-generation-only-secret-key \
  -e PYTHONPATH=/repo/api \
  $(docker inspect bifrost-test-70494e39-api-1 --format '{{.Config.Image}}') \
  python /repo/api/scripts/operation_catalog/generate.py
```

The secret key must be ≥32 characters or the OpenAPI schema is partial and the
output is silently wrong.

## Verification

```bash
./test.sh tests/unit/test_mcp_thin_wrapper.py tests/unit/services/test_operation_catalog.py tests/unit/services/test_operation_inventory.py -q
./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -q
./test.sh tests/e2e/mcp/test_mcp_parity.py -k "<YourClass>" -q
./test.sh quality api
```

`./test.sh` is cwd-sensitive — run it from the worktree. `./test.sh e2e <path>`
deselects everything; pass the test path directly.

## Pitfalls already paid for

Each of these cost real time. The last four were found this session.

- **Operation IDs reject underscores in the resource segment.** Use dotted
  segments (`platform.jobs.get`, `files.policies.list`).
- **Before asserting `"error" not in payload`, check the response contract for
  a real `error` field.** `PlatformJobPublic` has one. Claims and
  `FilePolicyPublic` do not, so the plain assertion is safe there.
- **`./test.sh` is cwd-sensitive**, and editable CLI installs can serve a stale
  copy — confirm with
  `python -c "import bifrost.commands as c; print(c.__file__)"`.
- **Alembic revision IDs have a hard 32-character limit**
  (`alembic_version.version_num` is `varchar(32)`). A 35-char ID aborts startup
  with `StringDataRightTruncationError` *after* partially applying. Keep IDs
  short: `20260818_file_policy_names`, not
  `20260818_file_policy_mcp_tool_names`.
- **Route paths with a converter need care.** Starlette keeps
  `{policy_path:path}` in `route.path`; OpenAPI drops the converter. The
  catalog declares the **OpenAPI form** and `operation_inventory._openapi_path`
  normalizes before matching. Do not "fix" this by putting the converter in the
  catalog — three tests will disagree with each other.
- **The naming validator now handles nested CLI resources.** It folds ancestor
  segments into the noun and singularizes them (including `-ies` → `-y`). If
  you add another nested group, re-validate the **whole** catalog to prove no
  existing name shifted:
  ```bash
  ... python -c "from src.services.operation_catalog import OPERATION_CATALOG, validate_operation_catalog; validate_operation_catalog(); print(len(OPERATION_CATALOG))"
  ```
- **A stopped test stack looks "up."** See "Stack state" above.

## Working with a Sonnet executor

The split model worked, with one lesson. Both delegated slices produced good
work — accurate side effects read from real routes, correct conventions,
respected boundaries (neither invented a tool for the REST-only
`POST /policies/test`).

**But a subagent's report is a claim, not evidence.** Re-run the scoped tests
yourself before committing. This session:

- Claims reported success accurately; independent re-runs matched.
- File policies **blocked** on a genuine design gap (the nested-resource naming
  rule) and correctly stopped to ask rather than guess — but its messages did
  not arrive until after the orchestrator had independently hit the same wall.
  **Poll the working tree and the test XML rather than waiting on a report**,
  and use `SendMessage` to ask directly when progress stalls.
- Slice 5 was done directly by the orchestrator; it was small and touched
  shared inventory code.

Both remaining slices add a REST endpoint or change a public CLI contract, so
doing them directly is reasonable.

## Source-control boundary

Checkpoint commits and pushes to this integration branch are authorized. Jack
asked for **atomic commits with detailed messages** — one slice per commit,
body explaining what changed and why.

**Opening a PR, enabling auto-merge, or merging is NOT authorized** and
requires Jack's explicit approval.

Before any eventual PR, this worktree's `CLAUDE.md` requires `./test.sh pre-pr`
to pass for the exact `HEAD` (note: `pre-pr`, not the older `ci` target). Not
yet run against `65e266d0c`. Client `npm run tsc` / lint also unrun — no client
source changed, but the OpenAPI schema gained new operation identities, so a
type regen is worth confirming.

## Deferred findings — uncommitted, intentional

Two files are untracked so they do not muddy slice commits. Commit them with a
related slice or as their own docs commit:

- `docs/plans/2026-08-18-config-read-verb-defect.md` — `POST /api/sdk/config/get`
  is a pure read behind a POST. Includes the one half-defense (tri-state
  `scope` is awkward in a query string) and why it does not hold.
- `docs/plans/2026-08-18-manifest-binding-export-only-gap.md` — the catalog's
  `manifest` binding conflates "serialized into" with "reconciled from."
  Claims *and* Roles are export-only: `github_sync.py` has no `_resolve_claim*`
  and no `_resolve_role*`. Also records that `action_scopes` is validated for
  shape but never for membership in `AUTHORIZATION_SCOPE_CATALOG`, so a
  well-formed but nonexistent scope passes today (Phase 5 territory).

## After Phase 2

Phases 3–8 remain and are a different order of magnitude: Agent Skill
hydration, the transport-neutral `bifrost-build` Skill rewrite, the maintained
coding profile with dynamic authorization, native Builder target parity,
Builder UX/governance, and final delivery QA. Phase 5 (the authorization
intersection) and Phase 6 (Builder target parity) are the large ones. Treat
each as its own session with its own plan.
