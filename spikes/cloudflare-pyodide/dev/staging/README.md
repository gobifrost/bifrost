# Staging harness (Stage 1 / Stage 2)

Status: **partially proven on 2026-11-13**. The seam code is in place and the
real API contract was verified; the end-to-end run was blocked by host resource
contention, not by the design.

## What was verified

- A real Bifrost API (`docker-compose.debug.yml`, project `bifrost-debug-stage`)
  came up healthy and served the module contract the gateway expects:
  `GET /api/sdk/modules/<path>` → `{path, content, hash}` and
  `GET /api/sdk/modules-resolve?name=` → `{kind, path, storage_path, content, hash}`.
- Two workspace modules written through `/api/files/write` were fetched back with
  correct sha256 pins.
- The per-tenant supervisor deployed to Cloudflare reached the remote gateway and
  issued the resolve call (`api_calls` recorded), proving the transport path.
- Two real bugs found and fixed in the gateway: Workers rejects
  `redirect: 'error'` (must be `manual`), and the origin check rejected loopback
  HTTP, which the local harness needs.

## What blocked completion

- **Cloudflare's edge returns 502 for Worker subrequests to
  `*.trycloudflare.com`**, so a quick tunnel cannot expose a local API to a
  deployed Worker. The tunnel itself returns 200 from the host. A non-Cloudflare
  tunnel or a publicly hosted staging API is required for the deployed leg.
- On this host, running a second full stack alongside the existing
  `bifrost-debug-75d35063` stack caused the API to become unresponsive and the
  Linux worker to be OOM-killed (exit 137) shortly after boot.

## Bring-up (once capacity is available)

```powershell
$env:COMPOSE_PROJECT_NAME='bifrost-debug-stage'
docker compose -f docker-compose.debug.yml -f spikes/cloudflare-pyodide/dev/staging/docker-compose.staging.yml up -d api
# API on http://localhost:18000

# local workerd, same supervisor code, real API
cd spikes/cloudflare-pyodide/dev/staging
npx wrangler dev --config wrangler.tenant-staging.jsonc --port 8844 --ip 127.0.0.1
```

An attempt needs: `entry`, `requirements.modules` (the declared closure),
`manifest` (sha256 pins from the API), and `context.engine_token` (a real login
token). `scripts` for building that body are in the session log; the shapes are:

```json
{"tenant_id":"staging-1","action":"run","entry":"workflows/probe.py",
 "requirements":{"modules":["workflows/probe.py","workflows/helper.py"]},
 "manifest":{"workflows/probe.py":"<sha256>","workflows/helper.py":"<sha256>"},
 "context":{"engine_token":"<token>"},"egress":{"hosts":[]}}
```

## Next increments

1. Retry the deployed leg on a host with capacity and a non-Cloudflare tunnel
   (or a staging API on a public origin).
2. Put the **real engine** in the per-tenant child (`runtimeCode` + the packaged
   runtime) so SDK workflows execute; the current child runs workflow source
   without the SDK.
3. Then Stage 2 differential replay against the Linux worker, and Stage 3 behind
   the production gate.
