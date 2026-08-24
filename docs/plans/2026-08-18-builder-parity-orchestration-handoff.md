# Builder capability parity — orchestration handoff

**Prepared:** 2026-08-18
**For:** a fresh session that orchestrates on Opus 5 and delegates execution to
Sonnet 5
**Branch:** `codex/code-builder-pydantic-integration-20260816`
**Remote head at handoff:** `c84bdf9eb` (pushed; local and remote in sync)

Read this first, then
[`2026-08-17-builder-capability-parity-resume.md`](2026-08-17-builder-capability-parity-resume.md)
for the full state ledger and
[`2026-08-17-builder-capability-parity-execution.md`](2026-08-17-builder-capability-parity-execution.md)
for the phase plan. This document only covers **how to run the remaining Phase 2
work with a split orchestrator/executor model**.

## Start here

```bash
cd /home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816
git status --short --branch     # expect: clean, in sync with origin
git log -3 --oneline
./debug.sh status               # expect: Status: UP
./test.sh stack status          # expect: containers up
```

Do **not** create a new worktree. This one is the work location, and both its
debug and test stacks are already booted.

Running stacks at handoff (ephemeral — reconfirm, do not trust these verbatim):

- Debug URL: `https://bifrost-70494e39-qshz.eu1.netbird.services`
- Debug Compose project: `bifrost-debug-70494e39`
- Test Compose project: `bifrost-test-70494e39`
- Alembic head, live debug DB: `20260817_platform_job_names`

## Model split

There are no custom agent definitions in this repo (`.claude/agents/` does not
exist), so use the built-in `Agent` tool with an explicit model override:

```
Agent({
  description: "Canonicalize Claims slice",
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: "<the full slice brief — see template below>"
})
```

**Opus (orchestrator) keeps:**

- every design decision, and every question put to Jack;
- the two flagged slices' judgement calls (configs, policy rules);
- reading subagent reports and deciding whether evidence is real;
- all `git commit` / `git push` actions and their messages;
- the final handoff wording.

**Sonnet (executor) gets** one self-contained slice at a time. Do not fan out
slices in parallel: they all edit `operation_catalog.py`,
`test_operation_catalog.py`, `test_mcp_thin_wrapper.py`, and the generated
inventory, so concurrent agents will collide. Run them **sequentially**.

A subagent's report is a claim, not evidence. Before committing, the
orchestrator re-runs the scoped tests itself — cheap, and it has already caught
one wrong assertion this program.

## The remaining Phase 2 work

Measured, not estimated: **all four remaining slices are already thin HTTP
wrappers** with existing CLI groups. The expensive conversion work is done.

| # | Slice | Shape | Delegate to Sonnet? |
|---|-------|-------|---------------------|
| 1 | Claims | Mechanical, 5 tools over `/api/claims` | Yes, unattended |
| 2 | File policies | Mechanical, 4 tools over `/api/files/policies` | Yes, unattended |
| 3 | Configs | **Design decision first** | Only after Jack decides |
| 4 | Policy rules | **Design decision first** | Only after Jack decides |
| 5 | 11 Builder-local tools | Mechanical dispositions | Yes, unattended |

Recommended order: **1, 2, 5 first** (no decisions needed, so the session makes
real progress before spending any of Jack's attention), then raise 3 and 4
together as a single batched question.

### The two decisions to batch for Jack

**Configs.** There is no per-ID REST GET, so `bifrost configs get` and MCP
`get_config` each independently resolve a ref and then filter the
`GET /api/config` list payload client-side — the exact divergence the plan says
REST should own. Separately, `ConfigCreate.value` is `dict` while the public
`SetConfigRequest.value` is `str`, which the shared-DTO bullet will hit.
Options: (a) add `GET /api/config/{config_id}` and make both surfaces thin
readers — recommended, matches the plan's architecture; (b) catalog the read as
list-derived with an explicit reason; (c) drop the `get` operation.

**Policy rules.** The CLI group is singular (`policy-rule`) while the catalog
naming validator expects a plural resource, and the CLI exposes
`get`/`update`/`usages` leaves that have no MCP counterpart (MCP has only
list/create/delete). Decide whether MCP gains the missing operations or they are
recorded as intentional CLI-only exclusions, and whether the CLI group is
renamed to `policy-rules` (a breaking CLI change requiring the contract
tripwire's attention).

## Slice brief template for the Sonnet executor

Give the subagent everything it needs; it does not inherit this conversation.

> You are working in the git worktree
> `/home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816`
> on branch `codex/code-builder-pydantic-integration-20260816`. Never edit the
> primary checkout at `/home/jack/GitHub/bifrost` — it is on `main`. Always pass
> absolute paths; the shell cwd resets between commands and a relative path can
> land in the wrong checkout.
>
> Canonicalize the **<SLICE>** slice, following the pattern set by
> `api/src/services/mcp_server/tools/roles.py` and the platform-job slice in
> commit `ce67707cb` (read `git show ce67707cb` first — it is the smallest
> complete example).
>
> Steps:
> 1. Rename the MCP tools to `bifrost_<verb>_<noun>` in
>    `api/src/services/mcp_server/tools/<module>.py` (both the `TOOLS` list and
>    the `tool_funcs` map).
> 2. Add `OperationDefinition` entries to
>    `api/src/services/operation_catalog.py`. Operation IDs must match
>    `^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$` — **no underscores in the first
>    segment**; use dotted segments. A bad ID fails Pydantic validation at
>    import time and takes the whole API down on startup.
> 3. Add `**operation_route("<id>")` to each backing REST route.
> 4. Every optional surface (`cli`, `mcp`, `native_builder`, `manifest`) needs
>    either a binding or an explicit exclusion reason, or the model validator
>    rejects the entry.
> 5. Write a forward-only Alembic migration renaming persisted tool IDs,
>    modelled on `api/alembic/versions/20260817_platform_job_mcp_tool_names.py`.
>    Chain `down_revision` to the current head. Never restore a withdrawn
>    Builder migration body.
> 6. Register the slice in `api/tests/unit/test_mcp_thin_wrapper.py`
>    (`PARITY_HANDLERS` and `MODULES`) and add an operations block to
>    `api/tests/unit/services/test_operation_catalog.py`.
> 7. Update the expected counts in
>    `api/tests/unit/services/test_operation_inventory.py` if CLI leaves change.
> 8. Regenerate the inventory and Skill reference:
>    ```bash
>    timeout 180 docker run --rm -v "$PWD":/repo -w /repo/api --network none \
>      -e BIFROST_SECRET_KEY=operation-catalog-generation-only-secret-key \
>      -e PYTHONPATH=/repo/api \
>      $(docker inspect bifrost-test-70494e39-api-1 --format '{{.Config.Image}}') \
>      python /repo/api/scripts/operation_catalog/generate.py
>    ```
>    The secret key must be at least 32 characters or the app builds a partial
>    OpenAPI schema and the output is silently wrong.
> 9. Add live MCP coverage in `api/tests/e2e/mcp/test_mcp_parity.py` for both a
>    success path and a denial/not-found path.
>
> Verify (run from the worktree — `./test.sh` derives its Compose project from
> the current directory, so running it elsewhere targets a different stack and
> reports `Status: DOWN`):
> ```bash
> ./test.sh tests/unit/test_mcp_thin_wrapper.py tests/unit/services/test_operation_catalog.py tests/unit/services/test_operation_inventory.py -q
> ./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -q
> ./test.sh tests/e2e/mcp/test_mcp_parity.py -k "<YourClass>" -q
> ./test.sh quality api
> ```
> Note: `./test.sh e2e <path>` deselects everything — pass the test path
> directly instead.
>
> Do **not** commit, push, or open a PR. Report: files changed, exact commands
> run with their pass/fail counts, the new inventory counts, and anything you
> could not verify. If a design question arises that the brief does not answer,
> stop and report it rather than guessing.

## Pitfalls already paid for

Each of these cost real time this session. Put them in the executor's brief.

- **Operation IDs reject underscores in the resource segment.**
  `platform_jobs.get` fails validation at import and crashes API startup; use
  `platform.jobs.get`.
- **The naming validator derives the MCP name from the CLI path** and
  normalizes hyphens in both resource and verb. A hyphenated CLI resource used
  to produce an invalid hyphenated MCP tool name.
- **A contract field can collide with the tool-result convention.**
  `PlatformJobPublic` has a real `error` field that is `None` on success, so
  asserting `"error" not in payload` fails on a healthy read. Assert
  `payload.get("error") is None` instead.
- **`./test.sh` is cwd-sensitive.** Run it from the worktree or it targets a
  different worktree's stack.
- **Editable CLI installs can serve a stale copy.** After
  `pip install -e <worktree>/api`, confirm with
  `python -c "import bifrost.commands as c; print(c.__file__)"` that the path
  points into the worktree.
- **Heredocs and relative paths are dangerous after a cwd reset.** One `cat >>`
  in this session landed in the primary checkout on `main`. It was reverted
  immediately and nothing was committed there, but always use absolute paths.

## Live CLI drive

A live drive caught things the test suite did not. The scratch venv from this
session is at `/tmp/bifrost-cli-pjob` and already logged in; if it is gone:

```bash
mkdir -p /tmp/bifrost-cli-<name> && cd /tmp/bifrost-cli-<name>
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip packaging
.venv/bin/pip install --quiet -e /home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816/api
.venv/bin/bifrost login --url <DEBUG_URL> --email dev@gobifrost.com --password <FROM_DEBUG_STATUS>
```

Install the **worktree source**, not `/api/cli/download` — the download endpoint
serves the branch base build and will not contain new commands.

To apply a new migration to the debug stack: restart
`bifrost-debug-70494e39-init-1` (which runs alembic), then
`bifrost-debug-70494e39-api-1`. Hot reload never applies migrations.

## Source-control boundary

Checkpoint commits and pushes to this integration branch are authorized. Jack
has asked for **atomic commits with detailed messages** — one slice per commit,
body explaining what changed and why, not just what.

**Opening a PR, enabling auto-merge, or merging is NOT authorized** and requires
Jack's explicit approval. Before any eventual PR: `./test.sh pre-pr` must pass
for the exact HEAD, and the Builder file/behavior inventory must be updated
against the preserved backup with every omission accounted for.

Not yet run against the current head: client `npm run tsc`/lint and
`./test.sh pre-pr`. No client source has changed in these slices, but the
OpenAPI schema has gained new operation identities, so a type regen is worth
confirming before a PR.

## After Phase 2

Phases 3–8 remain and are a different order of magnitude from what is left of
Phase 2 — Agent Skill hydration, the transport-neutral `bifrost-build` Skill
rewrite, the maintained coding profile with dynamic authorization, native
Builder target parity, Builder UX/governance, and final delivery QA. Phase 5
(the authorization intersection) and Phase 6 (Builder target parity) are the
large ones. Treat each as its own session with its own plan; do not start one
at the tail of a Phase 2 session.
