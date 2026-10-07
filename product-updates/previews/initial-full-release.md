# Full Release Backfill Preview

> **Draft review preview — not a published release.** Copy, security/CVE findings and upgrade guidance await review. Asset URLs pin the implementation commit and become accessible when that commit is published to GitHub.

# Bifrost release notes

## Security

### Integration Credential Handling


Integration credentials and webhook signing secrets have tighter access controls.

**Update integrations:** save signing secrets when created or rotated, stop relying on delivered clientState, and use the authorized workflow SDK for runtime secrets.

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


Sync protects Solution-owned resources and reports missing source instead of deleting entities.

**Update sync:** Fetch before Commit, Sync, or Discard after changing files in Bifrost; review deletions through git sync.

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


Agent runs handle context limits, failover, timeouts, and interrupted specialists more reliably.

Sources: [#783](https://github.com/gobifrost/bifrost/pull/783), [#785](https://github.com/gobifrost/bifrost/pull/785), [#796](https://github.com/gobifrost/bifrost/pull/796), [#801](https://github.com/gobifrost/bifrost/pull/801), [#849](https://github.com/gobifrost/bifrost/pull/849), [#863](https://github.com/gobifrost/bifrost/pull/863), [#904](https://github.com/gobifrost/bifrost/pull/904), [#916](https://github.com/gobifrost/bifrost/pull/916)

Credits: [sdc53](https://github.com/sdc53) (#801)

### Run Workflow Services


Run long-lived listeners as supervised services. Start, stop, or restart them and inspect attempts and live logs.

**Open Services** (in Bifrost)

Sources: [#788](https://github.com/gobifrost/bifrost/pull/788), [#789](https://github.com/gobifrost/bifrost/pull/789), [#812](https://github.com/gobifrost/bifrost/pull/812)

### Restore Complete Solution Backups


Solution backups restore file inventories and table documents correctly, including large encrypted archives.

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

### Test Model Connections


Test an AI profile with a real model request before using it in an agent.

**Open AI Settings** (in Bifrost)

Sources: [#807](https://github.com/gobifrost/bifrost/pull/807), [#809](https://github.com/gobifrost/bifrost/pull/809), [#850](https://github.com/gobifrost/bifrost/pull/850), [#853](https://github.com/gobifrost/bifrost/pull/853), [#855](https://github.com/gobifrost/bifrost/pull/855), [#861](https://github.com/gobifrost/bifrost/pull/861)

Credits: [wilhil](https://github.com/wilhil) (#807)

### Search Workspace and Solution Source


Search workspace files and retained Solution source together. Solution results are read-only.

**Open Files** (in Bifrost)

Sources: [#873](https://github.com/gobifrost/bifrost/pull/873)

## Other changes

- [Apply custom logos consistently across Solutions, forms, and integrations.](https://github.com/gobifrost/bifrost/pull/856)
- [Apply organization permissions to live table subscriptions.](https://github.com/gobifrost/bifrost/pull/888)
- [Audit failed sign-ins.](https://github.com/gobifrost/bifrost/pull/892)
- [Audit permission-rule changes.](https://github.com/gobifrost/bifrost/pull/885)
- [Avoid duplicate sign-in redirects when a session expires.](https://github.com/gobifrost/bifrost/pull/764) — [MTG-Thomas](https://github.com/MTG-Thomas)
- [Find global Solutions when working inside an organization.](https://github.com/gobifrost/bifrost/pull/866)
- [Include the latest README in Solution exports.](https://github.com/gobifrost/bifrost/pull/795)
- [Keep streaming responses visible when a new conversation is saved.](https://github.com/gobifrost/bifrost/pull/756) — [MTG-Thomas](https://github.com/MTG-Thomas)
- [Keep workflow services connected after credentials renew.](https://github.com/gobifrost/bifrost/pull/844)
- [Let embedded apps view the workflow runs they started.](https://github.com/gobifrost/bifrost/pull/818)
- [Restrict system-account roles and unavailable agent tools.](https://github.com/gobifrost/bifrost/pull/832)
- [Retry event deliveries with a fresh timeout.](https://github.com/gobifrost/bifrost/pull/745) — [MTG-Thomas](https://github.com/MTG-Thomas)
- [Save changes reliably before requests complete.](https://github.com/gobifrost/bifrost/pull/860)
- [Save organization settings before an integration is mapped.](https://github.com/gobifrost/bifrost/pull/794)
- [Show consistent totals when listing service attempts.](https://github.com/gobifrost/bifrost/pull/819)
- [Show the actual error when OAuth renewal fails.](https://github.com/gobifrost/bifrost/pull/852)
- [Stop app builds when they are cancelled.](https://github.com/gobifrost/bifrost/pull/886)

## Contributors

[MTG-Thomas](https://github.com/MTG-Thomas), [sdc53](https://github.com/sdc53), [wilhil](https://github.com/wilhil)

> Release preparation: the canonical helper adds Docker, type-stub, signature and attestation instructions after an actual tag/version and the required review record are approved.
