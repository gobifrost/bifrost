# Cloudflare frontend hosting (edge static assets + API proxy)

Serve the Bifrost web client from Cloudflare Workers: static assets from the
edge, backend traffic (`/api`, `/auth`, `/ws`, `/mcp`, `/s3`, `/embed`,
`/health`, well-known routes, a small allowlist) reverse-proxied to the
canonical API origin.

The API keeps full authority — auth, scheduling, data, websocket source.
The edge worker only decides, per path, whether to serve a local asset or
forward to the configured origin. It never takes the origin from the request.

## Prerequisites

- A built client bundle in `deploy/cloudflare/frontend/assets/`
  (build from the repo `client/` workspace).
- A reachable canonical API origin over HTTPS (existing infrastructure).
- Wrangler authenticated for the target account (`wrangler login`).
- Workers Paid recommended for production (see Limits).

## Deploy

```bash
cd deploy/cloudflare/frontend
npm run build:client  # placeholder: produce ./assets from the repo client/
npx wrangler deploy --var API_ORIGIN:https://api.example.com
```

`API_ORIGIN` must be an HTTPS root with no path, query, or credentials;
anything else throws and is never proxied. Set `workers_dev` / routes / custom domain per
environment in `wrangler.toml`.

## Verify

```bash
npm test  # routing contract: node --test edge.test.mjs
```

Manual E2E checklist (run against the edge URL before cutting over DNS):

1. Login with valid credentials → token issued; invalid → 401, no leakage.
2. Static assets + SPA routes (`/`, `/workflows`) serve HTML/JS correctly.
3. `/_public_form_frame_policy` and `/_hmac_form_frame_policy` → 404.
4. Embedded form frame-policy for a real form returns the expected CSP;
   unknown IDs 404.
5. Websocket: connect, hold idle (reconnect cleanly on drop), confirm a
   notification triggered via the API still arrives after idle.
6. Upload a representative file; confirm it stores and reads back intact.

Proven during qualification (isolated test deployment, since closed):
routing tests 4/4; login, assets, health, module serving, and frame-policy
all correct through the edge; 2.5-minute and 30-minute idle websockets with
zero drops; 1 MiB and 10 MiB uploads hash-verified intact; 50 MiB correctly
refused by origin nginx (`client_max_body_size 50m`); 100 MiB correctly
refused at the edge (Workers 100 MB body limit). Edge adds ~50 ms to
websocket connect versus direct (same-tunnel A/B: 113 ms vs 165 ms median);
no per-message cost after connect.

## Limits

- **Plan:** works on Workers Free; ship production on Workers Paid. Free
  caps at 100,000 requests/day (then Error 1027) — every proxied API call
  counts. Paid has no request cap and raises CPU headroom (10 ms → 30 s
  default); the proxy itself burns low-single-digit milliseconds.
- **Uploads:** request-body ceilings follow the *domain* plan, not the
  Workers plan (100 MB Free/Pro, 200 MB Business, up to 5 GB Enterprise).
  Large-file flows should stay within the origin's own limits too.
- **Websockets:** no hard duration limit while the client stays connected,
  but runtime updates may terminate connections after a ~20-minute grace
  period — the client must reconnect (verified: 6 ms direct / ~220 ms via
  edge) and resume.
- **Static assets:** 20,000 files / 25 MiB each on Free (100,000 on Paid);
  a Vite build is nowhere near either.

## What this does not change

No API, auth, scheduling, storage, or execution semantics. The edge UI can
point at any API origin, and the API works identically with or without it.
Execution backends (Linux, containers, Cloudflare Python Workers) are
separate tracks with their own qualification.
