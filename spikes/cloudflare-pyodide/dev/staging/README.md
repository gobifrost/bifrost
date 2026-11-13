# Staging harness (Stage 1 / Stage 2)

## Status: Stage 1 integration proven on pve-t340 (2026-11-13)

First end-to-end attempt with the **real API**, **real module pins**, **real
token**, and the per-tenant supervisor in the same `workerd` runtime:

```
POST /  {"tenant_id":"staging-pve","action":"run","entry":"workflows/probe.py", ...}
→ status 200
  result:    {"ok": true, "doubled": 42}
  recorded:  workflows/helper.py, workflows/probe.py
  api_calls: 3   (modules-resolve + 2 module fetches, all 200)
```

That is the transport seam working: the Worker fetched module source from a real
Bifrost API through the gateway, verified both sha256 pins, executed the
workflow, and reconciled the import audit to real storage paths.

### Where it runs

| Component | Location |
|---|---|
| Bifrost API + Postgres + Redis + SeaweedFS | pve-t340 container `128` (`bifrost-pyodide-test-01`), `172.16.15.181:18000` |
| Worker (workerd) | `wrangler dev` on the workstation, `API_ORIGIN=http://127.0.0.1:18000` |
| Transport | `ssh -N -L 18000:172.16.15.181:18000 pve-t340` |

The API port is firewalled from the LAN, so the SSH forward is how the harness
reaches it — which conveniently keeps the gateway's loopback-HTTP allowance.

### Capacity note

Container 128: 2 cores, 8 GB, 47 GB disk (13 GB free). Host pve-t340: 16 cores,
62 GB with ~13 GB available and swap fully used. Building the API image there
works but is tight; run `docker image prune -f` first and avoid parallel builds.

Two host-specific fixes were needed for the debug compose on 128:

```sh
# 1. read-only source mount blocks Docker creating nested volume dirs
sed -i 's#- ./api/src:/app/src:ro#- ./api/src:/app/src#g' docker-compose.debug.yml
# 2. publish the API for the harness
#    (docker-compose.staging.yml: api.ports: ["18000:8000"])
```

## What is still blocked

- **Deployed-Cloudflare leg:** the edge returns 502 for Worker subrequests to
  `*.trycloudflare.com`, so a quick tunnel cannot expose the API to a deployed
  Worker. A non-Cloudflare public origin is required.
- **Stage 2 differential replay:** the Linux worker now runs on 128; the harness
  that submits the same attempt to both backends and compares results is the
  next increment.

## Bring-up (reproduce)

```powershell
$env:COMPOSE_PROJECT_NAME='stage128'
docker compose -f docker-compose.debug.yml -f docker-compose.staging.yml up -d api worker
ssh -N -L 18000:172.16.15.181:18000 pve-t340
cd spikes/cloudflare-pyodide/dev/staging
npx wrangler dev --config wrangler.tenant-staging.jsonc --port 8844 --ip 127.0.0.1
```

Attempt body: `entry`, `requirements.modules` (declared closure), `manifest`
(sha256 pins from the API), `context.engine_token` (real login token).
