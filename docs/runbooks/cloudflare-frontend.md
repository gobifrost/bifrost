# Cloudflare frontend hosting (edge static assets + API proxy)

Serve the Bifrost web client from Cloudflare Workers: static assets from the
edge, backend traffic (`/api`, `/auth`, `/ws`, `/mcp`, `/s3`, `/embed`,
`/health`, well-known routes, a small allowlist) reverse-proxied to the
existing Bifrost frontend gateway, which proxies to the canonical API.

The API keeps full authority — auth, scheduling, data, websocket source.
The edge worker only decides, per path, whether to serve a local asset or
forward to the configured origin. It never takes the origin from the request.

## Prerequisites

- Docker for the production client build, and Node/npm for the recipe commands.
- A reachable existing Bifrost frontend gateway over HTTPS, running
  `client/nginx.conf`. A bare FastAPI origin is insufficient: the gateway rewrites
  `/api/auth/*` to `/auth/*` (including session refresh), and serves the final
  `/embedded/forms/public/*` and `/embedded/forms/hmac/*` HTML with the framing
  restrictions resolved by the API. The Worker forwards these paths unchanged.
- Build the edge assets from the same client revision deployed at that gateway;
  embedded-form HTML and its matching bundles still come from the gateway.
- Wrangler authenticated for the target account (`wrangler login`).
- Workers Paid recommended for production (see Limits).

## Deploy

```bash
# From the repository root:
cd deploy/cloudflare/frontend
npm run build:client
npx wrangler deploy --var API_ORIGIN:https://api.example.com
```

`build:client` uses the production `client/Dockerfile` builder stage, installs
the locked client dependencies, builds the SDK module stubs and Vite bundle,
and copies the result to `./assets`. It removes its temporary container and
image afterward. No dependency install is needed in this recipe directory.

`API_ORIGIN` must be an HTTPS root with no path, query, or credentials;
anything else throws and is never proxied. Set `workers_dev` / routes / custom domain per
environment in `wrangler.toml`. Keep the gateway hostname distinct from the edge
hostname: `API_ORIGIN` must not resolve back to this Worker, including after DNS
cutover. Retain the gateway for refresh and embedded forms.

## Verify

```bash
npm test  # routing contract: node --test edge.test.mjs
```

Manual E2E checklist (run against the edge URL before cutting over DNS):

1. Login with valid credentials → token issued; invalid → 401, no leakage.
   Refresh the session through `/api/auth/refresh` and confirm the replacement
   token works; checking initial login alone does not exercise the gateway rewrite.
2. Static assets + SPA routes (`/`, `/workflows`) serve HTML/JS correctly.
3. `/_public_form_frame_policy` and `/_hmac_form_frame_policy` → 404.
4. Embedded form frame-policy for a real form returns the expected CSP;
   unknown IDs 404. Follow the public and HMAC entry redirects to their final
   `/embedded/forms/...` documents: confirm HTML loads, its framing policy is
   preserved, and its referenced scripts load through the edge.
5. Websocket: connect, hold idle (reconnect cleanly on drop), confirm a
   notification triggered via the API still arrives after idle.
6. Upload a representative file; confirm it stores and reads back intact.

Local verification: 6/6 mocked routing tests and production Vite/assets/SDK-module build passed.
Live Cloudflare deployment and end-to-end behavior are not verified.

## Limits

- **Plan:** works on Workers Free; ship production on Workers Paid. Free
  caps at 100,000 requests/day (then Error 1027) — every proxied API call
  counts. Paid has no request cap and raises CPU headroom (10 ms → 30 s
  default).
- **Uploads:** request-body ceilings follow the *domain* plan, not the
  Workers plan (100 MB Free/Pro, 200 MB Business, up to 5 GB Enterprise).
  Large-file flows should stay within the origin's own limits too.
- **Websockets:** no hard duration limit while the client stays connected,
  but runtime updates may terminate connections after a ~20-minute grace
  period — the client must reconnect and resume.
- **Static assets:** 20,000 files / 25 MiB each on Free (100,000 on Paid);
  a Vite build is nowhere near either.

## What this does not change

No API, auth, scheduling, storage, or execution semantics. This recipe retains
the existing frontend gateway and its API; it does not replace that gateway
with a direct connection to the bare API service.
Execution backends (Linux, containers, Cloudflare Python Workers) are
separate tracks with their own qualification.
