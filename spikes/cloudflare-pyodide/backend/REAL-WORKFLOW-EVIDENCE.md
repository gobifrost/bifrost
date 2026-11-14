# Real workspace workflows on a deployed Cloudflare Worker

Status: **passing**. Real solution workflows from `bifrost-workspace-next`
(`37581f6`) run on the deployed per-tenant Cloudflare worker and produce the
same results as the Linux worker.

This is the bar the spike set for itself: synthetic probes prove the runtime
boots; a workflow that a real solution ships proves the platform contract.

## What ran

Both workflows are unmodified solution source, uploaded with `POST /api/files/write`
and pinned with `GET /api/sdk/modules/{path}` (the hash is the manifest pin).

| Workflow | Source | Behaviour |
|---|---|---|
| `docs_ensure_file_grant` | `solutions/bifrost-docs/functions/file_grants.py` | Real **write**: reads a local module (`modules/migration_core.py`), computes a uuid5 grant id, reads then inserts into table `docs-file-org-grants` |
| `docs_list_organizations` | `solutions/bifrost-docs/functions/catalog.py` | Real **read**: `organizations.list()` over the SDK, provider/customer branch on `context.organization` |

They cover the three things a synthetic case cannot: a local-module import
closure, a genuine table write, and a multi-call SDK resource outside
`/api/sdk/`.

## Results

Linux worker (registered workflow, `POST /api/workflows/execute`):

```
docs_ensure_file_grant
  Success {"path_prefix":"00000000-0000-0000-0000-000000000002",
           "organization_id":"00000000-0000-0000-0000-000000000002"}

docs_list_organizations
  Success {"caller_mode":"picker",
           "own_organization_id":"00000000-0000-0000-0000-000000000002",
           "organizations":[{"id":"00000000-0000-0000-0000-000000000002","name":"Provider"}]}
```

Deployed Cloudflare worker (`POST /` on
`bifrost-pyodide-tenant-ddf316c1.midtown-technology-group.workers.dev`,
engine mode, DO token broker), 3 runs each on the final build:

| Workflow | Run | Child reused | Wall | Status | Result |
|---|---|---|---|---|---|
| `docs_ensure_file_grant` | 1 | no | 6244 ms | Success | matches Linux |
| | 2 | no | 4845 ms | Success | matches Linux |
| | 3 | **yes** | 1983 ms | Success | matches Linux |
| `docs_list_organizations` | 1 | no | 6470 ms | Success | matches Linux |
| | 2 | no | 4785 ms | Success | matches Linux |
| | 3 | **yes** | 1799 ms | Success | matches Linux |

A warm child is ~3x faster than a cold one, which is the per-tenant persistence
argument in one number.

### Write effect, not just return value

A matching return value does not prove the write landed, so the row was deleted
before the Cloudflare run and read back after it:

```
DELETE /api/tables/docs-file-org-grants/documents/57791d0d-...   -> 204
GET    .../57791d0d-...                                          -> 404   (cold)
POST   cloud run docs_ensure_file_grant                          -> Success
GET    .../57791d0d-...                                          -> 200
       data={"path_prefix":"...0002","organization_id":"...0002"}
POST   cloud run again                                           -> Success (same grant returned)
```

The grant id is `uuid5(bifrost-docs:<org>:file-org-grant:<org>)`, so an idempotent
second run cannot create a duplicate by construction.

## Three defects this exposed

1. **Unconditional `src.config` import** (upstream #1396, fixed in `c1a56fc63`).
   The port kept `from src.config import get_settings` above the `public_url`
   guard, so a runtime without server settings failed before the `try` and
   reported `Failed / ModuleNotFoundError`. Fixed upstream, test now blocks the
   module rather than patching `get_settings` (which imported it).

2. **Capability allowlist too narrow.** `organizations.list()` calls
   `/api/organizations`, which `apiTarget` refused with `403`. The boundary is a
   capability check, not the authorization gate — the run token is still the
   authority — so it now admits the SDK resource roots
   (`/api/organizations`, `/api/configs`, `/api/files`, `/api/integrations`,
   `/api/tables`, `/api/events`, `/api/forms`, `/api/agents`, `/api/agent-runs`,
   `/api/sdk/`, `/auth/refresh`) and still refuses dispatch/execution routes.
   Matching is segment-exact, so `/api/tables-admin` stays out.

3. **The reusable child kept the previous attempt's credential.** This one only
   appears on a *persistent* worker, and it is the important finding:
   - the SDK caches its client per thread (`bifrost.client._thread_local`);
   - the engine's `_set_process_engine_credentials` updates **env only**;
   - so attempt 2 reused attempt 1's client with attempt 1's run handle;
   - the broker had already deleted that handle, `TenantApi` answered `401`, and
     the failure surfaced as `Engine-local module fetch returned 401`.

   Measured, before the fix, on the deployed worker: every failing attempt
   carried an **earlier** attempt's handle (`run_1a5a9f00`, `run_c44b659d`), never
   its own. It is a per-tenant-child invariant, so the fix belongs at the attempt
   boundary in the child entry: hand the new credential to the already-cached
   client before the run starts (`_install_cached_access_token`, the same helper
   `bifrost._service_runtime` uses for in-process token handoff). The alternative
   reading — that the gateway was at fault — was wrong; the gateway never saw the
   request.

## Regression coverage

- `dev/staging/differential.mjs` case `sdk-reused-child`: two engine attempts on
  **one** tenant child, both required to be `Success` and to match Linux.
  Verified to bite: with the handoff removed the second attempt fails
  (`WorkflowLoadError`), with it both pass. Full run: **7/7 cases match**.
- `dev/tenant-workers/generic-code.test.mjs`: the child entry performs the
  credential handoff before `run_authorized_attempt`.

## How the attempt was driven

The harness assembles the engine request the API would send (`tenant_id`,
`action: "engine"`, `requirements.modules`, `manifest` pins, full engine
`context` including `caller`, `organization`, `content_hash`, `parameters`).
Everything after that is the real stack: real Bifrost engine, real SDK, real
module resolution, real API authorization via the run token. Not exercised here:
dispatch from `/api/workflows/execute` to a Cloudflare-backed worker (route flag),
queuing/retry, cancellation, and execution-log write-back through the platform.

## Residual limits

- One tenant, one caller identity, one organization.
- Effects compared for one workflow (row readback); no general row/file diffing.
- `@service` mode is not supported by the companion and is refused.
- Engine-mode module pins are trusted from the request; the data-only path
  verifies digests, engine mode does not yet.
- Warm/cold timings are single-sample observations, not a benchmark.
