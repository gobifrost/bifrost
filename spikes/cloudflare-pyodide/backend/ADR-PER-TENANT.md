# ADR: Per-tenant persistent workers for the Cloudflare Pyodide executor

Status: proposed (decision record + cost model, documentation only).
Supersedes nothing. Extends `ISOLATION-POSTURES.md` (measured 2026-11-12).

## 1. Decision and boundary relocation

**Decision:** move the Dynamic Workers trust boundary from **per-attempt** to
**per-tenant** for tenants that accept the weaker guarantee, keeping per-attempt
as the only posture for untrusted code.

- **Per-attempt (current default):** fresh isolate per attempt via
  `LOADER.load` (no id). Measured: **19/19 hostile canaries pass**;
  ~2.1 s Python boot per attempt locally.
- **Per-tenant (this ADR):** one long-lived worker per tenant via
  `LOADER.get(tenantId, code)`. Measured: cross-tenant canaries **PASS**
  (different workers never share an isolate); within-tenant canaries **FAIL** —
  builtins / modules / env / fs / credentials bleed between attempts of the
  same tenant. Warm attempt ~2 ms vs ~2.1 s cold.

The boundary relocation is explicit: **attempt inherits nothing** becomes
**tenant inherits nothing from other tenants, but attempts within a tenant
inherit everything from earlier attempts of that tenant**. Posture D degrades
to posture A (one long-lived worker, no reset) for retries and re-runs of the
same org. This is a blast-radius / policy decision for trusted operators, not
an isolation upgrade. Anything needing "an untrusted attempt inherits nothing"
still requires per-attempt workers.

Billable creation fact constraining both postures: **$0.002 per unique dynamic
worker per day**, and `LOADER.load(code)` with no id counts as **ONE per
invocation** (each attempt mints a new billable worker).

## 2. Variants

### Variant 1 — Dynamic Workers keyed by tenant (recommended for this ADR)

- Key: `LOADER.get(tenantId, code)`. One cached worker per tenant, reused
  across attempts.
- Creation meter: 1 unique worker per tenant per day while the id is stable
  (N tenants = N billable units/day gross).
- Code updates are the sharp edge (measured runtime fact): **a cached name's
  code is immutable** — `get(sameName, () => differentCode)` keeps the old
  code; only a new name returns new code. So an engine-code change under a
  stable tenant id does **not** take effect; forcing it requires id rotation,
  which re-multiplies the creation meter (see §4).
- No platform script-cap consumption.

### Variant 2 — One static script per tenant (deployed via Wrangler / API)

- One named Worker script per tenant instead of a Dynamic Workers binding.
- Avoids the Dynamic Workers creation meter entirely.
- Hard constraint: **500-script cap** per account/zone context. 20 tenants
  consume 20/500 slots; the variant stops scaling at 500 tenants without
  additional accounts, script reuse, or a return to Variant 1.
- Same isolation boundary as Variant 1 (within-tenant bleed unchanged), plus
  deploy-pipeline cost: every engine-code rollout must redeploy N scripts (or
  collapse them onto one generic script — see §4 mitigation, which largely
  converges Variant 2 back toward a shared-code shape).

## 3. Cost model (published rates + one measured workflow)

Published rates used (no other fees introduced):

- Requests: 10 M/month included, then $0.30/M.
- CPU: 30 M CPU-ms/month included, then $0.02/M.
- Dynamic Workers: 1,000/month included, then $0.002 per worker per day.
- Workers Logs: 20 M events/month included, then $0.60/M.
- Memory: provider memory is never exposed and never billed (128 MB per
  isolate, fixed). Memory contributes $0 to both postures.

Measured workload used:

- One real workflow, parent-invocation meter: **1,757 ms CPU / 10,610 ms wall**.
  Wall clock is not billed; only CPU-ms is. Guest-CPU placement is unchanged by
  this ADR, so the CPU line below is identical for both postures.

Worked example: **20 tenants × 500 runs/month = 10,000 runs** (1 run = 1
attempt; evenly spread ≈ 333 runs/day over 30 days).

| Line | Per-attempt (`load` per attempt) | Per-tenant (`get` per tenantId) |
|---|---|---|
| Dynamic Workers gross | 10,000 unique workers/month ≈ 333 unique/day × $0.002 ≈ **~$20/month** | 20 stable ids reused daily = 20 units/day ≈ 20 × $0.002 × 30 ≈ **~$1.20/month gross** |
| Dynamic Workers net of 1,000/mo included | (10,000 − 1,000) × $0.002 = **~$18/month** (matches `ISOLATION-POSTURES.md` posture-D row basis) | 20 ≪ 1,000 included → **$0** while ids are stable |
| Requests (10,000) | 10,000 ≪ 10 M → **$0** | same → **$0** |
| CPU, parent side (10,000 × 1,757 ms = 17.57 M CPU-ms) | 17.57 M < 30 M → **$0** | same → **$0** |
| Boot latency (local, not billed) | ~2.1 s cold every attempt | first attempt ~2.1 s cold, rest **~2 ms warm** |
| Logs | formula only: billable = max(0, events − 20 M)/1 M × $0.60; unchanged by this ADR at equal verbosity | same |
| Memory | $0 (fixed 128 MB, unbilled) | $0 |

Net effect at this volume: the **only** billed delta between postures is the
Dynamic Workers creation line — **~$18/month vs ~$0/month**. CPU, requests,
logs, and memory are at or under free tier for the parent-measured portion in
both postures. Rotation (§4) adds back creation cost per rotation event
(1 extra unique worker per rotated tenant per day).

Free-tier unit note: published Dynamic Workers pricing mixes a monthly
included quantity (1,000/month) with a daily meter ($0.002 per worker per day).
The table applies the included quantity against unique-worker invocations, the
same basis as the `ISOLATION-POSTURES.md` ~$18/10k figure. If Cloudflare meters
the free tier as concurrent live workers rather than invocations, per-attempt
gross (~333 live/day average) still exceeds it and per-tenant (20 live) still
clears it — the verdict (per-attempt pays, per-tenant ~$0) is unchanged, only
the exact per-attempt net moves.

## 4. What breaks and mitigations

What breaks under per-tenant (all measured, not hypothetical):

1. **Within-tenant bleed is total.** Successor reads predecessor's builtins,
   module cache, env, `/tmp`+`/session` files, and credentials
   (`6b-within-boundary-bleed` FAIL, `5-creds-separation` FAIL). A hostile or
   buggy first attempt owns every later attempt of that tenant.
2. **Code updates do not propagate under a stable id.** Cached name keeps old
   code; only a new name picks up new code. Shipping engine fixes without id
   rotation leaves tenants running stale code.
3. **Concurrency within a tenant serializes or interleaves on shared state.**
   Concurrent attempts of one tenant share one isolate's globals; throughput
   and correctness both depend on clearing state that cannot be fully cleared
   (see `ISOLATION-POSTURES.md` §C: nested object graphs, C-level registries
   such as `codecs`, unrewritable baseline modules, self-disarming hooks).

Mitigations (none restores attempt isolation; all reduce blast radius or cost):

- **Serialization per tenant.** Run at most one attempt per tenant at a time
  (per-tenant queue / mutex) so concurrent attempts cannot race on shared
  globals. Cost: tail latency under burst; does not fix sequential bleed.
- **Periodic rotation.** Rotate the worker id on a schedule (e.g. daily) and
  after any failure/suspicion, bounding how many past attempts a successor can
  observe. Cost: 1 extra unique worker per rotated tenant per rotation day;
  cold boot (~2.1 s local) on the first attempt after rotation.
- **Generic script so code edits do not force id rotation.** Ship the cached
  worker as a **small generic loader** (fetch and execute the workflow payload per attempt)
  rather than baking engine code into the cached bundle. Engine-code edits then
  change the fetched payload, not the cached worker's code, so the tenant id
  stays stable and the creation meter stays flat. Residual: the fetched payload
  still executes in the same long-lived isolate — this fixes the *staleness /
  rotation-cost* problem, not the *bleed* problem. Payload integrity
  (provenance/pinning) becomes load-bearing.
- **Per-attempt secret hygiene.** Never place long-lived tenant credentials
  where the isolate persists them (env, builtins, module attrs, fs). Mint
  least-privilege per-attempt tokens upstream; revoke after attempt end. Limits
  credential-reuse blast radius; does not sanitize other channels.
- **Opt-in per tenant, default deny.** Default new / untrusted tenants to
  per-attempt; allow per-tenant only with recorded operator acceptance of the
  relocated boundary (§1). Mixed fleet is expected.

## 5. Non-goals

- Not restoring attempt-level isolation inside a tenant. Best-effort manual
  reset (posture C, 13/19) is documented-unsound and must never be presented
  as a mitigation.
- Not changing the cross-tenant guarantee (already PASS) or the memory posture
  (fixed 128 MB, unbilled).
- Not re-litigating sub-interpreters / fresh-Pyodide-per-attempt (posture B):
  measured as not available in this runtime.
- Not claiming cloud timings or cloud bills from local timings. All
  latency figures (~2.1 s cold, ~2 ms warm) are local `workerd` measurements.
- Not setting rotation intervals, concurrency limits, or the static-vs-dynamic
  tenant ceiling here — those are follow-up decisions per fleet size (note the
  500-script cap for Variant 2).

## 6. Residual risks and acceptance evidence still needed

Residual risks:

- Within-tenant hostile takeover of later attempts (credential theft,
  exfiltration via persisted state, dispatch seizure — cf. unproven canary-11
  note in `ISOLATION-POSTURES.md`).
- Stale-code execution if rotation/generic-loader discipline slips.
- Single-tenant noisy-neighbor / state-corruption under concurrent attempts if
  serialization slips.
- Cost-model drift: cloud CPU-ms and log-event volumes for real guest
  workloads are unmeasured; the $0 CPU/requests lines above rest on one
  parent-side sample (1,757 ms CPU) and free-tier headroom, not on fleet data.
  Measured allowance consumption and the account-wide spend policy are recorded
  in `SPEND-GUARD.md`; the policy covers requests and CPU, **not** Dynamic
  Worker creation, which is the dominant line.

Acceptance evidence still needed (none of this is established by this ADR):

1. ~~Re-run of the hostile canary suite against **cloud** workers for the chosen
   variant~~ — **closed 2026-11-13**: 22/22 checks on the deployed per-tenant
   supervisor, cross-tenant PASS and within-tenant residue FAIL as predicted
   (`CLOUD-TENANT-ACCEPTANCE.md`).
2. ~~Cloud-metered confirmation of the creation rule (`load`-with-no-id = one
   billable worker per invocation; `get`-stable-id = one per tenant per day)~~
   — **closed 2026-11-13**: stable ids +0 creations for 3 invocations;
   `LOADER.load` +3 creations for 3 invocations, repeated (+3 again)
   (`CLOUD-TENANT-ACCEPTANCE.md`).
3. Cloud-metered CPU-ms per representative workflow (parent + guest) over a
   full billing window, plus log-event volume, to confirm the free-tier
   headroom assumed in §3. **Still open** — only creation and request meters
   were read back.
4. Rotation + generic-loader drill: engine-code update with stable tenant ids
   takes effect without id rotation; rotation itself bounds bleed to the
   rotation window. **Still open** — a redeploy of the constant script was not
   measured as a rotation event.
5. ~~Serialization proof: concurrent same-tenant attempts do not corrupt or
   observe each other beyond the already-accepted sequential bleed.~~
   **Closed 2026-11-13** in cloud: two concurrent same-tenant attempts did not
   overlap; two tenants ran in parallel (546 ms wall for 2 × 500 ms).
