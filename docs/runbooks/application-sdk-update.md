# Application SDK updates

Bifrost exposes SDK provenance on deployed Apps and lets an administrator
rebuild selected Apps against the SDK shipped by the current Bifrost instance.
Manual updates keep their current behavior. Automatic updates are an opt-in,
reviewed source-compatible transition; they do not apply developer code
changes.

## Provenance and status

Every successful V2 build records four values on the App:

- `sdk_package_version`: npm-safe Bifrost release version.
- `sdk_fingerprint`: the first 16 hex characters of SHA-256 over the bundled
  SDK bytes. Compatible source changes therefore become visible without a
  manual version bump.
- `sdk_contract_version`: the integer in
  `client/src/lib/app-sdk/sdk-contract.json`. Bump it only for a deliberately
  breaking SDK↔server wire-contract change.
- `sdk_built_at`: when that SDK was activated for the App.

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
or mutate sibling entities. Batch actions enqueue one independently observable
job per actionable App so the scheduler retains its normal admission control,
resource protection, cancellation, and failure reporting.

## Automatic App SDK Upgrades

In **Settings → Maintenance**, **App SDK Upgrades** has an **Enable Automatic
App SDK Upgrades** toggle. It defaults to off while this capability is being
dogfooded; the intended future default is on after that proof is complete.

When enabled, the elected scheduler reconciles at startup and once per minute,
considering at most 20 candidates per pass. It queues ordinary
`application.sdk_update` PlatformJobs, whose progress appears in
Notifications. An automatic attempt is eligible only for an active,
source-built App with retained source that needs a transition explicitly
approved by SDK metadata. The currently reviewed predecessor is contract 2 to
contract 3 only.

The scheduler attempts an App/target pair once, including failures. Use the
existing manual **Update SDK** action to retry a failure. Automatic updates do
not change authorization or add an authentication bypass.

Automatic rebuilds require a valid retained `package-lock.json`. The compiler
may refresh the vendored SDK lock entry, but refuses unrelated dependency
changes. An App without a valid reproducible lockfile is left for a manual
update or source redeploy.

Automatic jobs pin the SDK fingerprint and contract before compilation, then
verify that same target before activation. They retain the prior deployment
artifacts for recovery; failures leave the active deployment unchanged, and
there is no automatic rollback.

`application.sdk_update` uses payload version 2 for newly queued work. New
workers also finish already queued version 1 manual updates with their original
behavior, so those jobs do not need to be drained. A worker that predates
version 2 refuses a pinned automatic job rather than ignoring its target.
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
