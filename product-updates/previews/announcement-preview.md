# Backfill With Staged Announcement

> **Draft review preview — not a published release.** Copy, security/CVE findings and upgrade guidance await review. Asset URLs pin the implementation commit and become accessible when that commit is published to GitHub.

# Bifrost release notes

## Security

### Keep Secrets out of Admin Reads and Event History


Admin REST, MCP, and CLI reads return the existing secret placeholder instead of decrypted integration credentials. Workflows continue to obtain credentials through the authorized SDK path.

Generic webhook signing secrets are encrypted at rest and shown only when created or rotated. Microsoft Graph clientState is encrypted and removed from delivered notifications. Per-execution engine tokens are redacted from output, excluded from persisted diagnostic context, and rejected after the execution finishes.

**Review your integrations:** copy a new webhook signing secret when it is created or rotated; it cannot be read back later. Consumers that previously used clientState from delivered Graph events must stop depending on that field. Use the authorized workflow SDK for runtime secrets.

Sources: [#845](https://github.com/gobifrost/bifrost/pull/845), [#870](https://github.com/gobifrost/bifrost/pull/870), [#871](https://github.com/gobifrost/bifrost/pull/871), [#874](https://github.com/gobifrost/bifrost/pull/874)

### Tighten Execution and Data Access Boundaries


Table updates must satisfy the row policy before and after the change, preventing a permitted row from being rewritten into a forbidden organization. Agent runs, artifacts, and WebSocket channels follow the caller's permitted scope.

SDK credential routes admit real execution credentials and the permitted platform principals. App embed sessions are confined to their app and workflow endpoints instead of also reaching same-organization form runtime routes.

**Check custom clients before upgrading:** ordinary user session tokens no longer read secrets through SDK credential routes, and admin-only endpoints require platform administration. These are implemented restrictions, separate from the newer report-only workflow permission checks.

Sources: [#791](https://github.com/gobifrost/bifrost/pull/791), [#813](https://github.com/gobifrost/bifrost/pull/813), [#814](https://github.com/gobifrost/bifrost/pull/814), [#815](https://github.com/gobifrost/bifrost/pull/815), [#817](https://github.com/gobifrost/bifrost/pull/817)

### Review Dependency Security Fixes


The dependency refresh includes pydantic-ai 2.53.0, which addresses [GHSA-6fqq-452j-qhrp](https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-6fqq-452j-qhrp): streamed requests could retain a shared concurrency-limiter slot and eventually block requests using that limiter. The upstream advisory says agent-level concurrency and non-streaming requests are not affected.

The updated SimpleWebAuthn **browser** dependency includes upstream notes about a separate server-package advisory and raised runtime requirements. Those notes need an applicability review; this change does not prove that Bifrost shipped the affected server package.

The docs tooling also picks up a js-yaml change that bounds CPU work in YAML merge handling.

**Release review required:** confirm deployment applicability, any assigned CVE identifiers, and the upstream compatibility notes before publishing a security summary. This draft records the observed advisories; it does not assert that no CVEs were fixed.

Sources: [#718](https://github.com/gobifrost/bifrost/pull/718), [#910](https://github.com/gobifrost/bifrost/pull/910), [#714](https://github.com/gobifrost/bifrost/pull/714)

## Action Required

### Review Safer Workspace Sync and Reimport


Workspace sync now protects Solution-owned entities and refuses an absent manifest that would otherwise delete the workspace. Maintenance Reimport reports missing source instead of deleting entities. External subscriptions to a Solution's events survive redeployment, and sync cannot deactivate the provider organization.

**Update your sync routine:** after changing files in Bifrost, run **Fetch** before Commit, Sync, or Discard. These actions refuse stale working trees. Discard also refuses while a merge is unfinished, keeping conflict markers out of the running source.

To remove entities whose source is missing, review and confirm the deletions through git sync. Reimport is no longer a deletion mechanism.

Sources: [#864](https://github.com/gobifrost/bifrost/pull/864), [#867](https://github.com/gobifrost/bifrost/pull/867), [#868](https://github.com/gobifrost/bifrost/pull/868), [#875](https://github.com/gobifrost/bifrost/pull/875), [#891](https://github.com/gobifrost/bifrost/pull/891)

### Choose How Long History Stays


Admins can set how long Bifrost keeps completed runs, events, and audit records in **Settings → Maintenance**. Run history keeps cost and resource totals after the detailed records are removed. Audit records move to object storage and are read back and verified before their database rows are cleared.

**Review before upgrading:** run and event history defaults to **30 days**, including existing installs. Choose **Keep forever** before the daily cleanup if you need older detailed runs. Deleted run history has no archive. Audit defaults differ: existing installs retain archives indefinitely; new installs keep 90 days in the database and 365 days in the archive.

Preview and Run Now controls let an admin inspect and apply the policy. Active, paused, and cancelling runs are protected from history cleanup.

Sources: [#905](https://github.com/gobifrost/bifrost/pull/905), [#908](https://github.com/gobifrost/bifrost/pull/908)

### Update Clients for the CLI and MCP Changes


MCP tools for platform entities now call the same REST endpoints and use canonical `bifrost_<noun>_<verb>` names. Stored platform agent tool references and allow/block lists are migrated; external clients that retain old names need to refresh their discovery or configuration.

**Upgrade the CLI/SDK with the platform.** The obsolete interactive local runner and its session endpoints are removed. Direct local workflow runs remain available. SDK versions through 1.4.1 cannot parse the changed execution response, and the current minimum client floor is 1.4.2; release preparation must keep that floor aligned with the version that ships this change.

App management requires Platform Admin. Custom provider-staff clients must review the new boundary. The renamed MCP tools preserve endpoint validation and audit behavior instead of reimplementing it.

Sources: [#820](https://github.com/gobifrost/bifrost/pull/820), [#830](https://github.com/gobifrost/bifrost/pull/830), [#831](https://github.com/gobifrost/bifrost/pull/831), [#833](https://github.com/gobifrost/bifrost/pull/833), [#834](https://github.com/gobifrost/bifrost/pull/834), [#835](https://github.com/gobifrost/bifrost/pull/835)

## What's New

### Product Updates, Now in Bifrost


Product release notes are available right in the app. Open **What's New** to see what's changed, catch up on unread updates, and find the details that matter before you upgrade.

We're also [on Discord](https://discord.gg/f7TCcWX2s). Join the Bifrost community to ask questions, share workflows, and talk about what you're building.

GitHub, Discord, and the Bifrost website are always a click away in the footer.

### See What a Person Can Access—and Why


Open a person from **Users** to see their effective access, role assignments, and the organizations those roles reach. The Roles page shows grants, holders, and placements; the permission catalog makes the vocabulary searchable.

For recorded access checks, admins can compare the decision at the time with the same inputs evaluated against today's settings. What-if checks are available through the API and CLI.

**The workflow access checks are report-only.** They describe what the proposed model would allow or block and do not change requests. Identity-management routes already use the role evaluator; this update does not claim that every permission area is enforced. Default unattended identities and run lineage prepare the remaining execution model, and the interface identifies permissions whose enforcement is still pending.

![Roles page showing built-in role grants, holders, and organization placements in a seeded development instance.](https://raw.githubusercontent.com/gobifrost/bifrost/72e730fec8d11ecb65b7220b1de7b82ebe7e98f5/product-updates/assets/fd0c7319-fcc0-5901-8eeb-3ec1b74f98c8/roles.png)

Built-in roles, their grants, and where they apply. Captured from this isolated development build.

Sources: [#879](https://github.com/gobifrost/bifrost/pull/879), [#889](https://github.com/gobifrost/bifrost/pull/889), [#890](https://github.com/gobifrost/bifrost/pull/890), [#895](https://github.com/gobifrost/bifrost/pull/895), [#900](https://github.com/gobifrost/bifrost/pull/900), [#903](https://github.com/gobifrost/bifrost/pull/903), [#914](https://github.com/gobifrost/bifrost/pull/914), [#915](https://github.com/gobifrost/bifrost/pull/915)

### Keep Agent Runs Useful When Models Struggle


Agent context management uses the configured model chain's real context window instead of a fixed 24,000-token limit. Run details show cost, context, and cache use. Profiles can specify output limits and an explicit failover profile for transport failures after the normal retry budget is exhausted.

Structured output wrapped in a Markdown JSON fence is parsed into the requested object. SDK callers can wait within workflow deadlines and resume waiting using the returned run id.

If a delegated specialist times out after completing checks, the parent receives a bounded receipt of completed and unfinished work. Timeout remains a failure; the receipt preserves evidence rather than declaring success. A sweeper also finishes agent runs left cancelling after their worker is gone.

Sources: [#783](https://github.com/gobifrost/bifrost/pull/783), [#785](https://github.com/gobifrost/bifrost/pull/785), [#796](https://github.com/gobifrost/bifrost/pull/796), [#801](https://github.com/gobifrost/bifrost/pull/801), [#849](https://github.com/gobifrost/bifrost/pull/849), [#863](https://github.com/gobifrost/bifrost/pull/863), [#904](https://github.com/gobifrost/bifrost/pull/904), [#916](https://github.com/gobifrost/bifrost/pull/916)

Credits: [sdc53](https://github.com/sdc53) (#801)

### Run Long-Lived Workflow Services


Use a supervised workflow service for a long-lived listener or integration. Bifrost manages worker claims, startup readiness, renewable credentials, crash recovery, and stop transitions through its shared execution infrastructure.

The Services interface and CLI expose start, stop, restart, enable, disable, policy settings, attempts, and trailing logs. Live log updates and attempt history help explain what a listener is doing.

Solution-backed services preserve their permitted workspace source access when credentials rotate, including a cold source load after a restart.

Sources: [#788](https://github.com/gobifrost/bifrost/pull/788), [#789](https://github.com/gobifrost/bifrost/pull/789), [#812](https://github.com/gobifrost/bifrost/pull/812)

### Preserve Complete Solution Backups and Relationships


Full Solution backups include the complete file inventory and preserve table document ids, keeping references between documents, folders, attachments, and other records intact. Oversized tables fail visibly instead of silently producing a partial backup.

Large encrypted backups restore by reading payloads from the archive without extracting another encrypted copy. Durable downloads go directly through a short-lived authorized object-storage link, so the browser does not buffer the whole archive in memory.

Full exports can run in isolated Kubernetes build pods when that execution policy is enabled. Lost runners produce a failed export state. Apps can also link directly to their owning Solution's backup history.

Sources: [#878](https://github.com/gobifrost/bifrost/pull/878), [#880](https://github.com/gobifrost/bifrost/pull/880), [#881](https://github.com/gobifrost/bifrost/pull/881), [#882](https://github.com/gobifrost/bifrost/pull/882), [#899](https://github.com/gobifrost/bifrost/pull/899), [#901](https://github.com/gobifrost/bifrost/pull/901), [#902](https://github.com/gobifrost/bifrost/pull/902), [#907](https://github.com/gobifrost/bifrost/pull/907)

### Compare Workflow Resource Use


The **Workflow Resources** view in Usage shows runs and a per-workflow ranking with CPU, memory, duration, and AI usage. Filters and the selected view remain in the URL, and returning from a run restores the filtered list.

New runs record sampled resource peaks. Older runs show blank values where no telemetry was captured. Peak CPU measures the share of one core and can exceed 100% when a run uses multiple cores; brief bursts may fall between samples.

Sources: [#803](https://github.com/gobifrost/bifrost/pull/803)

### Build Apps with Platform Identity and Branding


V2 apps can use `useUser` and `RequireRole` for user-aware presentation, `useBranding` for platform names, logos, and theme palettes, and `useOrganizations` for organizations the caller is allowed to see. These helpers preserve the underlying endpoint authorization; hiding a control is not an access gate.

The starter app has a top navigation shell and an intentionally unwired example button, so a new independent app does not call a workflow it never installed. The header supports navigation and a compact, keyboard-accessible phone menu, including correct desktop restoration in React 18 apps.

Existing apps receive these SDK changes through an SDK update or redeploy. Installed plugin authoring guidance arrives with the next versioned plugin release.

Sources: [#894](https://github.com/gobifrost/bifrost/pull/894), [#896](https://github.com/gobifrost/bifrost/pull/896), [#897](https://github.com/gobifrost/bifrost/pull/897), [#898](https://github.com/gobifrost/bifrost/pull/898), [#906](https://github.com/gobifrost/bifrost/pull/906), [#913](https://github.com/gobifrost/bifrost/pull/913)

### Run Heavy App Builds in Isolated Kubernetes Pods


App deploys and SDK rebuilds can run in temporary Kubernetes pods with their own memory limits, keeping heavy compilers away from the shared scheduler.

The execution settings expose per-job placement and concurrency controls, and Diagnostics identifies jobs running in Kubernetes. Shared platform-job policies manage claims, progress, timeouts, and cleanup.

This is opt-in. Existing installs keep local execution until an operator enables the compatible Kubernetes builds overlay and execution policy. It does not move every workflow or platform operation into a remote pod.

Sources: [#781](https://github.com/gobifrost/bifrost/pull/781)

### Review a Solution Before Importing It into the Workspace


Import a Solution ZIP or repository snapshot into the workspace with a scoped review. Choose which entities and source to bring in, resolve collisions, and see unchanged items before applying the import.

The import preserves portable entities, configuration declarations, integration shells, roles, claims, and file policies. Progress uses the shared platform-job system. A workspace import stays distinct from a managed Solution installation.

Sources: [#792](https://github.com/gobifrost/bifrost/pull/792)

### Choose Models from a Shared Catalog


Model settings now use the models.dev catalog for provider choices, context windows, pricing, capabilities, and reasoning controls. A bundled snapshot remains available offline; a refresh can bring in newer catalog data. Community providers are labeled separately from Bifrost's native integrations.

OpenCode Go is a supported provider, with its model-specific request surfaces and session headers. Connection and profile **Test** buttons now send a real request through saved model settings, so a working model list alone no longer makes a configured profile look healthy.

A connection without profiles explains that it can only test model discovery. Existing and manually entered prices keep their precedence.

Sources: [#807](https://github.com/gobifrost/bifrost/pull/807), [#809](https://github.com/gobifrost/bifrost/pull/809), [#850](https://github.com/gobifrost/bifrost/pull/850), [#853](https://github.com/gobifrost/bifrost/pull/853), [#855](https://github.com/gobifrost/bifrost/pull/855), [#861](https://github.com/gobifrost/bifrost/pull/861)

Credits: [wilhil](https://github.com/wilhil) (#807)

### Search Workspace and Solution Source Together


Source search covers workspace files and retained Solution source through one paged search path. Solution results are labeled read-only, and callers can narrow the search to a particular source or Solution.

One indexer handles writes, moves, deletes, and bulk refreshes. Reconciliation compares current objects without restoring deleted files, and a concurrent save wins over an older reconciliation read. Binary or oversized files remain searchable by path.

Sources: [#873](https://github.com/gobifrost/bifrost/pull/873)

## Other changes

- [Apply three minor and patch updates to GitHub Actions.](https://github.com/gobifrost/bifrost/pull/842)
- [Apply two minor and patch updates to client dependencies.](https://github.com/gobifrost/bifrost/pull/839)
- [Commit request database changes before sending the response.](https://github.com/gobifrost/bifrost/pull/860)
- [Finish legacy logo migration for Solutions, forms, and integrations.](https://github.com/gobifrost/bifrost/pull/856)
- [Give each event-delivery retry a fresh timeout window.](https://github.com/gobifrost/bifrost/pull/745) — [MTG-Thomas](https://github.com/MTG-Thomas)
- [Include the latest Solution README in shareable and full exports.](https://github.com/gobifrost/bifrost/pull/795)
- [Keep roles off the system account and list only usable agent tools.](https://github.com/gobifrost/bifrost/pull/832)
- [Keep service SDK authentication working after credential renewal.](https://github.com/gobifrost/bifrost/pull/844)
- [Let embedded apps read the workflow executions they started.](https://github.com/gobifrost/bifrost/pull/818)
- [Preserve audit attribution and API error behavior on the worker SDK path.](https://github.com/gobifrost/bifrost/pull/811)
- [Preserve separately discovered MCP resource documents.](https://github.com/gobifrost/bifrost/pull/822)
- [Preserve streaming activity when a new conversation receives its id.](https://github.com/gobifrost/bifrost/pull/756) — [MTG-Thomas](https://github.com/MTG-Thomas)
- [Record operation ids and caller surfaces in audit rows, and measure workflow operation use.](https://github.com/gobifrost/bifrost/pull/848)
- [Record policy-rule changes in the audit log.](https://github.com/gobifrost/bifrost/pull/885)
- [Record refused sign-ins in the audit log.](https://github.com/gobifrost/bifrost/pull/892)
- [Refresh client dependencies with fifteen minor and patch updates.](https://github.com/gobifrost/bifrost/pull/911)
- [Refresh client dependencies with ten minor and patch updates.](https://github.com/gobifrost/bifrost/pull/828)
- [Refresh the Nginx client image.](https://github.com/gobifrost/bifrost/pull/710)
- [Refresh the Node container image.](https://github.com/gobifrost/bifrost/pull/752)
- [Refresh the Python container image.](https://github.com/gobifrost/bifrost/pull/708)
- [Remove failed repository Solution installs in the same transaction as job failure.](https://github.com/gobifrost/bifrost/pull/825)
- [Remove withdrawn Builder schema residue before later migrations.](https://github.com/gobifrost/bifrost/pull/857)
- [Resolve global Solution installs by name from an organization-scoped caller.](https://github.com/gobifrost/bifrost/pull/866)
- [Restore the audit actor in platform-job execution.](https://github.com/gobifrost/bifrost/pull/821)
- [Retry safe SDK reads and explicitly keyed writes after transient disconnects.](https://github.com/gobifrost/bifrost/pull/797)
- [Return consistent totals and items when listing service attempts.](https://github.com/gobifrost/bifrost/pull/819)
- [Run engine SDK requests through the worker-local HTTP socket.](https://github.com/gobifrost/bifrost/pull/810)
- [Save organization configuration overrides before an integration mapping exists.](https://github.com/gobifrost/bifrost/pull/794)
- [Show the provider error when an OAuth refresh returns success=false.](https://github.com/gobifrost/bifrost/pull/852)
- [Start one login redirect when concurrent requests lose authentication.](https://github.com/gobifrost/bifrost/pull/764) — [MTG-Thomas](https://github.com/MTG-Thomas)
- [Stop child Node processes when app builds are cancelled.](https://github.com/gobifrost/bifrost/pull/886)
- [Update client dependencies and resolve the accompanying compatibility issues.](https://github.com/gobifrost/bifrost/pull/827)
- [Update Playwright to 1.63.0 for browser testing.](https://github.com/gobifrost/bifrost/pull/709)
- [Update the AI runtime libraries and their provider dependencies.](https://github.com/gobifrost/bifrost/pull/824)
- [Update the GitHub Actions used to build, sign, and test releases.](https://github.com/gobifrost/bifrost/pull/786)
- [Update the Python AI runtime dependencies.](https://github.com/gobifrost/bifrost/pull/837)
- [Update Vitest and its UI to 5.0.2.](https://github.com/gobifrost/bifrost/pull/826)
- [Use the REST organization rule for WebSocket table subscriptions.](https://github.com/gobifrost/bifrost/pull/888)
- [Wait for the database schema before starting workers and the scheduler.](https://github.com/gobifrost/bifrost/pull/858)

## Contributors

[MTG-Thomas](https://github.com/MTG-Thomas), [sdc53](https://github.com/sdc53), [wilhil](https://github.com/wilhil)

> Release preparation: the canonical helper adds Docker, type-stub, signature and attestation instructions after an actual tag/version and the required review record are approved.
