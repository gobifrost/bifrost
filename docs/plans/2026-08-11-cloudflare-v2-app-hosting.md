# Optional Cloudflare hosting for v2 apps

**Date:** 2026-08-11
**Status:** proposed; separate from Builder runner completion.

## Decision

Do not start a new Cloudflare Pages integration. Use **Workers Static Assets**
and, for the MSP-scale target, **Workers for Platforms**.

Cloudflare now documents migration from Pages to Workers, and Workers provides
the same static-asset behavior with a broader platform. Ordinary paid Workers
are limited to 500 Workers per account; Pages projects are also capped at 500
on paid plans. Workers for Platforms is designed for customer-deployed code,
supports dispatch namespaces and dynamic path/hostname routing, and has no
fixed script-count ceiling.

This is optional production hosting for published `standalone_v2` apps. V1,
preview, Builder sandbox execution, and existing v2 origin hosting remain
unchanged by default.

## Why it fits

- The Builder already produces bounded, reviewed v2 `dist/` artifacts.
- Uploading artifacts is a durable publish operation and belongs in the
  existing `application.publish` PlatformJob path, not a new service.
- A dynamic dispatch Worker can retain `/apps/{slug}` and route to an isolated
  user Worker with static assets.
- `/api/*` stays on Bifrost, so the existing SDK and application authorization
  remain authoritative.
- No additional Docker image or permanent Bifrost container is required.

## Current runtime constraint

Published v2 apps currently use the Bifrost client shell at `/apps/{slug}`.
That shell authenticates the viewer, fetches the bundle manifest, and invokes
the app's `mount()` export with the SDK bootstrap. Uploading `dist/` to Pages
would not reproduce this contract by itself.

Cloudflare hosting therefore needs a Bifrost-owned edge bootstrap, not merely a
bucket upload. The bootstrap must preserve deep links, theme, organization
scope, logout, embed behavior, SDK token handling, and access invisibility.

## Proposed architecture

1. Provision one untrusted Workers for Platforms dispatch namespace and one
   Bifrost-owned dispatch Worker.
2. Attach a route for the existing `/apps/*` origin. Requests for preview,
   editing, V1, or non-edge-hosted apps pass through to the existing origin.
3. Give each edge-hosted v2 app a generated external script identity derived
   from its immutable application UUID, never an administrator-entered name.
4. On navigation, the dispatch Worker presents the browser's existing Bifrost
   cookie to a narrow `/api/app-edge/session` origin endpoint. Bifrost performs
   normal app authorization and returns a short-lived, app-bound edge grant
   plus bootstrap metadata.
5. Sign that grant with a dedicated asymmetric edge-session key. Cloudflare
   receives only the public verification key, so it cannot mint Bifrost user or
   app tokens.
6. The dispatch Worker invokes the app's user Worker and serves its static
   assets plus the fixed Bifrost bootstrap document. SDK traffic continues to
   same-origin `/api/*`.
7. Publishing uploads the new asset version, runs an authenticated probe, then
   atomically moves Bifrost's active edge-deployment pointer. The prior
   deployment ID remains available for rollback.

The implementation spike must prove that route pass-through cannot recurse and
that current normal, embed, and impersonated cookies arrive at the dispatch
Worker exactly as expected. Embed launch may require a one-time edge launch
code because URL fragments are not visible to the Worker.

## Hoster setup

Keep this under **Settings > Builder** as an optional **App hosting** section:

1. Reuse the saved Cloudflare Account ID and token where permissions allow.
2. Detect the Bifrost zone from its configured public URL.
3. Verify Workers for Platforms billing/entitlement and required permissions.
4. Provision the namespace, dispatch Worker, route, verification key, and live
   probe automatically.
5. Show live checks and do not expose namespace, script, or route names as
   normal form fields.

The hosting capability adds at least `Workers Routes Write` to the current
`Workers Scripts Write` token requirements. Bifrost should report missing
permissions precisely rather than asking for a global Cloudflare key.

## Product controls

- Global setting: disabled, available, or default for new published v2 apps.
- Per-app setting: **Bifrost origin** or **Cloudflare edge**.
- Release view: provider, deployment digest/ID, deployed time, health, traffic,
  last error, and rollback.
- Promotion review: changing hosting provider is explicit and audited.
- Deletion/unpublish: remove the user Worker only after Bifrost marks the app
  unavailable; retain a bounded rollback window.

## Cost and limits

Cloudflare currently documents Workers for Platforms at $25/month, including
20 million requests, 60 million CPU milliseconds, and 1,000 scripts; additional
scripts are $0.02 each. Static-asset requests and storage have favorable
Workers pricing, but Bifrost should ingest measured request/CPU/script usage
before presenting authoritative per-customer dollars.

Relevant limits include 100,000 static files per paid Worker version and 25 MiB
per asset. These fit the current bounded app build but must be enforced before
upload.

## Delivery phases

1. **Spike:** one private v2 app, same `/apps/{slug}` URL, auth/deep-link/embed
   matrix, upload/rollback, and origin pass-through.
2. **Control plane:** provider config, provisioning PlatformJob, deployment
   records, publish integration, cleanup, metrics, and audit.
3. **UX:** readiness wizard, per-app host selection, release/health/rollback,
   and actionable failures.
4. **Scale:** request/CPU/script metering, customer attribution and quotas,
   saved support filters, and rate-limit backpressure.

## Official references

- [Migrate from Pages to Workers](https://developers.cloudflare.com/workers/static-assets/migration-guides/migrate-from-pages/)
- [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Workers for Platforms](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/)
- [Dynamic dispatch Worker](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/configuration/dynamic-dispatch/)
- [Workers for Platforms static assets](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/configuration/static-assets/)
- [Workers for Platforms pricing](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/pricing/)
- [Workers route permission](https://developers.cloudflare.com/api/resources/workers/subresources/routes/methods/update/)
