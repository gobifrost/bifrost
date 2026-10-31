# Server-Hosted App SDK

Date: 2026-10-30
Status: Proposed design; no SDK delivery behavior has changed yet.

## Outcome

Apps load the SDK implementation from the instance they run against. An instance
upgrade supplies the new SDK on ordinary document reload without rebuilding the
App or redeploying its Solution. The App keeps its own React and application code.

Existing Apps need one migration build to replace their bundled SDK with a small
binding. After that, an SDK update that preserves the public App API needs no App
build. Changes to App code, or a new SDK API that requires App code changes, still
need a normal App build.

Today, **Update SDK** already rebuilds only the selected App, not its whole
Solution. Hosting the SDK removes that remaining App rebuild for compatible SDK
updates.

## What Changes

Today the instance packages `client/src/lib/app-sdk/`, injects its tarball into an
App build, and Vite compiles the SDK into the App's own JavaScript. The instance
selects the SDK at build time. It cannot replace those compiled bytes on reload.

Keep the existing SDK together, including its provider, hooks, components, HTTP
client, WebSocket handling, tables, files, and workflow execution. Serve that code
from the instance instead. Keep `import ... from "bifrost"` as the developer-facing
import; its installed package becomes the small binding plus type declarations.

This replaces the earlier proposal to extract a React-free protocol runtime.
There is no new table, workflow, or event translation layer. Existing SDK code
already interprets server responses; that same code simply runs from the hosted
artifact. The [preservation inventory](server-provided-app-runtime-contract-v1.md)
records existing behavior to verify, not a new intermediate API to implement.

Relevant existing paths:

- SDK exports and implementation: `client/src/lib/app-sdk/index.v2.ts` and siblings.
- SDK packaging: `api/src/services/sdk_package/`.
- App builds: `api/src/services/solutions/app_build.py`.
- Deployed mounting: `client/src/components/jsx-app/StandaloneV2App.tsx`.
- Local preview: `api/bifrost/solution_dev/proxy.py`.
- Local scaffold: `api/bifrost/commands/solution.py`.

## Supplying the App's React

The current SDK build already leaves React external so its hooks use the App's
React instance. A separately served SDK must preserve that relationship. Loading
another React copy, or using the host's React with an App's renderer, can break
hooks.

Build the existing SDK as a factory whose dependencies are supplied by the App's
binding. Conceptually:

```ts
const sdk = hostedFactory.createSdk({
  react: AppReact,
  jsxRuntime: AppJsxRuntime,
});

export const BifrostProvider = sdk.BifrostProvider;
export const useTable = sdk.useTable;
// Forward the remaining existing public exports in the same way.
```

Bundle the SDK's own icon dependencies into the hosted artifact, while keeping
React external. Apps should not need to provide whatever new icon exports the
instance's SDK happens to use. Include additional React peer modules only when
the actual dependency graph needs them.

One possible build is an esbuild CommonJS bundle inside a static factory with a
lexical dependency resolver for the supplied peers. This is generated JavaScript,
not browser `eval` or `new Function`. The feasibility probe must establish the
packaging mechanism before implementation commits to it.

Each App binding creates its own SDK instance. Module globals, React contexts,
transport defaults, and subscriptions stay inside that instance. The shared
registry holds the factory, not a mutable current App, organization, token, or
SDK instance. Preserve the existing provider and imperative-client behavior;
hosting the SDK does not justify redesigning its public API.

The binding must preserve every runtime export, including error constructors used
with `instanceof`, and self-contained type declarations. An old binding and newer
hosted SDK must continue to agree on that public surface. Future peer requirements
or public API breaks need explicit migration; hosting does not eliminate them.

## Loading and Local Development

The factory must be ready before a binding synchronously forwards its exports.
For deployed Apps, the host can load it before evaluating the App entry. For
`bifrost app start` and `bifrost solution start`, the existing development proxy can
load it before the Vite entry. Use the selected instance through the local proxy
so local Solution workflow interception continues to work.

Direct `npm run dev` is also supported today. Its provider receives the API URL
at render time, after module imports have evaluated. Factory injection alone does
not solve that ordering. Resolve and test this mode before switching the package;
do not silently require developers to edit App sources or discard direct Vite
support. An asynchronous binding or integration with the existing scaffold's
instance selection needs a separate, concrete proof of load ordering and export
identity. This is a loader concern, not a reason to split the SDK's protocol code.

Existing App source imports, provider props, and mount bootstrap should continue
to work. Do not require App authors to pass a new runtime object. Preserve
`baseUrl`, token, App/Solution identity, organization scope, theme, and logout.

The CLI proxy currently renews upstream authentication, but a browser WebSocket
closed with 4001 also needs a positive renewal result before reconnecting. Verify
that behavior in both CLI preview modes using the existing auth bridge/session
boundary. Do not assume HTTP renewal proves WebSocket recovery. This is a current
authentication integration concern; hosting must preserve server-enforced expiry,
logout, and revocation.

## Delivery and Selection

Build one hosted artifact from the instance's SDK source as part of the image.
No per-App npm install or SDK compilation happens when an App opens.

A small uncached descriptor selects the instance's current implementation for a
supported binding interface. It points to an immutable, content-addressed
JavaScript asset. The loader verifies the expected interface and artifact
integrity before execution. Static assets contain no credentials or tenant data.
Keep one delivery implementation for deployed Apps and local preview.

Publish assets into an existing shared-storage abstraction before advertising
hashes, so requests across serving replicas retrieve the same bytes. Retain
advertised artifacts across rollouts; App deployment cleanup must not delete
shared SDK assets. Verify endpoint readiness before migrating Apps during a
rolling upgrade. This delivery work is independent of SDK behavior and does not
require a new operations protocol.

The instance selects the implementation; an App declares the binding interface
it understands. The first version does not add a per-App pin to arbitrary old
SDK packages. Such a pin would require the server to keep supporting those old
packages' protocols. Do not introduce old bundled SDK fallbacks.

Load once for a document. A new document gets the current instance SDK; do not
hot-swap hooks in a running tree, replay workflows, or force reload unsaved work.
No new **Refresh App** function is required. Existing instance-version refresh
behavior can be reused, but an open document has not received new code until it
actually reloads.

## Build Provenance and Migration

Distinguish the binding compiled into the App from the hosted implementation
loaded for the current document. An SDK implementation update must not make every
migrated App appear stale or enqueue builds merely because its hash changed.
Keep the App's original build fingerprint and build timestamp truthful.

Use explicit build metadata to identify migrated bindings; legacy bundled Apps
retain their current update checks until actually rebuilt. Carry trustworthy
metadata through prebuilt archives too. Do not mark an old bundle as migrated
just because it was imported into a newer instance.

Use the existing `application.sdk_update` PlatformJob for the one-time migration.
Preserve retained source, unrelated dependency pins, target checks, atomic
activation, failure history, and resource limits. Leave Git state, manifests,
Solution metadata, and other entities untouched. Validate migration against the
SDK's public API rather than inspecting individual Apps' business logic.

The **App SDK Upgrades** setting controls automatic binding rebuilds, including
the migration. After migration, the hosted SDK follows the instance independently
of this rebuild setting. Explain that distinction in its help text.

Do not bump `sdk-contract.json` solely for packaging. It records breaking wire
changes. Define a reviewed migration rule explicitly; the current automatic rule
for wire contract 2 to 3 does not authorize arbitrary future migrations.

Inline Apps already obtain platform exports from the host. Inspect their shared
SDK consumers when changing packaging, but do not rebuild them or create a second
SDK implementation. The Python server SDK is outside this browser delivery change.

## Verification Before Shipping

1. Prove the existing SDK can run using an App React copy distinct from the host's,
   and that two factory instances have independent contexts and transport state.
2. Verify direct export identity, public types, error constructors, provider,
   header, hooks, tables, files, and workflows against existing behavior.
3. Build an App once, record its deployment and asset hashes, change the hosted
   SDK, reload, and observe the new behavior without an App build or asset change.
4. Exercise deployed mounting, both CLI preview modes, and direct `npm run dev`,
   including actual token expiry and local Solution workflow routing.
5. Rehearse mixed-replica artifact loading and one retained-source migration job.
   Verify atomic activation and unchanged unrelated source/dependencies/entities.

Update the web SDK runbook and Product Updates when this becomes implemented.
Document that App code remains immutable while the SDK implementation follows the
instance on reload.
