# Handoff — Slice 4 (#390) next session

Paste the prompt below into a fresh session to resolve the open items and ship Slice 4.

---

## PROMPT

You are resuming **Slice 4 of GitHub issue #390** (manifest serialization
unification) in the Bifrost repo. The core work is **DONE and reviewed** on
worktree branch `390-manifest-unify-spike` (at the time of handoff, tip
`f921e5b9`) in `/home/jack/GitHub/bifrost/.worktrees/390-manifest-unify-spike`.
Work happens IN THAT WORKTREE, never on the primary `main` checkout. Tests run
via `./test.sh` only (Dockerized; test stack project is `bifrost-test-e7d765f2`,
already up — `./test.sh stack status` to confirm, `./test.sh stack up` if not).

### What's already complete (do NOT redo)
Every `Manifest*` model is the single source of truth for its serialization
(`from_row` / `view(dest)` / `to_orm_values(dest)`), replacing the four
hand-written writer families. Byte-identical output. Full backend suite 6378
passed / 0 failures, round-trip detector (`tests/e2e/roundtrip/`) 25/25,
contract-version tripwire green, pyright (CI-equivalent) 0 / ruff clean. 15
entities converted across 16 tasks, all reviewed (per-task + whole-branch opus +
Codex). SDD ledger: `.superpowers/sdd/progress.md`. Plan:
`docs/superpowers/plans/2026-06-20-manifest-unification-slice4.md`. Open items:
`docs/superpowers/plans/2026-06-20-manifest-unification-slice4-OPEN-ITEMS.md`
(READ THIS FIRST — it has full file/line pointers + acceptance criteria).

### Environment gotchas (verified this session — save yourself the rediscovery)
- **pyright**: the worktree has no `.venv`, so a bare `pyright` reports ~50
  false "Import X could not be resolved" (third-party libs). The CI-equivalent
  run is `cd api && /home/jack/GitHub/bifrost/.venv/bin/pyright --venvpath
  /home/jack/GitHub/bifrost` — THAT is the source of truth (0 errors). Per-file
  `pyright <file>` from `api/` also works. In-editor "bifrost.manifest_codec
  could not be resolved" + the cascading `Manifest(...) object` errors are the
  same FP — ignore.
- **Golden fixtures** (`api/tests/unit/golden/manifest_codec/*.json`) are the
  byte-identity oracle, compared NON-circularly (vs committed JSON, not vs the
  now-delegating live writer). The `/app` test mount is READ-ONLY, so capture
  writes to the writable LOG_DIR mount. To (re)capture after an intentional
  output change:
  `COMPOSE_PROJECT_NAME=bifrost-test-e7d765f2 LOG_DIR=/tmp/bifrost-bifrost-test-e7d765f2 docker compose -p bifrost-test-e7d765f2 -f docker-compose.test.yml --profile test run --rm -e UPDATE_GOLDEN=1 test-runner pytest tests/unit/test_manifest_codec.py -q`
  then `cp /tmp/bifrost-bifrost-test-e7d765f2/golden/manifest_codec/<name>.json
  api/tests/unit/golden/manifest_codec/`, INSPECT it, then run
  `./test.sh tests/unit/test_manifest_codec.py` TWICE (idempotency: per-run uuids
  are masked via `volatile_keys`). `./test.sh` does NOT forward `UPDATE_GOLDEN`
  — the direct compose run is required.
- Seed deterministic names (not `f"rt_{uuid}"`) so goldens are stable; mask
  per-run PK/FK ids via `volatile_keys=`.

### TASK 1 (do this first) — Fix B1: topic EventSources lose their topic key
This is the ONE real bug (the user does not want to land on main with it open).
It is **pre-existing on main** — Slice 4 reproduced it byte-identically — so
fixing it is a DELIBERATE behavior change, done as its own commit with a test +
golden re-capture. Full spec in the OPEN-ITEMS doc §B1; summary:

`EventSource` has three kinds: webhook, schedule, **topic**. A topic source is
identified by `event_type` (e.g. `"ticket.created"`), looked up by
`EventSourceRepository.get_by_topic()` (`api/src/repositories/events.py:172`).
`ManifestEventSource` has **no** parent `event_type` field, so topic sources lose
that key on export and import as `event_type = NULL` → `get_by_topic()` can't
find them → trigger never fires. Webhook/schedule are fine.

Fix (use superpowers:test-driven-development; write the failing round-trip test
FIRST):
1. Add `event_type: str | None` to `ManifestEventSource` in `api/bifrost/manifest.py`
   (likely `classify(FieldClass.CONTENT)`; it's the topic routing key).
2. Emit it in `api/src/services/manifest_generator.py::serialize_event_source`
   (`event_type=es.event_type`). Confirm `capture._event_entries` picks it up
   automatically (it delegates to `serialize_event_source().model_dump()`).
3. Add it to `ManifestEventSource._install_view` if that view is allowlist-based
   (check — EventSource overrides `_install_view` to return the FULL model_dump
   with Nones kept, so it may already flow through; verify the install golden
   gains `event_type`).
4. Write it in the importers: `manifest_import.py::_resolve_event_source` (git-sync)
   and `deploy.py::_upsert_events` (install — its `_direct` comes from
   `to_orm_values(INSTALL).direct`; confirm `event_type` reaches the EventSource
   row and that the old `"event_type": mevent.get("event_type")` line is fully
   superseded, not duplicated).
5. Tests: a new e2e that seeds a `topic` EventSource with `event_type="x.y"`,
   round-trips it through BOTH the `_repo` git-sync path AND a Solution install
   (use the `deploy_solution()`/`wait_for_deploy()` helpers in
   `tests/e2e/platform/conftest.py` — deploy is ASYNC), and asserts
   `get_by_topic("x.y")` finds the re-imported source on each. Confirm
   `EventSubscription.event_type` (a DIFFERENT, already-working field on
   subscriptions) is untouched.
6. Re-capture the `event_git_sync` / `event_install` goldens (they now include
   `event_type`); INSPECT the diff; run the codec tests ×2 + the round-trip
   detector (`./test.sh tests/e2e/roundtrip/`) + the full suite — all green.
7. Commit as a clearly-separate "fix(events): round-trip topic event_type ..."
   commit (it intentionally changes manifest output for topic sources).

Verify with B1's acceptance criteria in the OPEN-ITEMS doc.

### TASK 2 (optional, quick) — P1–P6 polish sweep
Only if the user wants a clean tree before merge. All are reviewer-triaged
ship-as-is (zero correctness impact). See OPEN-ITEMS §P-series for exact
locations: P1 restore 3 `serialize_*` delegator docstrings; P2 hoist
`_install_view` allowlist sets to module-level frozensets; P3 hoist
import-at-call-site in capture/deploy; P4 add `pytest.raises(NotImplementedError)`
for child-model `to_orm_values`; P5 comment on `entity_change_hook.py` bypassing
`view()`; P6 add explicit `import_owner="direct"` to `ManifestAgent.path`. Batch
into one cosmetic commit. Skip if the user prefers to fold into Pass 2.

### TASK 3 — Open the PR (use the `bifrost-issues` skill for PR flow)
After B1 (and optionally P-series) are green:
- Re-run the FULL gate: `./test.sh stack reset && ./test.sh all` (must be
  green), CI-equivalent pyright + ruff, contract-version tripwire.
- Open the PR against `main`. **DO NOT use a `Closes #390` keyword** — #390 stays
  open for Pass 2. Mention B1 is fixed and link the OPEN-ITEMS doc for P-series +
  Pass 2.
- `main` uses GitHub's native merge queue: `gh pr merge <N> --repo
  gobifrost/bifrost` (NO strategy flag) enqueues. **The user reviews before
  merge — confirm with them before queueing auto-merge.** Arm the combined
  reviews+checks+state watcher after opening.

### Do NOT
- Start Pass 2 (shape cleanups / format-version latch / base+superset
  consolidation) — it's a separate opt-in effort; do not begin without the user
  re-confirming value.
- Re-run the whole 16-task conversion or re-review converted entities — they're
  done and reviewed.
- Edit files on the primary `main` checkout.
