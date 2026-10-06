# Prerelease Candidate Preview

> **Draft review preview — not a published release.** Copy, security/CVE findings and upgrade guidance await review. Asset URLs pin the implementation commit and become accessible when that commit is published to GitHub.

# Bifrost release notes

## Security

### Protect Integration and Webhook Secrets


Admin reads no longer expose decrypted credentials. Webhook signing secrets are encrypted and shown only on creation or rotation; Graph clientState is removed from delivered events.

**Update integrations:** save new signing secrets when shown, stop relying on delivered clientState, and use the authorized workflow SDK for runtime secrets.

**Open Integrations** (in Bifrost)

Sources: [#845](https://github.com/gobifrost/bifrost/pull/845), [#870](https://github.com/gobifrost/bifrost/pull/870), [#871](https://github.com/gobifrost/bifrost/pull/871), [#874](https://github.com/gobifrost/bifrost/pull/874)

### Stricter Data and Execution Access


Table updates check permissions before and after changes. Agent artifacts and WebSocket channels enforce caller scope; app embeds stay within app endpoints.

**Check custom clients:** ordinary session tokens no longer read SDK secrets, and admin endpoints require Platform Admin. These restrictions are enforced; newer workflow permission checks remain report-only.

Sources: [#791](https://github.com/gobifrost/bifrost/pull/791), [#813](https://github.com/gobifrost/bifrost/pull/813), [#814](https://github.com/gobifrost/bifrost/pull/814), [#815](https://github.com/gobifrost/bifrost/pull/815), [#817](https://github.com/gobifrost/bifrost/pull/817)

### Dependency Security Updates


Updated pydantic-ai to 2.53.0 for [GHSA-6fqq-452j-qhrp](https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-6fqq-452j-qhrp) and js-yaml to bound YAML merge CPU use.

**Release review pending:** confirm applicability and CVE identifiers. The SimpleWebAuthn browser update mentions a separate server advisory; it does not establish that Bifrost used the affected server package.

Sources: [#718](https://github.com/gobifrost/bifrost/pull/718), [#910](https://github.com/gobifrost/bifrost/pull/910), [#714](https://github.com/gobifrost/bifrost/pull/714)

## Action Required

### Safer Sync and Reimport


Sync protects Solution-owned entities and rejects missing manifests. Reimport reports missing source instead of deleting entities.

**Update your sync routine:** Fetch before Commit, Sync, or Discard after changing files in Bifrost. Review source deletions through git sync.

**Open GitHub Settings** (in Bifrost)

Sources: [#864](https://github.com/gobifrost/bifrost/pull/864), [#867](https://github.com/gobifrost/bifrost/pull/867), [#868](https://github.com/gobifrost/bifrost/pull/868), [#875](https://github.com/gobifrost/bifrost/pull/875), [#891](https://github.com/gobifrost/bifrost/pull/891)

### Set History Retention


Choose how long to keep completed runs, events, and audit records.

**Before upgrading:** run and event history defaults to 30 days, including existing installs. Select **Keep forever** before cleanup to retain older runs; deleted run details have no archive. Existing installs retain audit archives indefinitely. New installs keep audits for 90 days in the database and 365 days in the archive.

**Open Maintenance** (in Bifrost)

Sources: [#905](https://github.com/gobifrost/bifrost/pull/905), [#908](https://github.com/gobifrost/bifrost/pull/908)

### Update the CLI and MCP Clients


Upgrade the CLI/SDK with the platform: SDKs through 1.4.1 cannot parse the new execution response. Refresh external MCP clients for the renamed `bifrost_<noun>_<verb>` tools. The interactive local runner is removed; direct workflow runs remain. App management now requires Platform Admin.

**Open MCP Settings** (in Bifrost)

Sources: [#820](https://github.com/gobifrost/bifrost/pull/820), [#830](https://github.com/gobifrost/bifrost/pull/830), [#831](https://github.com/gobifrost/bifrost/pull/831), [#833](https://github.com/gobifrost/bifrost/pull/833), [#834](https://github.com/gobifrost/bifrost/pull/834), [#835](https://github.com/gobifrost/bifrost/pull/835)

## What's New

### See a User's Effective Access


See a user's effective access, role assignments, and organization scope. Workflow permission checks remain report-only.

**Open Users** (in Bifrost) · **Open Roles** (in Bifrost)

![Roles and organization scope](https://raw.githubusercontent.com/gobifrost/bifrost/72e730fec8d11ecb65b7220b1de7b82ebe7e98f5/product-updates/assets/fd0c7319-fcc0-5901-8eeb-3ec1b74f98c8/roles.png)

Built-in role grants and organization scope.

Sources: [#879](https://github.com/gobifrost/bifrost/pull/879), [#889](https://github.com/gobifrost/bifrost/pull/889), [#890](https://github.com/gobifrost/bifrost/pull/890), [#895](https://github.com/gobifrost/bifrost/pull/895), [#900](https://github.com/gobifrost/bifrost/pull/900), [#903](https://github.com/gobifrost/bifrost/pull/903), [#914](https://github.com/gobifrost/bifrost/pull/914), [#915](https://github.com/gobifrost/bifrost/pull/915)

### More Reliable Agent Runs


Agents use the configured model context window and can fail over to another profile. Run details show cost, context, and cache use; timed-out specialists retain completed-work evidence.

**Open Agents** (in Bifrost)

Sources: [#783](https://github.com/gobifrost/bifrost/pull/783), [#785](https://github.com/gobifrost/bifrost/pull/785), [#796](https://github.com/gobifrost/bifrost/pull/796), [#801](https://github.com/gobifrost/bifrost/pull/801), [#849](https://github.com/gobifrost/bifrost/pull/849), [#863](https://github.com/gobifrost/bifrost/pull/863), [#904](https://github.com/gobifrost/bifrost/pull/904), [#916](https://github.com/gobifrost/bifrost/pull/916)

Credits: [sdc53](https://github.com/sdc53) (#801)

### Run Workflow Services


Run long-lived listeners as supervised services. Start, stop, or restart them and inspect attempts and live logs.

**Open Services** (in Bifrost)

Sources: [#788](https://github.com/gobifrost/bifrost/pull/788), [#789](https://github.com/gobifrost/bifrost/pull/789), [#812](https://github.com/gobifrost/bifrost/pull/812)

### Restore Complete Solution Backups


Full backups preserve file inventories and table document IDs. Large encrypted restores use less temporary storage, and downloads go directly to object storage.

**Open Solutions** (in Bifrost)

Sources: [#878](https://github.com/gobifrost/bifrost/pull/878), [#880](https://github.com/gobifrost/bifrost/pull/880), [#881](https://github.com/gobifrost/bifrost/pull/881), [#882](https://github.com/gobifrost/bifrost/pull/882), [#899](https://github.com/gobifrost/bifrost/pull/899), [#901](https://github.com/gobifrost/bifrost/pull/901), [#902](https://github.com/gobifrost/bifrost/pull/902), [#907](https://github.com/gobifrost/bifrost/pull/907)

### Compare Workflow Resource Use


Compare workflows by CPU, memory, duration, and AI usage. Older runs without telemetry show blank resource values.

**Open Usage** (in Bifrost)

Sources: [#803](https://github.com/gobifrost/bifrost/pull/803)

### Use Identity and Branding in Apps


The app SDK adds user, role, branding, and organization helpers, plus responsive navigation. Update the SDK or redeploy existing apps to use them.

**Open Apps** (in Bifrost)

Sources: [#894](https://github.com/gobifrost/bifrost/pull/894), [#896](https://github.com/gobifrost/bifrost/pull/896), [#897](https://github.com/gobifrost/bifrost/pull/897), [#898](https://github.com/gobifrost/bifrost/pull/898), [#906](https://github.com/gobifrost/bifrost/pull/906), [#913](https://github.com/gobifrost/bifrost/pull/913)

### Run App Builds in Kubernetes


Run app deploys and SDK rebuilds in isolated Kubernetes pods with memory limits. Requires the compatible Kubernetes builds overlay and execution policy; existing installs keep local execution.

**Open Execution Settings** (in Bifrost)

Sources: [#781](https://github.com/gobifrost/bifrost/pull/781)

### Review Workspace Imports


Choose entities and source from a Solution ZIP or repository snapshot before importing. Review collisions and unchanged items before applying changes.

**Open Solutions** (in Bifrost)

Sources: [#792](https://github.com/gobifrost/bifrost/pull/792)

### Choose and Test Models


Choose models from the models.dev catalog, including OpenCode Go. Profile tests now send a real model request.

**Open AI Settings** (in Bifrost)

Sources: [#807](https://github.com/gobifrost/bifrost/pull/807), [#809](https://github.com/gobifrost/bifrost/pull/809), [#850](https://github.com/gobifrost/bifrost/pull/850), [#853](https://github.com/gobifrost/bifrost/pull/853), [#855](https://github.com/gobifrost/bifrost/pull/855), [#861](https://github.com/gobifrost/bifrost/pull/861)

Credits: [wilhil](https://github.com/wilhil) (#807)

### Search Workspace and Solution Source


Search workspace files and retained Solution source together. Solution results are read-only.

**Open Files** (in Bifrost)

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
