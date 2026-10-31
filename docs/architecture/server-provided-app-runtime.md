# Server-Provided App Runtime

Date: 2026-10-30
Status: Proposed design; no runtime delivery behavior has changed yet.

## Outcome

An App uses the protocol implementation supplied by the Bifrost instance it is
running against. Updating authentication, WebSocket frames, table invalidation,
or execution transport must not require recompiling that App.

There is one migration build for an existing standalone App to adopt the new
binding. Afterward, routine protocol changes arrive with an instance upgrade
and take effect when its document reloads. App assets stay immutable. An SDK
interface change that requires new App code still needs an App build.

Even today, **Update SDK** rebuilds one App rather than redeploying its entire
Solution. This design removes that rebuild for changes behind the stable
runtime interface.

## What Happens Today

The instance serves its SDK package at `/api/sdk/download`. Server builds
generate the same package from their image, inject `bifrost-sdk.tgz`, and run
Vite. The SDK's transport implementation becomes part of each App's deployed
JavaScript. An instance upgrade cannot replace those compiled bytes.

The relevant paths are:

- SDK package and fingerprint: `api/src/services/sdk_package/__init__.py`.
- App compilation: `api/src/services/solutions/app_build.py`.
- App mounting and bootstrap: `client/src/components/jsx-app/StandaloneV2App.tsx`.
- SDK source: `client/src/lib/app-sdk/`.
- Local preview: `api/bifrost/solution_dev/proxy.py`.

The existing host authentication bridge shares live credentials and renewal
with deployed SDK copies. It does not replace their WebSocket state machine,
table event handling, or workflow response interpretation.

## Package Boundary

Keep the installable `bifrost` package as the developer-facing import. Split
its implementation into an App binding and a server-provided runtime.

| App binding, compiled with the App | Runtime, supplied by the instance |
| --- | --- |
| React context, hooks, and rendering state | Authenticated requests and bounded auth recovery |
| Header, theme controls, and presentation | WebSocket authentication, reconnect, and resubscribe |
| Stable public SDK types and error classes | Table/file operations and response normalization |
| Pagination, snapshot refresh coalescing, and rendering | Table operations and stable SDK event normalization |
| Runtime interface requirement | Workflow execution, streaming, terminal result handling |
| Mapping runtime results to existing hook return values | User, organization, and branding resource normalization |

The runtime contains no React, React DOM, JSX, lucide, or host UI imports. An
App continues to use its own React peer dependency and its own React root.
Ship self-contained declarations for the stable public interface with the
binding package. They must not refer to host-only `@/` aliases or track changing
generated server DTOs. The runtime adapter alone consumes generated wire types.

Moving only HTTP and WebSocket primitives is insufficient. `useTable` and
`useInfiniteTable` currently recognize `table_invalidated`; make those events
an explicit stable SDK contract that the runtime produces, independent of future
server frame names. Their existing refresh coalescing and pagination can stay
in the binding. `useWorkflow` interprets execute responses and terminal
execution states; move its request/stream/completion state machine into a plain
runtime operation. Hooks retain loading, error, latest-run guards, and rendering
behavior while observing stable runtime results.

Keep current filter validation and flattened row shape observable to Apps.
The binding may retain its pure filter/flattening helpers against the frozen SDK
query and document shapes. The runtime translates those stable shapes to and
from the current server expression/record formats. Cover this with existing
filter and table-hook regression cases. Do not expose generated server DTO
aliases as a supposedly stable interface that changes with each API build.

Existing public calls such as `useTable`, `useWorkflowMutation`, `tables`, and
`files` retain their documented signatures for the migration. Preserve public
error-class identity in the binding: normalize runtime error codes there rather
than exposing a second copy of a constructor used by `instanceof`.

`useBifrostContext().authedFetch` remains an authenticated raw HTTP escape hatch.
It benefits from centralized authentication but cannot insulate App-authored
endpoint paths or response parsing from REST changes. The rebuild-free promise
covers the typed SDK operations handled by the runtime.
Delegate it to `request()` without consuming or reshaping its `Response`.
Preserve the current Authorization ownership, App/org headers, live token
access, and exact refresh-required replay rule in both typed and raw calls.

## Runtime Interface and Selection

Use a versioned, React-free factory with per-mount clients. Its first interface
is `v1`; the implementation fingerprint changes whenever its bytes change.
The conceptual shape is:

```ts
interface AppRuntimeV1 {
  readonly interfaceVersion: 1;
  readonly fingerprint: string;
  createClient(context: AppRuntimeContext): AppRuntimeClientV1;
}

interface AppRuntimeClientV1 {
  readonly tables: TableClientV1;
  readonly files: FileClientV1;
  readonly workflows: WorkflowClientV1;
  readonly resources: ResourceClientV1;
  request(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
  dispose(): void;
}
```

This is an architectural sketch, not the final exhaustive TypeScript contract.
The [public operation inventory](server-provided-app-runtime-contract-v1.md)
records the current `index.v2.ts` surface. Match that inventory when freezing the
first TypeScript interface. Public query, result, subscription, cancellation,
and error types belong to that interface; server wire DTOs remain internal to
the runtime.

An App pins the runtime **interface**, while the instance selects the current
implementation of that interface. There is no per-App database pointer to an
arbitrary historical protocol package in this first design. Such a pointer
would require continued server support for that package's wire behavior and
could retain obsolete authentication behavior.

If a future public interface genuinely breaks, introduce a new explicit
interface version and migration. Do not silently claim compatibility, choose a
different version after a failed load, or bundle an old protocol implementation
as an offline fallback. Supporting two interface versions would be a separate,
reviewed decision; it is not an automatic compatibility policy.

## Delivery and Caching

Build the runtime artifact as part of the API image. It is static JavaScript,
contains no credentials or tenant data, and requires no npm install per App.
Retain one source of truth for the code served to local and deployed Apps.

Proposed routes, separate from existing Python `/api/sdk/runtime/*` operations:

| Route | Behavior |
| --- | --- |
| `/api/app-runtime/v1/manifest` | Current implementation descriptor; `Cache-Control: no-store` |
| `/api/app-runtime/assets/<sha256>.mjs` | Exact immutable JavaScript; long-lived immutable caching |

The descriptor identifies interface version, content hash, artifact URL, and
instance build. URLs remain relative to the selected instance/proxy origin.
The host and binding use the same loader, keyed by origin and interface. The
loader obtains one implementation for a document and does not replace live
objects beneath running hooks. A new document obtains the current descriptor.

Manifest and module are public static artifacts, like today's SDK download;
neither encodes authorization or contains credentials. Validate descriptor
interface, digest, and same-origin asset URL before loading. Use an
integrity-checked module script at the canonical hashed URL, followed by
versioned factory registration, so bytes are checked before execution without
blob URLs or weakening CSP. The loader records implementation hash metadata;
the JavaScript bundle need not embed its own content hash. Cache successful
loads by origin, interface, and digest; a failed load must not poison the cache.

The host preloads the factory before mounting a deployed App. A small versioned
registry can expose the factory across the host/App module boundary, following
the existing authentication-bridge pattern. This registry holds factories,
never a mutable "current App" or current organization. Existing App sources need
not start forwarding a new bootstrap field merely to adopt the updated package.
Runtime validation and registration finish before evaluating the App entry,
including when its module factory is already cached from an earlier visit.

The binding's provider owns readiness when running outside the host: it loads
the same factory before rendering children that use its client. A missing or
unsupported runtime produces an explained load error; it does not select a
bundled transport. This keeps one protocol implementation in every mode.

Content-addressed URLs must never serve another build's bytes under the same
hash. Publish artifacts into a dedicated platform-owned prefix in the existing
shared object store before advertising their descriptors. The asset route uses
the same storage abstraction as other immutable platform artifacts, with a fixed
prefix and validated digest. Retain published hashes across instance rollouts;
App deployment cleanup must not delete shared runtime assets.

During a rolling instance upgrade, every API that implements this delivery
contract can serve an advertised hash from shared storage. Establish endpoint
availability before switching builds to the thin package; a server predating
the delivery endpoint cannot serve it. Do not rely on sticky routing or ignore
an asset hash mismatch. A preexisting document continues to use the runtime it
already loaded until ordinary reload. Runtime/server rollout compatibility is
still required across the supported rolling window. The initial migration must
be gated on delivery readiness rather than started by the first new worker while
older serving APIs still lack the endpoint.

No "Refresh App" control or forced mid-action reload is introduced. A runtime
update must not replay workflow starts or discard unsaved App state. A truly
incompatible server cutover can still require a normal document refresh; that
requirement must be explicit rather than described as a live hot swap.
The current `useVersionCheck`/`VersionUpdateBanner` detects an instance upgrade
and offers refresh; automatic reload currently occurs for stale Vite imports,
not every instance upgrade. Reuse those existing behaviors without claiming an
already-open App has received new runtime code before its document reloads.

## Scope and Lifetime

Each mounted provider receives an instance-bound client capturing App identity,
Solution identity, and organization scope, plus a live credential accessor.
Scope changes dispose the old client and invalidate pending observations before
rendering results for the new scope. Token renewal does not remount the App.
Disposal closes subscriptions, cancels timers, and suppresses late callbacks.

Do not move today's module-global `transport` and table/file default scopes into
a shared host singleton. Hooks use the client from their own React context.
Different Apps must never share mutable default scope, transport headers,
subscription ownership, or resource caches.

Existing unbound `tables`/`files` exports need an App-package-local default client
to preserve their public signatures. They must not resolve through a host-global
active client. Multiple providers using that same package module with different
scopes cannot safely share an implicit default; the implementation must offer
explicit bound clients for that case and reject ambiguous unbound calls before
sending a request. This limitation must be documented and tested, not hidden by
last-writer-wins behavior.

Authentication remains server-enforced. The runtime uses the host's existing
coordinated refresh where available; local preview uses CLI proxy renewal.
Refresh failure, logout, authority changes, and revoked subscriptions retain
their current security outcomes. Generic 401 responses do not authorize replay
of requests that may already have had effects.

## Local Development and Other Consumers

Both `bifrost app start` and `bifrost solution start` already proxy `/api/*` and
`/ws/*` to the selected instance. Load the runtime through that origin too.
Keep the current local workflow interception and bound Solution context; a
runtime that targets the upstream origin directly would bypass local execution.
Verify JavaScript MIME types, cache headers, and query forwarding through the
proxy without exposing CLI credentials in runtime assets.

The proxy currently appends bound Solution/scope query parameters to API
requests. Static runtime routes must strip or tolerate them without changing
asset identity or caching scoped responses. Preserve JavaScript MIME and
immutable caching for hash assets, and `no-store` for the manifest. Do not add
tokens to module URLs or forward an upstream Authorization header on an asset
redirect. Direct Vite use with an explicit API origin continues to require the
instance's configured CORS permission; the CLI proxy remains the normal
single-origin path.

Local Solution workflow execution can return an already-terminal
`is_transient: true` result with no durable execution record. The runtime settles
that response immediately, preserving errors and results, without opening an
execution stream or polling a nonexistent execution. This behavior belongs in
the workflow client contract alongside ordinary server execution.

The CLI proxy also needs an explicit host-auth adapter for the runtime. Replacing
the token on an upstream handshake is insufficient when a socket closes with
4001 and the browser has no platform refresh bridge. Extend the existing local
dev-session boundary to confirm/renew CLI authentication and report success or
expiry without exposing credentials. The proxy can supply the same stable auth
callbacks as the host. Reconnect after a positive renewal result; failed CLI
renewal keeps the existing explained login-required outcome. Test actual expiry
through both preview modes rather than assuming HTTP renewal proves socket
renewal.

Disconnected prebuilt Solution archives can carry the thin binding but still
need a compatible runtime on the destination instance. Their metadata must
declare the runtime interface; do not falsely stamp an old monolithic prebuilt
bundle as migrated merely because it was imported on a new instance.

Inline Apps already receive their platform exports from the host and do not
need a source rebuild for this change. Update their host adapters to use the
same protocol implementation. Inspect `bifrost-runtime.ts`, `BundledAppShell`,
and `app-code-platform/useWorkflowMutation.ts` when moving shared SDK modules;
do not leave a second protocol implementation for those consumers.

## Provenance and Migration

Keep these identities distinct:

- Binding package version/fingerprint: the code actually compiled into the App.
- Runtime interface: the public contract that binding requires.
- Runtime implementation fingerprint: the implementation loaded for this
  document from the instance.
- Existing wire contract: protocol provenance for older monolithic bundles.

An instance runtime change must not make every compatible binding appear to
require a rebuild just because its fingerprint differs. Do not rewrite an App's
stored build fingerprint or build timestamp when it merely loads a new runtime.
Legacy bundled Apps retain their existing update status until actually rebuilt.

Add a nullable server-owned runtime-interface marker to active build provenance:
null identifies a monolithic bundle; `1` identifies a runtime-v1 binding. Stamp
it atomically with deployment activation and carry it in trustworthy prebuilt
SDK transport metadata. Existing `sdk_fingerprint` continues to identify the
compiled package, which is the thin binding after migration. This is build
metadata, not a new field in portable App source manifests. Add it to the
generated API status contracts rather than manually writing endpoint types.

`application_sdk_status`, batching, enqueue eligibility, and worker target fences
must branch explicitly on this marker. Legacy Apps keep wire/fingerprint
comparison; migrated Apps check required runtime-interface support and binding
compatibility. A server-only runtime change cannot enqueue a binding rebuild.

Use `application.sdk_update` for the one-time migration build, retaining its
source isolation, dependency pins, target checks, atomic activation, failure
history, and admission controls. No full Solution reconciliation is needed.
Approve the migration globally only after verifying the unchanged public SDK
surface; do not inspect individual App logic to infer compatibility.
The existing **App SDK Upgrades** setting controls automatic binding rebuilds,
including this migration. Once an App adopts the runtime, that runtime follows
the instance independently of the rebuild toggle. Turning the setting off does
not pin old authentication/protocol code. Explain this distinction in the
setting's help text and upgrade documentation.
The migration must preserve source files and the current bootstrap fields
(`baseUrl`, `token`, `orgScope`, `appId`, `solutionId`, theme, and logout). It may
replace the injected SDK and generated build output; it must not edit an App's
`main.tsx`, require new provider arguments, or update unrelated dependencies.

Do not bump `sdk-contract.json` solely to identify this packaging migration:
that number describes breaking server wire changes. Add explicit runtime/binding
metadata and a reviewed migration rule instead. The current automatic policy
only approves wire contract 2 to 3; it cannot silently be interpreted as approval
for a contract-3 App to adopt any future binding.

## Implementation and Acceptance

Deliver one complete vertical path before expanding the remaining operation
families: immutable runtime delivery, scoped client, binding readiness, table
snapshots/subscriptions, deployed mounting, and CLI preview. The final switch of
`/api/sdk/download` to the thin package waits until every existing SDK operation
has moved; do not ship a partially extracted package with hidden bundled paths.

Required acceptance evidence:

1. Build an App once, record deployment ID and asset hashes, then change the
   server's table/auth protocol handling behind runtime interface v1. Reload
   the unchanged App and verify it stays live without a build job or artifact
   change. This is the central cross-process regression test.
2. Verify expiry renewal/reconnect, logout, failed refresh, revoked policy,
   subscribe acknowledgement, snapshot reconciliation, and workflow completion
   through deterministic runtime tests. Keep React tests focused on the binding.
3. Mount clients with different App/Solution/org contexts concurrently, change
   one scope, and dispose one client. No data, headers, callbacks, or socket
   cleanup may cross into the other client.
4. Verify both CLI preview modes use the selected instance's artifact and local
   workflow routing, with no embedded protocol copy or credential-bearing asset.
5. Inspect build metadata to reject React/UI imports in the runtime and protocol
   imports in the binding; test public SDK exports and error-class behavior.
6. Rehearse descriptor/asset routing during mixed instance versions and after a
   normal reload. A mismatch is explained and never served under a false hash.
7. Run one retained-source migration job and verify atomic activation and
   unchanged unrelated dependencies, Solution entities, manifests, and Git state.

Product documentation must distinguish immutable App code from instance-managed
protocol behavior. Product Updates should explain the one-time migration and
subsequent rebuild-free runtime updates in those terms.
