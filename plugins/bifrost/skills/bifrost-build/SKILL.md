---
name: bifrost:build
description: Build Bifrost workflows, forms, and apps for both Solution workspaces (v2) and the global _repo workspace (v1). Supports SDK-first (local dev + git) and MCP-only modes.
---

# Bifrost Build

Build Bifrost artifacts — apps, workflows, forms, agents, tables. This hub detects your workspace mode and routes you to the right entry doc.

## Prerequisites

```bash
echo "SDK: $BIFROST_SDK_INSTALLED | Login: $BIFROST_LOGGED_IN | MCP: $BIFROST_MCP_CONFIGURED"
echo "Source: $BIFROST_HAS_SOURCE | Path: $BIFROST_SOURCE_PATH | URL: $BIFROST_DEV_URL"
```

**If SDK or Login is false/empty:** Direct user to run `/bifrost:setup` first.

**If `BIFROST_DEV_URL` is empty:** Ask the user: "I don't see `BIFROST_DEV_URL` set — what URL should I use for previews and platform links?" Never invent a `*.gobifrost.com` / `*.musick.gg` / etc. host.

## Step 1: Detect Workspace Mode

Walk up from the current directory looking for `bifrost.solution.yaml` (written by `bifrost solution init`):

```bash
# Walk up from cwd looking for bifrost.solution.yaml
dir="$PWD"; found=""
while [ "$dir" != "/" ]; do
  [ -f "$dir/bifrost.solution.yaml" ] && found="$dir/bifrost.solution.yaml" && break
  dir="$(dirname "$dir")"
done
echo "${found:-NOT_FOUND}"
```

**Found → Solution workspace (v2).** Read `references/solutions.md` and follow it.
- Entities (apps, workflows, forms, agents, tables, configs) are deploy-owned. They ship with the solution and are installed/updated by `bifrost solution deploy` — NOT by live CLI mutations. Calling `bifrost forms create` (or any entity create/update) in a solution workspace 409s because deploy owns those records.
- You author app code in `apps/` and Python workflows in `functions/`, then deploy with `bifrost solution deploy`.

**Not found → Global `_repo` workspace (v1).** Read `references/repo.md` and follow it.
- Entities are mutated live via entity CLI verbs (create, update, delete).
- File-backed entities (workflow `.py`, app `.tsx`) are synced by the watch daemon after the user starts it.
- MCP-only mode (no local source) also follows repo.md.

**Do not use `.bifrost/*.yaml` as a mode marker.** It is an export artifact and may be absent from any workspace.

## Step 2: Confirm Org + Access Before Scaffolding

This is mode-agnostic — always do it before creating anything.

1. **Which org?** Ask in natural language. Resolve to a UUID:
   ```bash
   bifrost orgs list --json
   bifrost orgs get "Org Name" --json
   ```

2. **Who has access?** Confirm the access tuple `(organization, access_level, role_ids)` and apply it consistently to the app and every supporting workflow, form, and agent. Mismatched access is the most common reason a working app silently 403s.

   **Compatibility rule:** when an app is assigned to roles X (under Org A or global), every supporting entity must satisfy at least one of:
   - **Global** scope (any org, available to all roles), OR
   - **Org A scoped + `access_level=authenticated`** (any logged-in user in Org A), OR
   - **Same role(s) X** assigned to it.

   Example — Finance app for Org A, role-restricted to `finance`:
   ```bash
   bifrost apps create --name "Finance" --slug finance --organization "Org A" \
     --access-level role_based --role-ids finance
   bifrost forms create --name "Submit Invoice" --workflow <uuid> \
     --organization "Org A" --access-level role_based --role-ids finance
   bifrost workflows register --path workflows/finance.py --function-name process_invoice \
     --org "Org A" --access-level role_based --role-ids finance
   ```

   Every workflow the Finance app calls must be either global, Org-A+authenticated, or `finance`-role — otherwise the Finance user gets a 403 at execution time.

   Discover roles:
   ```bash
   bifrost roles list --json
   ```

## Global Hard Rules (Both Modes)

- **In a Solution workspace, entities are deploy-owned — never mutate them live.** Creating or updating a form, agent, table, config, app, or workflow via the entity CLI create/update verbs against a solution-managed record 409s (deploy owns them). Author the content in the workspace and ship it with `bifrost solution deploy`. (This rule does NOT apply in the global `_repo` workspace, where live mutation is the normal path — see the mode dispatcher above.)
- **Never run the watch/push/pull/sync/git commands unsolicited.** These are user-driven, have broad blast radius, or launch interactive TUIs. Describe them to the user and ask them to run the command themselves. This applies in both solution and repo mode.
- **Never call `bifrost api` for third-party integration APIs** (HaloPSA, Pax8, NinjaOne, etc.). `bifrost api` is the Bifrost platform API only. Call integration APIs from within a workflow using the SDK, not the `bifrost api` passthrough.
- **Check before using `bifrost api GET <path>`.** If you don't know whether a platform endpoint exists, check `generated/openapi-digest.md` or the entity's `--help` flag first. Never guess URL patterns.
- **Do NOT read `.bifrost/*.yaml` for discovery.** It is an export artifact, not the source of truth. Use entity list/get commands (`bifrost forms list --json`, `bifrost workflows get <ref> --json`, etc.).

## MCP Tool Naming Convention (CRITICAL for Discoverability)

When workflows are exposed as MCP tools (via agents), their `name` becomes the MCP tool name and `description` becomes the MCP tool description. Claude.ai uses deferred tool search: tools compete on relevance ranking across ALL connected MCP servers. Generic names like `list_findings` get buried.

**Format:** `{context}_{action}` — prefix every tool name with a distinctive context word.

**Descriptions:** Must include the agent/feature name and enough distinctive vocabulary to win search ranking. Lead with what it does; include the domain context.

| Bad name | Good name | Bad description | Good description |
|----------|-----------|-----------------|------------------|
| `list_findings` | `list_agent_tuning_findings` | "List findings" | "List agent tuning findings from Bifrost AI agent run reviews with filtering and pagination" |
| `review_agent_runs` | `review_agent_tuning_runs` | "Review an agent's runs" | "Review a Bifrost AI agent's recent conversation runs and create tuning findings for prompt issues" |
| `dry_run_prompt` | `dry_run_agent_tuning_prompt` | "Test a candidate prompt" | "Generate a candidate prompt from confirmed agent tuning findings and dry-run test it against historical runs" |

**Rules:**
1. Every tool name MUST contain a context prefix that identifies its agent/feature (e.g. `agent_tuning_`, `halopsa_`, `documentation_`)
2. The `description` MUST mention the agent or feature name — this is what `tool_search` ranks on
3. Descriptions should be self-contained — someone seeing ONLY the description (no server name) should know what domain this tool belongs to
4. Follow professional MCP conventions: `microsoft_docs_search`, `outlook_email_search`, `execute_halopsa_sql`

## Reference Index

| I need to… | Read |
|---|---|
| Build in a Solution workspace | `references/solutions.md` |
| Build in the global `_repo` workspace | `references/repo.md` |
| Build or modify an app (TSX/React) | `references/apps.md` · `references/web-sdk-v2.md` |
| Write or debug a Python workflow | `references/workflows-python.md` · `references/python-sdk.md` |
| Build or configure an agent | `references/entities.md` |
| Create a form | `references/entities.md` |
| Create/update/delete any CLI entity | `references/entities.md` |
| Work with tables (read/write structured data) | `references/tables.md` |
| MCP-only mode (no local source) | `references/mcp-mode.md` |
| Exact CLI flag for a command | `generated/cli-reference.md` |
| Does a platform endpoint exist? | `generated/openapi-digest.md` |

## Session Summary

At end of session, provide:

```markdown
## Session Summary

### Completed
- [What was built/accomplished]

### Notes for Future Sessions
- [Relevant context]
```
