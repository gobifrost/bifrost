# Prod-like qualification path for Pyodide Bifrost Workers

What follows is a staged path from "cloud-accepted runtime" to "legitimately
tested against a real workspace". The distinction matters: today the Worker has
been proven on **real Cloudflare** but against an **attempt-supplied module
store** and a **placeholder credential**. That proves the runtime, not the
integration.

## Fidelity ladder

| Under test | Fixture (today) | Staging | Production canary |
|---|---|---|---|
| Engine + SDK code | real | real | real |
| HTTP transport to the Bifrost API | in-supervisor stub | real HTTPS | real |
| Credentials | `DUMMY_CREDENTIAL` | signed, short-lived run token | signed, short-lived |
| Workspace modules | attempt-supplied map | S3/Redis via the real gateway | real |
| Effects (tables/files/integrations) | none | synthetic tenant | real tenant, read-only |
| Claim / terminal / replay | none | real | real |
| Logs and error paths | local capture | real | real |
| Load | single attempt | sequential + concurrent | real traffic |

Each column is a different claim. The current record only supports column one.

## Stage 1 — Staging stack, synthetic tenant (highest value per unit of risk)

1. Boot the dev stack from this worktree (`./debug.sh up`) and create a
   synthetic org/tenant, e.g. `cf-qualification`, with a copy of two or three
   `bifrost-workspace-next` Solutions installed (start with read-only ones:
   inventory, reporting).
2. Expose the API over HTTPS with `netbird expose` (the dev stack already
   supports it). The Worker's egress allowlist names exactly that host.
3. Deploy the **per-tenant** supervisor to Cloudflare with that API origin and a
   signed dispatch credential, auth-gated and expiring.
4. Run attempts end to end: claim → gateway module fetch → child execution →
   terminal result → logs.

This is the first colum-two measurement. It exercises the real engine, real SDK
transport, real workspace content and real persistence.

**What it still does not prove:** provider eviction, production load, or
per-execution CPU.

## Stage 2 — Differential replay (the actual qualification harness)

For N captured attempts, run the same input on **both** backends and compare:

- terminal status and error class
- returned value
- emitted logs (redacted)
- table/file effects (count + content hash)
- subrequest count and wall time

Real attempts from staging are the input; Linux is the oracle. This is how you
find the failure modes fixtures miss — encoding differences, clock, locale, SDK
edge cases, module import order.

Exit criteria to define up front, for example: 200 sequential + 20 concurrent
attempts, 0 result diffs, 0 cross-tenant canary violations, identical effect
hashes, no orphaned rows.

## Stage 3 — Production canary (only after Stage 2 is clean)

One solution, one tenant, read-only integrations, behind a per-tenant route flag
with a kill switch that routes the tenant back to Linux. Compare against the
Linux result for 24–72 h. This is the only stage that proves production traffic,
and it needs explicit production authorization.

## Prerequisites: five seams, and where they stand

| Seam | Status |
|---|---|
| Real module gateway (S3/Redis instead of the attempt store) | **Not built.** Prototype deliberately serves the attempt-supplied map; path-level audit is proven, transport is not. |
| Signed dispatch credential with expiry/refresh | **Not built** in the prototype (token held in the supervisor). Upstream `remote/protocol.py` work on the branch is the start. |
| Claim/terminal/recovery through the opaque runtime profile | Partially built on the branch (`remote_pool.py`, `runtime_profile`); flagged as a shared seam owned elsewhere. |
| Egress enforcement at the child/gateway | Gate exists in the executor; not wired to the per-tenant path for real hosts. |
| Retry after provider eviction (idempotent attempt) | **Not built.** Eviction semantics are the biggest unknown for prod-like reliability. |

Without the first two, Stage 1 is a demo. They are the real work.

## Cost and safety controls

- **Spend policy is not enough.** It covers requests + CPU, not Dynamic Worker
  creation (measured: `SPEND-GUARD.md`). Per-tenant caps must be enforced by the
  dispatcher before dispatch.
- **Use stable tenant ids.** A staging tenant that reuses ids costs 0 creations
  after the first day; the acceptance suite's per-run suffixes are why the
  account sits at 105/1,000.
- **Do not set `usage_block` on the shared account.** It is account-wide and
  would take production Workers down (`SPEND-GUARD.md`).
- **Consider a second Cloudflare account** for staging: it isolates the
  1,000-creation allowance, the spend policy and the blast radius. That is an
  account/contract decision, not a technical one.
- **Kill switch**: per-tenant route flag flipping the backend back to Linux.

## What would invalidate the result

- Running against a stubbed gateway and calling it integration-tested.
- Comparing outputs without comparing effects (a workflow that returns the same
  value but writes different rows is a failure).
- Testing only first-run paths (cold, empty state) and never retries or
  evictions.
- Reporting eligibility as coverage (`WORKSPACE-NEXT-CENSUS.md`: 317/317 is
  routing, not execution).

## Recommended first move

Stage 1, with only the two read-only Solutions, and build the **real module
gateway** as the first seam — it is the difference between testing the runtime
and testing the platform. Everything else in Stage 1 can use existing
components.
