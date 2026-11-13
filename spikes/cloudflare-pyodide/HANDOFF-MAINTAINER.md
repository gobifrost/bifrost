# Handoff: Cloudflare Pyodide execution spike — maintainer synthesis

Scope: synthesis only, drawn from `backend/TRUST-MODEL.md`, `backend/ELIGIBILITY.md`,
`backend/DUTY-SHAPES.md`, `backend/DEPENDENCY-CENSUS.md`, `backend/CPU-TELEMETRY.md`,
`backend/ISOLATION-POSTURES.md`, `backend/ADR-PER-TENANT.md`, `ACCEPTANCE.md`.
Every number below appears in one of those documents. Sample-size and evidence-class
caveats are inline. Nothing here authorizes production routing; Linux stays the default.

## 1. Verdict

**Reviewed (trusted-author) short I/O workflows: experimental YELLOW.**
`ACCEPTANCE.md` rates the useful trusted subset experimental YELLOW, and
`backend/TRUST-MODEL.md` (accepted 2026-11-12) lifts the prior RED on trusted-author
workloads: org publishers are trusted operators (AD/GPO analogy), least-privilege gates
stay enforcing, and non-cooperative CPU termination is retired as a ship-blocker
(operator's bill, operator's timeouts). The remaining RED is for untrusted authorship
and platform-hard limits. **Per-tenant persistent workers: PROPOSED in this document, but now cloud-accepted** —
`backend/CLOUD-TENANT-ACCEPTANCE.md` (2026-11-13) closes the isolation, creation-meter and
serialization acceptance items on a deployed worker; within-tenant bleed remains measured
FAIL, so the boundary relocation stands exactly as described below. **Arbitrary untrusted code:
RED** — only per-attempt fresh isolates pass the full hostile canary suite (19/19, local
workerd), and deployed acceptance for that posture is incomplete with the cloud qualification
window retired. **Frontend hosting: shipped separately** — PR #1330 landed the edge hosting
recipe upstream; none of the source documents here contains frontend-hosting measurements.

## 2. What is proven, by evidence class

**Cloud deployed** (ran on Cloudflare; no billing confirmation):

- 5/5 benchmark workloads green with full gates (`backend/ELIGIBILITY.md`); the deployed
  frontier behind that claim is five fresh children, 15 correctness samples, 5.2–10.5 s
  measured provider round trips, CPU-only clock not advancing, CPU/peak/billed cost null
  (`ACCEPTANCE.md`).
- Multi-minute poll workflows hold one invocation open: 8 × 35 s → 246.1 s engine /
  254.9 s round trip, Success, 10/12 child subrequests; 9 × 40 s → 321.2 s / 332.5 s,
  Success, 11/12. No edge timeout at 100 s. `asyncio.sleep` did not trip the 1000 ms CPU
  budget (`backend/DUTY-SHAPES.md`).
- Data providers through the same path: miss 280 ms engine / 13.2 s round trip (user code
  ran); hit 99 ms / 9.1 s (cached, user function not executed)
  (`backend/DUTY-SHAPES.md`).
- `requests` 2.34.2 genuinely usable in the real Worker child (HTTP 200 through the
  gateway bridge, child never held the bearer); `msal` 1.39.0 imports and builds
  `ConfidentialClientApplication` against a no-network OIDC stub
  (`backend/DEPENDENCY-CENSUS.md`, spike `05439e42c`).
- Single-step `agent.run`: 1 iteration, reported $0.0000344 (`backend/ELIGIBILITY.md`;
  single reported figure, no distribution).

**Cloud observed, single sample** (OTel/Logpush probe, spike `1968d106a`, observability
reverted afterwards):

- One `json_transform` + one `config.get` workflow: parent `$workers.cpuTimeMs` 1757 ms,
  `$workers.wallTimeMs` 10610 ms. Dynamic child appears only as a nested OTel span
  (`durationMS` 9980, CPU/wall attributes absent); 5 invocation records in the window were
  all the parent script. Log-marker correlation unproven: no `console.log` records appear
  for any worker in the account (`backend/CPU-TELEMETRY.md`).

**Local workerd only** (`workerd` 2026-10-23, wrangler 4.156.0; not cloud evidence):

- Isolation postures, 39 attempts per posture: fresh `LOADER.load` per attempt 19/19
  canaries PASS; one long-lived worker 1/19; cached + manual reset 13/19 (fails four
  independent ways: nested object graph, `codecs` C-registry, unrewritable baseline
  modules, self-disarming reset hook); one-Worker-per-tenant 3/19
  (`backend/ISOLATION-POSTURES.md`).
- Warm attempt ~2 ms vs ~2.1 s cold Python boot (local); `backend/TRUST-MODEL.md` cites
  ~5 s fresh-isolate init — two different local figures, do not conflate.
- Importable set and bundle arithmetic are build-measured: bundle ships 11 distributions;
  adding requests/urllib3-2.8.0/charset-normalizer-3.5.2/msal/PyJWT-2.15.1 adds
  1,996,147 B (+13.0%), 472 → 607 modules, upload 14,986 → 16,936 KiB; ~15 MiB against
  the 64 MiB Worker limit (~47 MiB headroom); no existing module key changed
  (`backend/DEPENDENCY-CENSUS.md`).

## 3. Remaining blockers, each with its measured cause

1. **Durable execution missing by platform design.** No durable sleep; HTTP `waitUntil`
   bounded to 30 s after response/disconnect; persistent/supervised services rejected by
   the runtime (`backend/TRUST-MODEL.md`, `ACCEPTANCE.md`, `backend/ELIGIBILITY.md`).
2. **Subrequest budget is the real bound, and routing is unbuilt.** At `subRequests: 12`
   a 10th poll is refused once module resolution and entry-module load are counted; the
   dispatcher that must estimate request counts and pick a backend does not exist, and
   wall-clock routing is explicitly forbidden (`backend/DUTY-SHAPES.md`,
   `backend/ELIGIBILITY.md`).
3. **Provider CPU/memory accounting reads null.** In-runtime `time.process_time()` raises
   `RuntimeError` in Pyodide; provider CPU/memory are `null`; per-tenant cost attribution
   stays estimate-based (`backend/ELIGIBILITY.md`, `backend/DUTY-SHAPES.md`,
   `backend/CPU-TELEMETRY.md`).
4. **Memory is a fixed 128 MiB isolate, never billed, never measurable per execution.**
   A local 160 MiB allocation succeeded, so emulator evidence does not qualify deployed
   128 MiB enforcement; `peak_memory_bytes` has no Cloudflare equivalent
   (`backend/CPU-TELEMETRY.md`, `ACCEPTANCE.md`).
5. **Dependency closure is allowlist-only.** No `pip`; pre-bundled Pyodide wheels only.
   Outside the curated set (`httpx`, `pydantic`+core, `python-dotenv`, `PyYAML`, `redis`,
   `requests`, `urllib3`, `charset-normalizer`, `msal`, `PyJWT`, `certifi`, plus
   transitive/ glue packages) imports fail with `ModuleNotFoundError`; `cryptography`,
   `azure-*`, `boto3`, `pandas`, `paramiko` and the rest stay on Linux. msal real token
   acquisition additionally needs an allowlisted egress host and `cryptography` for
   RSA/EC signing (`backend/DEPENDENCY-CENSUS.md`, `backend/ELIGIBILITY.md`).
6. **Per-tenant posture fails within-tenant isolation (measured).** Successor attempts read
   predecessor builtins, module cache, env, `/tmp`+`/session`, and credentials
   (`6b-within-boundary-bleed` and `5-creds-separation` FAIL); cached-name code is
   immutable so engine updates need id rotation; concurrent same-tenant attempts share one
   isolate's globals (`backend/ISOLATION-POSTURES.md`, `backend/ADR-PER-TENANT.md`).
7. **Canary 11 unresolved and all posture timings local.** Class-attribute dispatcher
   seizure reported `seized=true` without taking over dispatch: unproven, not proven-safe;
   every latency figure is local, not cloud-metered (`backend/ISOLATION-POSTURES.md`).
8. **Cloud acceptance authority retired.** The bounded 2026-11-02 private window closed
   after 13 reserved requests with public closure verified and secrets deleted; further
   cloud invocations, spending, or grants need fresh explicit authority
   (`ACCEPTANCE.md`).

## 4. Cost position

Published Workers Paid rates (verified 2026-11-12): requests 10 M/month included then
$0.30/M; CPU 30 M CPU-ms/month then $0.02/M; Dynamic Workers 1,000/month included then
$0.002 per worker per day; Workers Logs 20 M events/month then $0.60/M; Logpush 10 M then
$0.05/M. Per-execution formula: `cpu_ms * 0.02/1e6 + requests * 0.30/1e6 + creation *
0.002` (`backend/CPU-TELEMETRY.md`).

- **Creation meter dominates.** `LOADER.load(code)` with no id counts as one Dynamic
  Worker per invocation: 10,000 executions/month ≈ $18 creation alone; 100,000/month ≈
  ~$198 creation vs ~$2.91 CPU overage (100,000 × single-sample 1757 ms = 175.7 M
  CPU-ms); 30 M included CPU-ms lasts ~17,000 executions/month at that single-sample
  rate. At low volume the fresh model is cheap; at high volume creation exceeds CPU by
  ~90× (`backend/CPU-TELEMETRY.md`).
- **Per-tenant alternative (modeled, not metered).** Worked example 20 tenants × 500
  runs/month = 10,000 runs: per-attempt ≈ $20/month gross (~$18 net of the 1,000
  included) vs per-tenant ≈ $1.20/month gross, $0 net while ids are stable; CPU,
  requests, logs, memory identical between postures at this volume. Rotation adds 1 extra
  unique worker per rotated tenant per rotation day; Variant 2 (static script per tenant)
  avoids the creation meter but hits the 500-script account cap
  (`backend/ADR-PER-TENANT.md`).
- **Every billed figure above the published rates rests on one parent-side sample**
  (1757 ms CPU) plus free-tier headroom, not fleet data; cloud-metered confirmation of
  the creation rule, the free-tier unit (invocations vs concurrent workers), guest CPU,
  and log-event volume is explicitly still needed (`backend/ADR-PER-TENANT.md` §6).
  Measuring cost itself costs: full-rate observability emits billable log events, so
  production accounting should sample or aggregate (`backend/CPU-TELEMETRY.md`).

## 5. What a maintainer should review first

1. `spikes/cloudflare-pyodide/backend/ELIGIBILITY.md` — the dispatch contract: five
   declaration-driven inputs, the subrequest-not-duration rule, and the eligible/Linux
   table. Any router implementation starts here.
2. `spikes/cloudflare-pyodide/backend/TRUST-MODEL.md` — the decision that reframes gates
   as least-privilege and retires termination proof; sets what is operator risk vs
   platform blocker.
3. `spikes/cloudflare-pyodide/backend/ISOLATION-POSTURES.md` — why only per-attempt fresh
   isolates pass (19/19 vs 1/19, 13/19, 3/19) and why manual reset must never ship.
4. `spikes/cloudflare-pyodide/backend/CPU-TELEMETRY.md` — the single-sample 1757 ms
   figure, the supervisor-not-workflow CPU caveat, the creation-meter math, and the
   unproven log-marker correlation.
5. `spikes/cloudflare-pyodide/ACCEPTANCE.md` — the full ledger: what is locally proven,
   what is deployed-but-incomplete, the retired cloud window, and the Linux-default rule.

## 6. What was NOT done and why

- **No router implementation.** Eligibility inputs are defined; who computes the
  subrequest estimate and picks the backend is unbuilt (`backend/ELIGIBILITY.md`).
- **No production deadline enforcement.** The qualification fixture admits declared
  `timeout_seconds ≤ 30` and records `workflow_deadline: null`; enforcement is a
  dispatcher concern, unproven (`backend/DUTY-SHAPES.md`).
- **No per-execution CPU/memory delivery.** Post-hoc Logpush/OTel write-back is designed
  (idempotent, never re-classifying terminal results) but not built; memory has no
  equivalent at all (`backend/CPU-TELEMETRY.md`).
- **No per-tenant rollout.** The ADR is documentation-only; its five acceptance items
  (cloud canary re-run, metered creation-rule confirmation, metered CPU/log volumes,
  rotation/generic-loader drill, serialization proof) are all outstanding, and the cloud
  window needed to run them is retired (`backend/ADR-PER-TENANT.md`, `ACCEPTANCE.md`).
- **No allowlist expansion beyond requests/msal.** Each further package needs its own
  measured Pyodide import test; native-extension packages are Linux by construction
  (`backend/DEPENDENCY-CENSUS.md`).
- **No deployed canonical persistence/terminal execution.** Deployed runs prove transport
  and SDK behavior, not cloud error/terminal persistence, dispatch-credential
  issuance/revocation timing, or physical termination (`ACCEPTANCE.md`).
