# Server-Hosted App SDK

Date: 2026-10-30
Status: Implemented and locally verified; PR candidate, not released or deployed.

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

The packaging probe used an esbuild CommonJS bundle inside a static factory with
a lexical dependency resolver for the supplied peers. This is generated
JavaScript, not browser `eval` or `new Function`. The unchanged `index.v2.ts`
bundled with only React and its JSX runtime external, including the SDK's icons.
Server rendering passed with a copied App React distinct from the host's React;
two factory instances retained separate context and transport headers. This
proves packaging feasibility, not browser loading or local-development readiness.

Each App binding creates its own SDK instance. Module globals, React contexts,
transport defaults, and subscriptions stay inside that instance. The shared
registry holds the factory, not a mutable current App, organization, token, or
SDK instance. Preserve the existing provider and imperative-client behavior;
hosting the SDK does not justify redesigning its public API.

The binding preserves the existing public exports and ships self-contained type
declarations. Five small public error constructors stay in the binding and are
supplied as factory peers, so SDK-thrown errors and App imports agree on
`instanceof`. Hook and component exports are stable forwarding functions; their
identity need not equal the private hosted implementation's functions. Their
signatures and observable behavior remain the App-facing contract. Future React
peer requirements or public API breaks need explicit migration.

## Loading and Local Development

The binding's `BifrostProvider` receives the existing `baseUrl`, loads the factory,
and renders children inside the hosted provider only when ready. Child hooks
remain synchronous. Loading and failure states are visible; failed loads do not
silently render an empty App or choose an old bundled SDK. The host preloads the
same factory before evaluating a deployed App entry, including cached entries.

This readiness provider also preserves direct `npm run dev` with a runtime-only
API URL. A Vite 5.4.21 probe confirmed that top-level await fails both production
build and dependency optimization with the unchanged scaffold's ES2020 targets.
The readiness provider avoids raising those targets or changing App source/config.
Production serves prebuilt JavaScript; it never runs a development server when an
App opens.

Imperative methods use the provider's selected SDK after configuration. Before a
provider exists, preserve the existing same-origin default by loading lazily when
called; do not guess a remote URL. A pending subscription returns cancellation
immediately and must never open a socket after cancellation. A late same-origin
load cannot overwrite the provider's later explicit selection. As before, a
top-level call made before a remote provider exists cannot infer that provider's
future API URL. This is an explicit boundary, not a new remote-origin fallback.

The document shares factory selection by origin and public major across the
host's and App's separately bundled loader copies. It never shares an instantiated
SDK or App credentials. Both CLI preview commands continue through the selected
instance's local proxy so local Solution workflow interception is preserved.

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

Publish assets into the platform-owned `_platform/app-sdk/` shared-storage prefix
before advertising hashes, so supporting replicas retrieve the same bytes. Retain
advertised artifacts across rollouts; App deployment cleanup must not delete
shared SDK assets. The descriptor is `/api/app-sdk/v1/manifest`; immutable scripts
are `/api/app-sdk/assets/<sha256>.js` and register a content-derived factory ID.
The descriptor separately hashes the final script, avoiding a self-referential
registration key. Production images prebuild the artifact; a missing production
artifact fails startup instead of compiling a replacement on a request.

There is no universal serving-replica membership source that proves an initial
rolling upgrade has removed every API lacking these routes. During that first
route introduction, a newly hosted App can show a load error until the ordinary
instance upgrade finishes and its document reloads. Do not claim a nonexistent
readiness gate, require draining, or hide this with an old SDK fallback. Shipping
the routes in advance reduces this window but does not prove readiness when
self-hosted operators skip intermediate releases. Later supported replicas share
the retained artifact delivery contract.

An App binding declares a public SDK API major; the instance selects the latest
maintained implementation for that supported major. The first implementation
serves major 1. A future genuinely breaking App-facing change introduces another
major while existing Apps keep requesting their supported major. An ordinary
instance upgrade must never silently advance that choice. Moving major requires
an explicit binding/source migration and an App build.

Supported majors use the same backend and receive current authentication and
security behavior. This does not eliminate compatibility work: each major's
promised semantics must remain implementable against the current backend. Keep
an explicit supported-major matrix and contract tests. Removing an endpoint or
capability that an older major needs requires preserving that behavior or an
announced end-of-support migration. There is no arbitrary pin to frozen historical
SDK releases, automatic major fallback, or separate backend API version required
merely to host the SDK. The factory peer ABI has its own version, distinct from
the public SDK major and the server wire contract.

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
retain their current update checks until actually rebuilt. Prebuilt archives
carry CLI-produced metadata as the privileged deploy author's attestation,
without independent JavaScript inspection. Do not mark an old bundle as migrated
just because it was imported into a newer instance.

Use the existing `application.sdk_update` PlatformJob for the one-time migration.
Preserve retained source, unrelated dependency pins, target checks, atomic
activation, failure history, and resource limits. Leave Git state, manifests,
Solution metadata, and other entities untouched. Validate migration against the
SDK's public API rather than inspecting individual Apps' business logic.

The **App SDK Upgrades** setting controls automatic binding rebuilds, including
the migration. After migration, the hosted SDK follows the instance independently
of this rebuild setting. Explain that distinction in its help text.

The initial hosted-delivery change declares no automatic hosted migration. Manual
SDK updates and new builds can adopt it after the normal instance upgrade; a
later reviewed policy can permit automatic migration after dogfooding. Existing
wire-contract automatic rules do not implicitly authorize this packaging change.

Do not bump `sdk-contract.json` solely for packaging. It records breaking wire
changes. Define a reviewed migration rule explicitly; the current automatic rule
for wire contract 2 to 3 does not authorize arbitrary future migrations.

Active deployments carry `sdk_binding_api_major`; null identifies a bundled SDK.
`sdk_fingerprint` identifies the compiled binding after migration and must stay
stable when only the hosted implementation changes. Status and automatic jobs
ignore wire-contract changes for supported hosted majors. SDK-update job payload
version 3 fences the expected and target binding major so older workers cannot
claim this migration. Already queued manual jobs retain their original target
semantics through exact historical payload schemas.

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
