# Staging harness (Stage 1 / Stage 2 / Stage 3)

## Status 2026-11-13

| Stage | State |
|---|---|
| 1 — real API integration | **Proven**, deployed Worker through a named tunnel |
| 2 — differential replay | **5/5 cases match** (result, failure class, SDK, logs) |
| 3 — production canary | **Prepared, not executed** (needs production authorization) |

## Infrastructure

| Piece | Value |
|---|---|
| Bifrost API stack | pve-t340 container `128` (`bifrost-pyodide-test-01`), `172.16.15.181:18000` |
| Tunnel | `bifrost-stage` (`06e4a44f-47ad-424e-b261-ce942730afee`) |
| Public origin | `https://bifrost-stage.midtowntg.com` |
| Worker | `bifrost-pyodide-tenant-ddf316c1`, version `a21f94cf` |

### Stage 1 result (deployed, real API)

```
POST → 200
result:    {"ok": true, "doubled": 42}
recorded:  workflows/helper.py, workflows/probe.py
api_calls: 3 (modules-resolve + 2 fetches, all 200)
gateway probe from the Worker: {"configured": true, "status": 200}
```

The earlier `trycloudflare.com` 502 does not occur on a named tunnel — the
Worker reached the API normally.

### Stage 2 result

`differential.mjs` runs each case twice: Cloudflare per-tenant supervisor and the
Linux worker, then compares the result and the failure class. SDK cases use
`action: "engine"` on Cloudflare and `/api/workflows/register` + execute on Linux.

```
PASS imports-helper  — cloud=200/ok               linux=Success
PASS stdlib-only     — cloud=200/ok               linux=Success
PASS raises          — cloud=502/dynamic-runtime  linux=Failed
PASS sdk-tables-list — cloud=Success              linux=Success
PASS logs-marker     — cloud=Success              linux=Success
DIFFERENTIAL: ALL CASES MATCH
```

Run against the **deployed** worker with the DO broker. The `logs-marker` case
asserts the workflow log line appears on both backends, so the log path is
compared, not just the return value.

The SDK case is `await tables.list()` through the real engine on both sides; results
compare canonically (key order differs). The error case is deliberate: both fail, and
the harness compares failure *class*.

Two API contracts discovered by reading `api/src/routers/workflows.py`:

- **Registration** is `POST /api/workflows/register` (`path`, `function_name`), not
  `POST /api/workflows` (405).
- **Uniqueness is `(organization, function_name)`** — a second decorated `main`
  returns 409 even at a different path. The harness names each SDK case
  `case_sdk_<run>`.
- Workflows execute by **UUID**; a `path::function` reference does not resolve for
  execution.

## Engine mode (real SDK workflows)

`action: "engine"` runs the **real Bifrost engine** in the constant per-tenant
child (`tenantEngineCode` → `runtimeCode`), with outbound routed through the
stateless `TenantApi` stub (virtual host rewrite + `apiTarget` path allowlist).

Verified against the staging stack on 2026-11-13:

```
{"status":"Success","result":{"count":0,"kind":"list"},
 "execution_context":{"user_id":"dbb5617a-…","email":"dev@gobifrost.com",
                      "scope":"GLOBAL","workflow_name":"sdk_case"}}
```

That is `await tables.list()` executing through the real SDK, against the real
API, from a cached per-tenant child.

### Credential posture (DO broker)

Engine mode is now **credential-free inside the child**:

1. The supervisor mints an opaque handle (`run_<uuid>`) and stores the
   attempt-scoped token in a Durable Object (`RunTokenBroker`) keyed by handle,
   with a 10-minute expiry.
2. The child's `context.engine_token` is the handle, not the token.
3. `TenantApi` resolves the handle through the DO and swaps in the real token on
   the way out. The handle is deleted in a `finally` after the attempt.

Verified: `GET /auth/me` with `Bearer run_1111…` → **401**, with the real token →
**200**. The child's credential is not usable anywhere else.

Remaining difference from the data-only mode: engine mode does not re-verify
module pins (the engine fetches modules through the stub); the stub constrains
paths via `apiTarget` but is not a full egress allowlist.

Consequences: use engine mode for trusted-operator workloads; keep the
data-only mode where the credential-free/pinned posture matters. A Durable
Object token broker (handle in child, token in DO) would restore the
credential-free posture for engine mode and is the documented follow-up.

### Remaining

- **Effect comparison** in the differential (row/file hashes, not just results).
- **Pin verification in engine mode** (the data-only mode already does it).
- Stage 3 remains behind the production gate.

## Stage 3 — prepared, not executed

A production canary needs, at minimum:

1. A per-tenant route flag that sends one tenant to Cloudflare and a kill switch
   that sends it back to Linux.
2. A read-only solution and a dedicated tenant, no external writes.
3. Production authorization — this is a reserved gate; the harness is ready but
   nothing in production has been touched.

## Reproduce

```powershell
# 1. API stack on 128 (persistent, isolated project)
docker compose -f docker-compose.debug.yml -f docker-compose.staging.yml -p stage128 up -d api worker

# 2. Connector (token is on pve at /root/bifrost-stage-tunnel.token)
ssh pve-t340 'TUNNEL_TOKEN=$(cat /root/bifrost-stage-tunnel.token) nohup cloudflared tunnel run >/root/bifrost-stage-tunnel.log 2>&1 &'

# 3. Deploy the supervisor against the public origin
$env:STAGING_API_ORIGIN='https://bifrost-stage.midtowntg.com'
$env:SPIKE_EXPIRES_AT='<epoch-ms>'
cd spikes/cloudflare-pyodide/dev/tenant-workers
cf deploy --secrets-file "$env:TEMP\bifrost-pyodide-tenant-staging-secrets.json"

# 4. Differential replay
cd ../staging
node differential.mjs --worker https://bifrost-pyodide-tenant-ddf316c1.midtown-technology-group.workers.dev
```

## Security and cleanup

The named tunnel makes the dev API reachable from the internet at a stable
hostname. It authenticates with the seeded dev user, so **stop the connector
when testing ends**:

```sh
ssh pve-t340 'pkill -f "cloudflared tunnel run"'
```

Teardown of the tunnel resources (when no longer needed):

```sh
cf tunnels delete 06e4a44f-47ad-424e-b261-ce942730afee
cf dns records delete --zone midtowntg.com <record-id>
```

Two host-specific fixes were required and are documented in the compose overlay:
the debug compose's read-only `/app/src` mount blocks Docker's nested volume
directories, and the API port must be published for the tunnel origin.
