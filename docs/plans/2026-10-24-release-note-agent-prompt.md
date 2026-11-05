# Release notes: "Run with an agent" prompt block

Design, 2026-10-24. Jack asked for a standard way to give admins an easy
button for breaking or recommended changes: explain the change, then offer a
prompt to copy into an agent that has access to their setup.

## Revision (Jack, 2026-10-24, during implementation)

Call it **Agent Prompt**. The callout shows the Copy Prompt button and one
description, "Copy this prompt to have your coding agent help you with this
change."; the prompt box stays collapsed and expands on request. With a fixed
heading and description, `agent_prompt` is plain text: `needs` and `title`
are dropped, and the prompt itself says what the agent must reach. The GitHub
release keeps the same heading and sentence and collapses the fence in a
`<details>` block. This supersedes the field table and the rendering text
below.

## Shape

A Product Updates entry gets an optional `agent_prompt` with three fields:

| Field | Purpose |
| --- | --- |
| `needs` | What the agent must be able to reach, in plain words. Example: "your Bifrost docker-compose.yml and shell", "kubectl access to your Bifrost namespace". |
| `prompt` | Plain text the admin copies verbatim. |
| `title` | The callout heading (optional). Defaults to "Do It With an Agent". |

Rules:
- The prompt does not count toward the entry's word budget, like images.
- It has its own limit of 400 words.
- It is reviewed with the entry.
- It never contains secrets, install-specific URLs, or instructions to
  disable safety checks.
- It tells the agent to show its plan and wait for the admin's confirmation
  before changing anything.

## Rendering

- **App (What's New):** a callout under the entry body.
  - It shows an agent icon (Lucide `Bot`, in the theme's accent colour), the
    title, and "Copy this prompt into an agent that can reach {needs}."
  - The prompt sits in a read-only, scrollable monospace box with a
    **Copy Prompt** button.
  - The icon is agent-neutral, not the Claude logo: admins use Claude Code,
    Codex and others, and the trademarks are not ours to put in the product.
- **GitHub release:** a `#### Do It With an Agent` subheading, the same
  sentence, and the prompt in a fenced `text` block, which GitHub renders
  with its own copy button.

## Validation and rendering details

- **Schema:** add `agent_prompt` to both the source entry and the bundle
  entry (both are `additionalProperties: false`); `build_bundle` copies it.
- **Validator:**
  - at most 400 words;
  - `needs` is non-empty;
  - no raw HTML (reuse `RAW_HTML`);
  - no URLs except the Bifrost docs and `github.com/gobifrost`;
  - no backticks;
  - must contain the confirmation step ("wait for my confirmation"), so
    the safety rule is machine-checked.
- **GitHub fence:** the prompt is rendered in a fence and skips the
  installation-relative link rewrite.
- **App:** the callout shows on full entries; `Fixed`/`Security` entries
  render compact and do not show it. `in_app: false` entries never show.
  - The prompt box is content-sized up to about 12 lines, then scrolls.
  - Copy uses the existing secure-context fallback (`PolicyExampleBlock`),
    with success and error states and an `sr-only` status.
- **Discord:** no renderer exists; posting a prompt there is a manual step.

## Tooling

- **Schema:** `product-updates/schema.json`, then regenerate the TypeScript
  types. That covers the validator (`scripts/product_updates.py`: fields,
  limit, plain text), the bundle, `render-release`, and the client component
  (`ProductUpdateContent`), with a vitest test for Copy.
- **Authoring guide (`docs/product-updates-authoring.md`):** when to add a
  prompt. Use it for breaking changes, recommended upgrade steps and
  first-time setup of a new capability. Don't use it for ordinary features.
  The guide also gets a prompt template.
- **`bifrost-release` skill (Claude and Codex):** a checklist item asking
  whether an action-required or recommended entry should carry a prompt.

## Prompt template

```text
You are helping me with this Bifrost change: <one sentence>.
You can reach: <needs>.
1. Inspect my current setup and report what applies to it.
2. Propose the exact changes, and wait for my confirmation.
3. Apply them, then verify with: <checks>.
Do not change anything unrelated, and stop if something does not match
what you expect.
```

## First use

The "Recommended: Upgrade to the Unified Worker" entry from #1055. Its
prompt covers:
- the Compose `worker` service (2 GB, two replicas);
- `--remove-orphans`;
- RabbitMQ and KEDA removal after the API logs that it drained;
- deleting the old Kubernetes Deployments;
- leftover environment variables.
