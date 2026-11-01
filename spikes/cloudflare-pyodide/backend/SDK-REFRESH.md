# Actual SDK refresh qualification

The unmodified upstream SDK refresh succeeds inside a fresh local Python Worker
against the real isolated API using a genuine canonical run credential. The
workflow requires a replacement access token before its table write. The run
then completes through the existing upstream consumer, survives lost terminal
acknowledgement without relaunch, and rejects both access-token use and run-refresh
redemption after terminal completion (401). Independent table readback is 70.
The returned token probe is redacted; no parent signing secret is present.

`results/canonical-refresh-routed.json` preserves the actual `/auth/refresh` 200
request, one physical launch, tenant 403, canonical completion, reconciliation,
credential revocation and fixture cleanup. Its source hashes include the route
adapter, driver, bootstrap and guard. This is local scoped execution, not deployed
acceptance or a cancellation guarantee.

## Smallest demonstrated blocker

The bridge admitted `/api/auth/refresh`, but upstream's SDK and router both use
`/auth/refresh`. The corrected capability admits only the exact upstream route
on the fixed test origin, removes the incorrect route and performs no rewrite,
redirect or alternate-origin fallback. The API still owns refresh authorization.
Three route tests cover that path and existing SDK/table paths, plus rejected
origins and unrelated auth routes. No upstream platform code or credential grant
changed, and no SDK refresh coordinator was replaced.

The initial threading hypothesis was **not established**. Current upstream code
uses `asyncio.to_thread` on this async refresh path; the actual operation now
passes. This does not qualify every threading facility, the synchronous refresh
path, concurrent refresh/adoption, or cancellation during refresh. No unsupported
native dependency or missing wheel was encountered, so no compilation attempt
or OS emulation was warranted for the observed route failure.

## Retained experiments

| Receipt | Change and observed outcome |
|---|---|
| canonical-refresh.json | Initial probe did not require a replacement before continuing, and parent assertions lost provider diagnostics. Failed observer; API logs showed a table write. No claim of zero effects or refresh success. Initial driver/observer snapshots retained. |
| canonical-refresh-final.json | Required a replacement and captured redacted provider diagnostics before assertions. SDK returned no replacement, workflow failed, document readback was 404, and no API refresh request reached the server. No thread incompatibility was inferred. Driver snapshot retained. |
| canonical-refresh-routed.json | Corrected exact refresh route. Real replacement, table write, canonical reconciliation and terminal revocation all passed. No automatic retry of an uncertain business attempt; each experiment had a separate fixture run. |

Commands from the spike directory, with the isolated API healthy and the existing
authorized-local Wrangler supervisor connected to that test network:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-routed
node backend/verify-canonical-refresh.mjs
node --test backend/test-api-route.test.mjs
```

The run command refuses existing receipts before effects. The verifier is offline
and checks actual success, one refresh, terminal access/refresh revocation and
exact source. Historical reports intentionally do not assert current source;
the earlier guarded tenant pair is verified against its immutable commit:

```sh
node backend/canonical-isolation.mjs --verify canonical-isolation-guarded-final --commit d09c4e113efea3ba9e3c8c94b42a6dfc6b5474fa
```

The own supervisor/network attachment are removed after qualification, and the
test API is restored to its prior stopped state. No cloud call, public endpoint,
new spending, persistent credential, security setting or production change.

## Shared placement coordination

Existing passive Eddie exchange `82cd3e90-3ddf-4da6-a534-c9e1635c870c` was read
and acknowledged in native run22. Its reply reported no confirmed shared
runtime-eligibility agreement and could not certify the other owner's unpublished
files conflict-free. This is coordination evidence, not new authority. No
Containers steering or shared claim/model/controller/recovery edits were made.
The proposed placement boundary remains a proposal for coordinated review.
