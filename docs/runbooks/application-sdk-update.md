# Application SDK updates

Bifrost exposes SDK provenance on deployed Apps and lets an administrator
rebuild selected Apps against the SDK shipped by the current Bifrost instance.
Manual updates keep their current behavior. Automatic updates are an opt-in,
reviewed source-compatible transition; they do not apply developer code
changes.

## Provenance and status

Every successful V2 build records SDK provenance on the App:

- `sdk_package_version`: npm-safe Bifrost release version.
- `sdk_fingerprint`: the first 16 hex characters of SHA-256 over the code
  compiled into the App: the full SDK for legacy builds, the small binding after
  hosted migration. Hosted implementation changes do not change this hash.
- `sdk_contract_version`: the integer in
  `client/src/lib/app-sdk/sdk-contract.json`. Bump it only for a deliberately
  breaking SDK↔server wire-contract change.
- `sdk_built_at`: when that SDK build was activated for the App.
- `sdk_binding_api_major`: the hosted public SDK major selected by the binding;
  null identifies a legacy bundle. Supported majors keep working across server
  wire-contract changes without an App rebuild.

For prebuilt Solution archives, these fields are the privileged deploy author's
attestation, normally emitted by the CLI from the installed SDK package. The
server does not inspect prebuilt JavaScript to verify that stamp.

The status contract is `current`, `update_available`, `update_required`,
`unknown`, or `not_applicable`. Missing provenance is `unknown`; App list
responses include whether rebuild source is available. Solution list/get
responses provide a server-computed aggregate and actionable App count, using
one bounded App query rather than fetching every App in the browser.

## Rebuild behavior

`application.sdk_update` is a shared PlatformJob. It resolves the active
deployment's retained source, injects the current SDK tarball, runs the normal
compiler, and atomically swaps the active deployment only after a successful
build. Failure leaves the previous deployment active. The job reports truthful
phases—loading, resolving source, and rebuilding—without synthetic percentages;
completion is 100%.

An SDK-only rebuild of a Solution App extracts only that App from the retained
Solution archive. It must not reconcile the Solution, touch Git or manifests,
or mutate sibling entities. It validates the App manifest and archive safety
without parsing unrelated entity manifests; full Solution preview and install
still validate the complete Solution. Batch actions enqueue one independently
observable job per actionable App so the scheduler retains its normal admission
control, resource protection, cancellation, and failure reporting.

## Automatic App SDK Upgrades

In **Settings → Maintenance**, **App SDK Upgrades** has an **Enable Automatic
App SDK Upgrades** toggle. It defaults to off while this capability is being
dogfooded; the intended future default is on after that proof is complete.

When enabled, the elected scheduler reconciles at startup and once per minute,
considering at most 20 candidates per pass. It queues ordinary
`application.sdk_update` PlatformJobs, whose progress appears in
Notifications. An automatic attempt is eligible only for an active,
source-built App with retained source that needs a transition explicitly
approved by SDK metadata. Legacy wire-transition rules do not authorize a
hosted-binding migration. The initial hosted delivery release leaves
`automatic_hosted_migration_from` empty while adoption is dogfooded through the
existing manual **Update SDK** action after a normal instance upgrade. Migrated
Apps are never automatically advanced to another public major.

The scheduler attempts an App/target pair once, including failures. Use the
existing manual **Update SDK** action to retry a failure. Automatic updates do
not change authorization or add an authentication bypass.

Automatic rebuilds require a valid retained `package-lock.json`. The compiler
uses npm to prepare the vendored SDK entry, then copies only that entry and its
root declaration into the retained lockfile. Other lockfile values stay pinned,
including optional-package metadata that npm may normalize in its candidate.
The retained non-SDK root declarations must match `package.json`, and `npm ci`
must install the resulting lockfile successfully. The developer's retained
lockfile need not already contain the platform-injected SDK. SDKs with their own
normal, optional, or bundled dependency graph require a manual update. An App
without a valid reproducible lockfile is left for a manual update or source
redeploy.

Automatic jobs pin the SDK fingerprint and contract before compilation, then
verify that same target before activation. They retain the prior deployment
artifacts for recovery; failures leave the active deployment unchanged, and
there is no automatic rollback.

`application.sdk_update` uses payload version 3 for newly queued work, fencing
the expected and target binding major as well as the fingerprint and wire
contract. New workers retain the exact version 1 and 2 schemas for already queued
manual jobs, with their original target semantics. Older workers cannot claim
version 3 rather than silently ignoring its binding-major target.
Upgrade workers before enabling automatic updates. A worker whose installed SDK
does not match a job's pinned target also refuses it; enqueue the job again
after workers are aligned.

## Retained source and privacy

Independent deploys retain a sanitized zip beside each immutable deployment.
Solution deploys retain the validated deployed Solution archive. SDK rebuilds
never rely on `node_modules` or an already-built `dist` directory.

Older Solution exports may contain the same logo path twice. The shared archive
reader accepts repeated paths only when their contents are byte-identical and
extracts one logical file. It compares duplicates in bounded chunks after
checking the archive limits, which count every physical member. Conflicting
contents still fail validation. SDK updates do not rewrite retained archives or
modify App source to recover from identical duplicates.

Sanitization rejects unsafe archive paths and excludes `.env`, `.env.*`,
`node_modules`, `dist`, `build`, `out`, `.git`, coverage, framework caches, and
similar generated directories. Independent App source is capped at 256 MiB
expanded. Treat retained source as private deployment material: access follows
the App/Solution authorization boundary and export streams rather than loading
the archive into API memory.

## UI and CLI

Apps keep their normal deployment status and add a separate SDK badge. Starting
an update changes that badge to `Updating SDK`; the menu action becomes disabled
and progress continues through Notifications. Failed jobs show `SDK update
failed` with a retry action. Apps that need an update but lack retained source
also show `Source unavailable`. Manual updates remain available whether or not
the automatic setting is enabled.

CLI operations:

```bash
bifrost apps sdk status [APP]
bifrost apps sdk update APP
bifrost apps sdk update --all
bifrost apps source export APP ./app-source.zip
bifrost solution sdk deployed-status
bifrost solution sdk deployed-update
```

The last two commands operate on Apps belonging to the bound deployed Solution.
`bifrost solution sdk update` remains the local-development command that updates
an App workspace's installed SDK.

## Initial resource benchmark

The spike used the normal scheduler and its 1 GiB job memory limit. A small App
SDK update completed in 9.76 seconds, starting at 270.6 MiB and peaking at
713.8 MiB (443.2 MiB increase). A dependency-heavy App update completed in
15.88 seconds, starting at 196.0 MiB and peaking at 561.4 MiB (365.4 MiB
increase). Its preceding full deploy took 16.44 seconds and increased by
212.4 MiB.

These are development-stack observations, not capacity guarantees. They do
show that the remembered 200 MiB estimate is too low; keep the PlatformJob's
512 MiB minimum memory-headroom policy until production-like measurements
justify changing it.
