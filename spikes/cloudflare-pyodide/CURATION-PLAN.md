# Cloudflare-Pyodide spike — upstream curation plan

Read-only analysis of how to split this spike branch into upstream-reviewable
changes. No code was modified to produce this plan. No deletion of evidence is
proposed — everything stays on the spike branch / in spike storage; only
selected platform seams are proposed for upstream.

Method: `git diff --stat origin/main...HEAD` and `git diff --numstat
origin/main...HEAD`, plus inspection of each hunk under `api/src` and
`api/tests`. Line counts below are `+added / -deleted` from numstat.

Branch shape (from diffstat): **1489 files changed, +169848 / -57**, of which:

- `api/src`: 9 files, +252 / -50 (8 modified + 1 new).
- `api/tests`: 11 files, +1718 / -7 (1 modified + 10 new).
- Everything else is spike material: `spikes/cloudflare-pyodide/results/`
  (712 files), `spikes/cloudflare-pyodide/backend/` (537 files),
  `spikes/cloudflare-pyodide/dev/` (171 files), plus harness/docs at the spike
  root and 4 repo-root-adjacent files (`.dockerignore`, `.gitignore`,
  `docker-compose.test.yml`, `product-updates/dispositions/pr-1161.json`).

## 1. Actual platform changes under `api/src` and `api/tests`

### 1a. `api/src` (9 files)

| Path | Lines | Why it is needed | Behavioral or additive |
|---|---|---|---|
| `api/src/services/execution/result_envelope.py` | +37 / -0 (new file) | Pure mapping from the shared engine result dict to the canonical parent envelope; lets Linux and companion runtimes share one contract without duplicating the field list. Neither measures resources nor invents zero values for unavailable telemetry. | **Additive** (new pure module; no behavior change by itself) |
| `api/src/services/execution/simple_worker.py` | +4 / -27 | Replaces the inline envelope dict with a call to `execution_result_envelope()`; preserves the existing contract including the supervised-service identity passthrough. | **Refactor, behavior-neutral** (net deletion; no contract change) |
| `api/src/services/execution/worker.py` | +44 / -12 | Adds the runtime seam: `ResourceMetrics` fields become `Optional` (unknown stays unknown instead of fake zeros), new `ExecutionResourceObserver` Protocol + `ProcessResourceObserver` (existing Linux accounting, unchanged for default callers), `run_execution()` gains `resource_observer` and `public_url` kwargs, `import resource` moves inside `_get_resource_usage` so non-POSIX providers can import the module. | **Additive seam** (defaults preserve current behavior) |
| `api/src/services/execution/engine.py` | +43 / -7 | Two things: (a) `ExecutionRequest.public_url` (additive; isolated providers need not import the server's settings/dependency closure, falls back to `get_settings().public_url` when `None`); (b) new `_capture_execution_streams()` context manager replacing the inline `redirect_stdout/redirect_stderr` block — retains printed lines on success *and* failure through the existing log path, scrubs discovered credentials before transport, streams via `write_log` with a telemetry-failure guard. | **Mixed**: (a) additive, (b) **behavioral** (success-path prints now retained; redaction boundary shared) |
| `api/src/services/execution/process_pool.py` | +2 / -2 | `attach_transport_metadata()` now binds `execution_id` as well as `sync`, so a physical result cannot choose another canonical execution identity after Redis loss. | **Behavioral** (small, targeted correctness fix) |
| `api/src/jobs/consumers/workflow_execution.py` | +5 / -1 | Fenced failure write: passes `owner_worker_id` to `update_execution()` and, when the write is refused (`not written`), drops the fenced result (`_drop_fenced_result`) and returns instead of publishing a stale failure notification. | **Behavioral** (unexpected setup errors respect executor ownership) |
| `api/src/worker/remote/protocol.py` | +20 / -0 | Optional `Hello.runtime_profile` field (validated `[a-z][a-z0-9._-]{0,63}`), serialized inside the signed bytes; `LINUX_RUNTIME_PROFILE = "linux-process-v1"` (omitted profile = existing Linux protocol, unchanged signed bytes) and `CLOUDFLARE_PYTHON_RUNTIME_PROFILE = "cloudflare-python-v1"` constants; `SignedHello` round-trip preserves the field. | **Additive** (backward compatible; omission = Linux) |
| `api/src/worker/remote/host.py` | +5 / -0 | Admission guard: rejects `runtime_profile` presented outside the signed hello, and rejects any non-`None`/non-Linux profile at the control host (experimental adapter is representable but not admittable). | **Behavioral guard** (rejects what was previously unrepresentable; no change to existing Linux hellos) |
| `api/src/worker/remote/remote_pool.py` | +87 / -8 | `ThinRemoteProcessPool` gains a `runtime_profile` ctor param (default Linux, validates against the two known profiles) plus the opaque-runtime rule set: `runtime_handle` (regex-validated, never a fake PID, `pid` key forbidden) via `_bind_workflow_handle()`; workflow-only inventory; immutable timing/identity checks; no persistent-service routes; no physical terminate (`RuntimeError` — cancellation not qualified); rejected-inventory reconciliation guard; ACK without valid PID becomes `ConnectionError` (launch state unknown, route preserved) instead of `RuntimeError`; heartbeat gains `runtime_invocations` for non-Linux profiles while OS-process diagnostics keep the integer-PID contract. | **Behavioral, but fenced behind a non-default profile** (Linux default path keeps PID semantics; see §4 for the shared-seam risk) |

### 1b. `api/tests` (11 files)

| Path | Lines | Why it is needed | Behavioral or additive |
|---|---|---|---|
| `api/tests/unit/execution/test_result_envelope.py` | +55 / -0 | Pins the one engine-result contract shared by Linux and companion runtimes (status mapping, defaults, unknown metrics, service passthrough). | Additive (new coverage for PR-2) |
| `api/tests/unit/execution/test_result_transport_identity.py` | +72 / -0 | Pins that transport metadata binds `execution_id` without mutating the result dict. | Additive (new coverage for `process_pool.py` fix) |
| `api/tests/unit/test_execution_runtime_metadata.py` | +86 / -0 | Pins trusted runtime inputs: default process observer still measures real resources; `public_url` override avoids server-settings import; unknown measurements stay `None`. | Additive (new coverage for PR-1 seams) |
| `api/tests/unit/engine/test_stream_capture.py` | +89 / -0 | Pins that printed workflow output shares the engine redaction/logging boundary (retained on success and failure, secrets scrubbed, telemetry failure does not change outcome). | Additive (new coverage for PR-3) |
| `api/tests/unit/worker/remote/test_runtime_profile.py` | +73 / -0 | Pins runtime-identity authentication: unsigned profile not silently ignored, explicit profile bound to signature, legacy Linux hello round-trips without a profile, malformed profiles rejected. | Additive (new coverage for PR-4) |
| `api/tests/unit/jobs/consumers/test_workflow_execution_lineage.py` | +24 / -0 (modified file) | Extends the existing lineage test module: setup failure fences owner and notifies only after the fenced write succeeds (`written` x `owner` parametrization). | Additive cases on a modified file (covers `workflow_execution.py` fix) |
| `api/tests/unit/dev/test_pyodide_remote_boundary.py` | +26 / -0 | Documents *current* remote admission constraints (non-process ACK identity rejected, unavailable provider memory rejected) — explicitly not a Pyodide compatibility claim. | Spike-qualification (stays out; see §3) |
| `api/tests/unit/dev/test_pyodide_remote_ack_boundary.py` | +97 / -0 | Uncertain-launch ACK contracts; no provider/SDK compatibility claim. | Spike-qualification (stays out; see §3) |
| `api/tests/unit/dev/test_pyodide_physical_handle.py` | +167 / -0 | Experimental opaque physical adapter behavior; explicitly not public runtime admission. | Spike-qualification (stays out; see §3) |
| `api/tests/unit/dev/test_pyodide_inventory_uncertainty.py` | +85 / -0 | Opaque inventory cannot certify physical settlement by Linux conventions. | Spike-qualification (stays out; see §3) |
| `api/tests/unit/dev/test_pyodide_attempt_journal.py` | +949 / -0 | Real-PostgreSQL fencing with physical launch as a deterministic local callback. **Loads the spike prototype at a hardcoded container path** (`/app/pyodide-prototype/attempt_journal.py`, mounted by the `docker-compose.test.yml` hunk below) — i.e. it tests spike code, not platform code, and cannot run without the spike mount. | Spike-qualification (stays out; see §3) |

### 1c. Repo-root-adjacent hunks (not `api/src`/`api/tests`, listed for completeness)

| Path | Lines | Disposition |
|---|---|---|
| `.dockerignore` (+3) | Excludes `spikes/cloudflare-pyodide/` and `.cloudflare/` from API build contexts. | Upstream-sensible only if/when any `spikes/` content is committed upstream; otherwise stays with the spike. |
| `.gitignore` (+3) | Ignores local `.cloudflare/` CLI account/build cache. | Independently upstreamable hygiene (no dependency on the spike). |
| `docker-compose.test.yml` (+1) | Mounts `spikes/cloudflare-pyodide/backend/attempt_journal.py` to `/app/pyodide-prototype/attempt_journal.py` for the journal test. Test-only mount coupling the suite to spike content. | Stays out (spike-harness coupling; see §3). |
| `product-updates/dispositions/pr-1161.json` (+15, new) | Draft author-proposal disposition for PR 1161. | Process artifact; follows the PR that lands, not a standalone change. |

## 2. Proposed PR split (4 PRs)

Ordered so each PR is reviewable and testable on its own; later PRs stack on
earlier ones. File lists are exact (paths relative to repo root).

### PR-1 — Runtime seams: observer + public_url + transport identity (smallest, first)

Rationale: pure seams with no new runtime admitted. Default behavior unchanged;
everything else in this plan depends on it conceptually but not textually
(except PR-2's envelope import, which is independent code).

Files (6):

- `api/src/services/execution/worker.py`
- `api/src/services/execution/process_pool.py`
- `api/tests/unit/test_execution_runtime_metadata.py` (new)
- `api/tests/unit/execution/test_result_transport_identity.py` (new)
- `.gitignore` (`.cloudflare/` cache ignore — trivially includable, or split out alone)

Dependencies: none. This is the base of the stack.

What the reviewer checks: `ResourceMetrics` optionality, `ProcessResourceObserver`
preserving existing accounting, `run_execution()` kwarg defaults, `execution_id`
binding in `attach_transport_metadata`, lazy `resource` import.

### PR-2 — Result envelope extraction (pure refactor + contract test)

Rationale: extracts the mapping historically owned by
`simple_worker._execute_async` into `result_envelope.execution_result_envelope`
with zero contract change; companion runtimes (and PR-4) can then share it.

Files (3):

- `api/src/services/execution/result_envelope.py` (new)
- `api/src/services/execution/simple_worker.py`
- `api/tests/unit/execution/test_result_envelope.py` (new)

Dependencies: conceptually follows PR-1 (shared "one contract" story) but
textually independent — no file overlap, mergeable in either order. Kept
separate so the refactor gets a clean behavior-neutral review.

### PR-3 — Engine stream capture + fenced setup-failure write

Rationale: the two behavioral correctness fixes to the shared execution path:
retained + redacted prints on all outcomes, and owner-fenced failure writes that
do not emit stale notifications. Groups the two changes a maintainer must
scrutinize together because both alter what the parent records/publishes.

Files (4):

- `api/src/services/execution/engine.py`
- `api/src/jobs/consumers/workflow_execution.py`
- `api/tests/unit/engine/test_stream_capture.py` (new)
- `api/tests/unit/jobs/consumers/test_workflow_execution_lineage.py` (modified)

Dependencies: stacks on PR-1 (`engine.py` uses `request.public_url` from the
PR-1 seam; PR-1 should merge first to avoid a forward-reference). Independent
of PR-2.

### PR-4 — Remote protocol profile + fenced opaque-pool rules (largest, last)

Rationale: the only PR that names the experimental runtime. Admits no new
capacity: the control host still rejects non-Linux profiles, and the pool's
opaque rules only activate under an explicitly constructed non-default
`runtime_profile`. This is where the ACK-uncertainty reclassification
(`RuntimeError` → `ConnectionError`, route preserved) and the
no-fake-PID / no-physical-terminate rules live.

Files (4):

- `api/src/worker/remote/protocol.py`
- `api/src/worker/remote/host.py`
- `api/src/worker/remote/remote_pool.py`
- `api/tests/unit/worker/remote/test_runtime_profile.py` (new)

Dependencies: stacks on PR-1 and PR-2 (pool heartbeat/envelope vocabulary and
the "unknown stays unknown" telemetry rule come from those PRs). Must come
after PR-1 at minimum. Independent of PR-3 (no file overlap).

### PR dependency graph (summary)

```text
PR-1 (seams) ──┬──▶ PR-3 (engine + consumer; needs public_url seam)
               │
               ├──▶ PR-4 (pool/protocol; needs observer/envelope vocabulary)
               │
PR-2 (envelope, textually independent) ──▶ PR-4 (shares envelope contract)
```

PR-1 and PR-2 are mutually order-independent. PR-3 needs PR-1. PR-4 needs PR-1
(and PR-2 for the full story, though it applies cleanly without it).

## 3. What stays out of upstream entirely

Nothing below is proposed for deletion — it remains on the spike branch and in
whatever spike archive the team uses. "Stays out" means "never enters `main`".

| Category | Exact scope | Where it should live instead |
|---|---|---|
| Captured evidence | `spikes/cloudflare-pyodide/results/` — 712 files, the bulk of the +169848 insertions (per-run JSON/XML/text receipts, benchmark outputs, supervisor transcripts, e.g. `results/ack-uncertainty/`, `results/api-prepare/`, `results/workerd-sdk-resume/`). Also `results/`-scale logs under the spike root. | Stay on the spike branch (or a spike archive / object store if the branch gets too large). They are qualification receipts, not source. |
| Prototype executor + notes | `spikes/cloudflare-pyodide/backend/` (537 files: `attempt_journal.py`, `ct128-journal-await.py`, `test_attempt_journal_owners.py`, ~500 `*.md` design/qualification notes e.g. `ACK-UNCERTAINTY.md`, `CONTRACT-JOURNAL.md`, `CANONICAL-*.md`) and `spikes/cloudflare-pyodide/dev/` (171 files: `CANONICAL-*.md`, `CODEQL-1935-1940.md`, `ENGINE-CONTEXT-*.md`, etc.). | Stay in `spikes/` on the spike branch. If the team curates spikes into `main`, that is a separate spike-governance decision — none of it is a prerequisite for PR-1…PR-4. |
| Spike harness + generated bundles | Spike root: `wrangler.jsonc`, `package.json`, `package-lock.json`, `uv.lock`, `pylock.toml`, `pyproject.toml`, `bench.mjs`, `cloud*.mjs`, `probe-io.*`, `verify*.mjs`, `summarize.py`, `prepare.py`, `termination.mjs`, `Dockerfile.tools`, `Dockerfile.test-tools`, `src/` (5 files: `probe.py`, `settings_adapter.py`, `workspace_helper.py`, …), `node_modules/`, `.venv*/`, `.wrangler/`, `.cloudflare/`, `python_modules/`, `*.md` qualifications (`ACCEPTANCE.md`, `CLOUD-BENCHMARK.md`, `WORKERD-*.md`, `NODE-*.md`, `SOURCE-RUNTIME-QUALIFICATION.md`, `CODEQL_REVIEW.md`, `IMPLEMENTATION.md`, `assessment.md`, `experiments.md`, `handoff.md`). | Stay on the spike branch; generated/vendor/cache dirs (`node_modules/`, `.venv*/`, `.wrangler/`, `.cloudflare/`) should never be committed anywhere (the `.gitignore`/`.dockerignore` hunks already point that way). |
| Spike-coupled test harness | `api/tests/unit/dev/test_pyodide_*.py` (5 files, +1334 lines combined, of which `test_pyodide_attempt_journal.py` alone is +949) and the `docker-compose.test.yml` +1 mount hunk that wires `backend/attempt_journal.py` into the test container. These tests import spike prototype code at a hardcoded container path and document explicitly experimental boundaries ("not public runtime admission", "no provider/SDK compatibility claim"). | Stay on the spike branch under `api/tests/unit/dev/` as spike qualification. Do not move to upstream `api/tests`: the journal test cannot pass without the spike mount, and the other four pin down intentionally non-committal boundaries. If a maintainer later wants any of these behaviors upstream, the corresponding rule must be re-expressed against platform code with platform fixtures (as PR-4's `test_runtime_profile.py` already does for the signed-profile boundary). |
| Process artifact | `product-updates/dispositions/pr-1161.json` | Travels with whichever PR actually lands (re-authored per the product-updates flow), not as a standalone file. |

Note on `.dockerignore` / `.gitignore`: the `.cloudflare/` ignore lines are
safe upstream hygiene, but the `spikes/cloudflare-pyodide/` docker exclusion
only matters if spike content ever enters `main` — either hunk can ride with
PR-1 or be dropped without affecting the plan.

## 4. Risks: shared paths other owners also modify

- **`api/src/worker/remote/remote_pool.py` (+87 / -8) — known shared seam,
  highest collision risk.** This is the file other teams extend for launcher
  capacity, adoption, heartbeat, and termination behavior. The spike touches
  `_adopt_handle`, inventory adoption, rejected-inventory handling,
  `_remote_pid_from_ack` (signature widened to `object`, error reclassified to
  `ConnectionError`), `_request_terminate`, and the heartbeat builder — all
  active areas. Mitigations already in the diff: everything non-Linux is
  branched on `self.runtime_profile != LINUX_RUNTIME_PROFILE`, and the default
  ctor value preserves the Linux wire representation; still, any concurrent
  change to ACK handling, inventory reconciliation, or heartbeat shape will
  conflict textually or semantically. PR-4 should be rebased last, reviewed by
  the pool owners, and landed promptly after approval. Flag in the PR body that
  `_remote_pid_from_ack`'s callers now see `ConnectionError` where they
  previously saw `RuntimeError`.
- **`api/src/services/execution/engine.py` (+43 / -7) — shared execution path.**
  Every workflow/script execution flows through `execute()`; the stream-capture
  swap changes what is logged and retained on success. Risk is behavioral rather
  than textual: log-volume growth, redaction-regex performance, or a transport
  that reacts badly to the new `write_log` call pattern. PR-3's test pins the
  boundary, but the reviewer should confirm log-pipeline expectations.
- **`api/src/services/execution/worker.py` (+44 / -12) — moderately shared.**
  `run_execution()` signature is extended with keyword-only optional args
  (backward compatible), but `ResourceMetrics` fields becoming `Optional`
  changes the type contract for every consumer of metrics. Any owner that
  formats or aggregates metrics with non-optional assumptions needs to handle
  `None`. PR-1 must call this out and `pyright` must be clean.
- **`api/src/jobs/consumers/workflow_execution.py` (+5 / -1) — consumer
  correctness, low collision.** Small hunk, but it changes failure-notification
  behavior (suppressed publish on fenced-out writes). Owners of execution
  history/notifications should confirm the dropped-publish case is the desired
  semantic.
- **`api/src/worker/remote/protocol.py` / `host.py` (+20 / +5) — low risk.**
  Additive optional field inside signed bytes; omission preserves the exact
  legacy byte layout, and the host guard only fires on profiles that could not
  previously be expressed. Main risk is a concurrent protocol-version bump
  colliding on the `Hello` dataclass — rebase and re-verify signed-byte
  stability.
- **`api/src/services/execution/process_pool.py` (+2 / -2) and
  `simple_worker.py` (+4 / -27) — lowest risk.** Two-line binding fix and a
  pure extraction respectively; call out the `execution_id`-binding change to
  the dispatch owners, but expect no conflicts.
- **Cross-cutting:** the `dev/*` spike tests must not leak into any upstream
  PR's CI surface — they depend on the spike mount and on explicitly
  non-committal semantics. Keep them out of the PR file lists (§2) so upstream
  CI never evaluates spike prototypes.

## 5. Smallest first PR: PR-1 (runtime seams)

Ship **PR-1** first: `worker.py` + `process_pool.py` tests-and-seams with the
`.gitignore` hygiene line.

- **Exact file list:** `api/src/services/execution/worker.py`,
  `api/src/services/execution/process_pool.py`,
  `api/tests/unit/test_execution_runtime_metadata.py`,
  `api/tests/unit/execution/test_result_transport_identity.py`, plus the
  3-line `.gitignore` addition.
- **Why independently valuable:** the observer seam lets future providers (and
  tests) substitute resource accounting without touching the engine; the
  `public_url` passthrough removes a server-settings import from the execution
  path for isolated workers; the `execution_id` binding is a standalone
  correctness fix (a physical result can no longer name a different canonical
  execution). Each is reviewable without any Cloudflare/Pyodide context.
- **Why low-risk:** all changes are keyword-only/optional additive or a
  two-line binding fix; default-constructed paths execute the exact same code
  as today (`ProcessResourceObserver` wraps the existing functions;
  `public_url=None` falls back to `get_settings()`). No protocol, pool,
  heartbeat, or notification behavior changes. No spike files, mounts, or
  `dev/*` tests are included, so upstream CI stays hermetic.

Verification for PR-1 (proposed, not executed here): targeted unit suites for
the touched modules, `pyright`/`ruff` on `api/`, and the DTO/parity tripwires
only if the PR touches DTO surfaces (it does not — no Pydantic models change).
