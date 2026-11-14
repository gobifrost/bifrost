# Stage 3 — production canary (shadow)

Status: **live**. A read-only production workflow runs every 15 minutes on the
production Linux worker and on the deployed Cloudflare per-tenant worker, and
the two results are compared. Evidence is appended to
`/opt/bifrost-canary/runs.jsonl` on `vm-mtg-bifrost-prod-01`.

Authorization: explicit ("Stage 3 canary away"). Scope kept deliberately narrow:
one read-only workflow, one identity, no customer org writes, no live traffic
routing, instant kill switch.

## What is deployed

| Piece | Value |
|---|---|
| Production | `https://bifrost.midtowntg.com` (`BIFROST_ENVIRONMENT=production`, `2.0.1-dev.1794442566`) |
| Host | `vm-mtg-bifrost-prod-01`, `/opt/bifrost-pax8` compose, pinned image digests |
| Canary worker | `bifrost-pyodide-canary-ddf316c1.midtown-technology-group.workers.dev` |
| Worker API origin | `https://bifrost.midtowntg.com` (verified: `/__gateway_probe` → `{"configured":true,"status":200}`) |
| Worker secret | its own `SPIKE_AUTH`, `0600` on the box, rotated once after a leak into a log line |
| Worker window | `SPIKE_EXPIRES_AT` ≈ 48 h |
| Workflow | `docs_list_organizations` (solution `44832b44-…`, module `functions/catalog.py`) — reads only, `organizations.list()` |
| Identity | device-flow session for the provider-org platform admin; refresh token at `/etc/bifrost-canary/last.json` `0600` |
| Runner | `/opt/bifrost-canary/canary.py` `0700` (`dev/staging/prod-canary.py`) |
| Schedule | `bifrost-canary.timer`, every 15 min |

Nothing in the application stack was modified: no image change, no restart, no
compose edit, no migration. The only production additions are this runner file,
a systemd timer, and a device-flow session.

## Evidence shape

Each record keeps **hashes, not data** — no customer names are stored:

```json
{"ts":"2026-11-14T14:26:12Z","workflow":"docs_list_organizations",
 "module_sha256":"6d393fe8…","linux_status":"Success","cloud_status":"Success",
 "match":true,"same_result":true,"org_count":297,
 "linux_ms":2204,"cloud_ms":6567,"worker_reused":false,"cloud_http":200}
```

First results on production:

| Cycle | Linux | Cloudflare | Match | Linux ms | Cloud ms | Reused |
|---|---|---|---|---|---|---|
| 1 (pre-fix) | Success | Failed `ExecutableNotFound` | no | 2 064 | 11 746 | no |
| 2 (fixed) | Success | Success | **yes** | 2 134 | 2 396 | yes |
| 3 (timer) | Success | Success | **yes** | 2 204 | 6 567 | no |

297 organizations on both backends, identical `caller_mode: "picker"` and
canonical result hash.

## Two production-only defects found

Both are invisible in staging and neither is a Bifrost defect:

1. **Cloudflare edge `1010` on the stock Python user-agent.** The production
   host's `Python-urllib/3.13` signature is blocked at the edge (`403 error code:
   1010`); `curl`, a browser UA, or any explicit UA passes. The runner now sends
   `User-Agent: bifrost-canary/1.0`. Worth knowing for any server-side caller of
   a `workers.dev` endpoint.
2. **Solution-scoped module paths are doubled if you pass the storage path.**
   The dispatcher passes the *logical* `file_path` (`functions/catalog.py`) plus
   `solution_id`; `get_module_sync` maps it into `_solutions/<id>/…`. Passing the
   already-scoped path makes the child look for
   `_solutions/<id>/_solutions/<id>/…`, resolve nothing, and fail as
   `ExecutableNotFound` with **zero** gateway traffic — a misleading error for a
   path bug. `file_path` = logical, `requirements`/`manifest` = storage path.

## Kill switch

Immediate, in order of preference:

```bash
rm -f /opt/bifrost-canary/ENABLED          # runner exits 0 without doing anything
systemctl disable --now bifrost-canary.timer   # stop the schedule
```

And at the worker, either alone is sufficient:

```bash
WORKER_NAME=bifrost-pyodide-canary-ddf316c1 \
STAGING_API_ORIGIN=https://bifrost.midtowntg.com SPIKE_EXPIRES_AT=0 \
cf deploy --secrets-file <canary secrets>          # every request fails closed
```

Teardown at the end of the window: stop the timer, `rm -rf /opt/bifrost-canary
/etc/bifrost-canary`, disable the unit files, redeploy the worker with expiry 0
(or delete it), and stop the device-flow session (`/auth/logout` /
`/auth/revoke-all` is NOT run — only the session's own revocation is needed).

## What this does and does not prove

**Proves:** the Cloudflare runtime executes a real production workflow, resolving
a solution-scoped module through the real module transport, authenticating with a
real production token, reading real production data, and returning a result
identical to the Linux worker — repeatedly, with a warm child.

**Does not prove:** live dispatch. Production has no per-tenant execution-backend
flag; `/api/workflows/execute` always runs on Linux. Routing real traffic needs
the dispatch/claim seam (`remote_pool` + runtime profile, unmerged #1399), a
route flag, and retry-after-eviction semantics. Until that exists this is a
shadow canary, and calling it "production routing" would overstate it.

Also not covered: retries after provider eviction, concurrent tenants, sustained
load, effect comparison beyond this workflow's reads.
