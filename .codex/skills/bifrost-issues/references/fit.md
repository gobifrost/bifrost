# Step 2 — Fit

**Does this move Bifrost forward as an extensible platform?**

## 2a. Placement

| Home | Use for | Must show |
|------|---------|-----------|
| Solution (built on Bifrost) | Domain products, vendor integrations, business processes | Existing APIs/SDK/workflows/apps support it; name any remaining platform gap |
| Shared platform capability | A reusable contract Solutions or core need | The concrete limitation, affected consumers, smallest general improvement |
| Native authoring/operating tool | Helping people build, test, deploy, operate on Bifrost | The builder benefit and why first-party earns its ongoing cost over a Solution |

- Provider- or vendor-specific native features fail by default. This includes native
  vendor app integrations (a GitHub App, a Slack app) for features that are not the
  platform's own; build them on integrations, webhooks and workflows and propose only
  the smallest shared gap. Exception: auth for the platform's own functions (e.g. Git
  sync). Popularity, a frequent
  vendor, or convenience for one deployment is not enough. Compare SDK helpers,
  workflows, mapping configuration and existing extension points first.
- Decline adding a native tool when the submitted job is already served by external tooling and the proposal establishes no distinct in-platform task. Name the existing owner (for example, repository CI for test collection and fixtures). A smaller product feature with a different purpose is a separate idea and needs its own evidence.
- When a Solution exposes a platform gap, split it: the enabling capability goes to core,
  the business rules stay in the Solution.
- "Use a Solution" alone is not an assessment — show the concrete path or the gap.

## 2b. Reuse

- Find the existing feature, service, component or permission that already does this or
  nearly does. Search the codebase (`rg`), issues, and open PRs. Name what you found.
- Prefer improving the existing path over a parallel one. A second implementation of
  something we have (job system, scheduler, dialog, table, permission check) fails.
- Prefer existing permission and capability boundaries before inventing a restriction.
- Check adjacent entry points: if workflows get a control, forms/agents/apps that share
  the concept likely need it too.
- Keep scope proportional: a small fix repairs the existing path; no unrelated rewrite,
  no framework for hypothetical futures.

## Precedents

- #649: a native subscription-auth feature was declined — unsupported-call approach,
  extra worker/runtime complexity, narrow personal-use audience.
- #1221: an assumed admin-only boundary was challenged by pointing at existing CLI `--org`
  behavior — reuse existing capability boundaries. Same item asked for the control on
  forms too, not only workflows — check related entry points.
- Philosophy examples: Microsoft CSP management → Solution. Azure Blob storage → assess a
  shared storage contract with provider adapters. Agent workbench → strong native-tool
  candidate.
