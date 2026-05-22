---
name: bifrost:build
description: Build Bifrost workflows, forms, and apps. Use when user wants to create, debug, or modify Bifrost artifacts. Supports SDK-first (local dev + git) and MCP-only modes.
---

# Bifrost Build

Create and debug Bifrost artifacts.

## Who Runs What (read this first)

The skill draws a hard line between commands the agent runs and commands the user runs. Mixing them up is the #1 source of confusion.

| Action | Who runs it | Why |
|---|---|---|
| `bifrost run <file>`, `bifrost workflows execute`, `bifrost <entity> create / update / delete / get / list`, `bifrost api GET/POST`, `bifrost requirements install` | **Agent** | Non-interactive, idempotent, scoped to the entity at hand |
| Writing files into `apps/` and `workflows/` | **Agent** (after confirming `bifrost watch` is running) | Watch syncs them automatically |
| `bifrost watch`, `bifrost sync`, `bifrost push`, `bifrost pull`, `bifrost git push` | **User** | Interactive TUI, broad blast radius, controls deployment cadence |
| `bifrost requirements install <pkg>` (ONLY on a workflow that triggered `ModuleNotFoundError`) | **Agent** | Recycles workers — see "Python workflow dependencies" |

When in doubt, the agent does the small entity mutation; the user does anything that watches files, deploys, or recycles infrastructure unsolicited.

## First: Check Prerequisites

```bash
echo "SDK: $BIFROST_SDK_INSTALLED | Login: $BIFROST_LOGGED_IN | MCP: $BIFROST_MCP_CONFIGURED"
echo "Source: $BIFROST_HAS_SOURCE | Path: $BIFROST_SOURCE_PATH | URL: $BIFROST_DEV_URL"
```

**If SDK or Login is false/empty:** Direct user to run `/bifrost:setup` first.

**If `BIFROST_DEV_URL` is empty:** Don't guess the URL. Ask the user: "I don't see `BIFROST_DEV_URL` set — what URL should I use for previews and platform links?" Then use that. Never invent a `*.gobifrost.com` / `*.musick.gg` / etc. host on your own; the user has been burned by this before.

## Step 1: Download Platform Docs (Once Per Session)

All platform reference (SDK, forms, agents, apps, tables, manifest YAML formats) is in a single document. Fetch it once and grep locally.

**SDK-First:**
```bash
mkdir -p /tmp/bifrost-docs
bifrost api GET /api/llms.txt > /tmp/bifrost-docs/llms.txt
```

**MCP-Only:** Call `get_docs` tool, save the result to `/tmp/bifrost-docs/llms.txt`.

Then use `Grep/Read` on `/tmp/bifrost-docs/llms.txt` whenever you need reference.

## Step 2: Detect Development Mode

**Auto-detect:** If a `.bifrost/` directory exists in the workspace, use **SDK-First**. Otherwise, **MCP-Only**. Only ask the user if ambiguous.

## SDK-First Mode

### Principles

- **Discovery goes through the platform.** Use `bifrost <entity> list` and `bifrost <entity> get <ref>` (or the equivalent MCP tools) to discover what exists. The platform's database is the source of truth for entity identity, not local files.
- **`.bifrost/*.yaml` is export-only.** It is what `bifrost sync` / `bifrost export` produce as a versioning / portability artifact. Never read it for discovery and never edit it to mutate entities. Per-UUID `.form.yaml` / `.agent.yaml` files do not exist — form and agent content lives inline in `.bifrost/forms.yaml` and `.bifrost/agents.yaml` keyed by UUID, regenerated from the DB.
- **Write code files locally; mutate entities through the CLI.** Workflow `.py` and app `.tsx` source belongs in the workspace and is synced by `bifrost watch`. Entity records (forms, agents, configs, tables, etc.) are mutated via `bifrost <entity> create | update | delete` — the server assigns the UUID on create.

### Before Building

1. **Which organization?** Ask the user which organization they're building for (natural language — don't dump a list of UUIDs). Confirm the org name, then resolve the UUID with `bifrost orgs get <name> --json` (or `bifrost orgs list --json` and pick from the list).
2. **Who should have access?** Confirm the access tuple `(organization, access_level, role_ids)` *before* you scaffold anything, and apply it consistently across the app and every workflow / form it depends on. Mismatched access is the most common reason a working app silently 403s for end users. Compatibility rule of thumb when an app is assigned to roles X (under Org A or global): every supporting workflow and form must satisfy at least one of —
   - **Global** scope (any org, available to all roles), OR
   - **Org A scoped + `access_level=authenticated`** (any logged-in user in Org A), OR
   - **Same role(s) X** assigned to it.

   Example: a Finance app for Org A, role-restricted to `finance`. Every workflow it calls must be either global, Org-A-scoped+authenticated, or role-assigned to `finance` — otherwise users with the `finance` role can't execute the workflows the app depends on. Discover roles with `bifrost roles list --json`. Pass the tuple through every subsequent create/register call so it doesn't drift:

   ```bash
   # Apps + forms support these flags directly on create/update.
   bifrost apps create --name "Finance" --slug finance --organization "Org A" \
     --access-level role_based --role-ids finance
   bifrost forms create --name "..." --workflow ... --organization "Org A" \
     --access-level role_based --role-ids finance

   # Workflows: --access-level + --role-ids on register, or update later
   # via `bifrost workflows update <ref> --role-ids ...` (bulk replace).
   bifrost workflows register --path workflows/foo.py --function-name foo \
     --org "Org A" --access-level role_based --role-ids finance
   ```
3. **What triggers this?** (webhook, form, schedule, manual)
4. **If webhook:** Get sample payload from user
5. **What integrations?** `bifrost integrations list --json` — drill into specific ones with `bifrost integrations get <ref> --json`.
6. **If migrating from Rewst:** Use `/rewst-migration` skill
7. **If building something new** (new integration, workflow, app, or shared module — not modifying existing):
   > "It sounds like we're building something new. Would you like me to clone the bifrost-workspace-community repo? It has working examples from the community and might already have what you need."

   If the user agrees:
   ```bash
   if [ -d /tmp/bifrost-community ]; then
     git -C /tmp/bifrost-community pull
   else
     git clone https://github.com/jackmusick/bifrost-workspace-community.git /tmp/bifrost-community
   fi
   ```

   Then use Glob/Grep/Read on `/tmp/bifrost-community` to find relevant examples. Surface what you find and let the user decide:
   - **Reference only** — use as inspiration, write fresh code in their workspace
   - **Port and adapt** — copy relevant files into the workspace and adapt (UUIDs, org references, integration mappings, etc.)

   Only ask once per session. If the user declines, don't ask again.

#### Organization Context for CLI Commands

- **`bifrost run`**: Use `--org <UUID>` to execute in that org's context
- **`bifrost watch`/`sync`**: Files sync based on manifest bindings, not CLI flags — but mutating entity records (forms/agents/etc.) goes through `bifrost <entity> create | update`, which take org refs explicitly
- **`bifrost api`**: Authenticated API client for inspecting platform state (executions, etc.) when no dedicated command exists

### Syncing and Deployment — NEVER Run Without Being Asked

**NEVER run `bifrost watch`, `bifrost sync`, `bifrost push`, or `bifrost git push` unless the user explicitly asks you to.** These commands sync local files to the live platform and can trigger interactive prompts, conflict resolution, or unintended deployments. The agent's job is to write files locally — the user controls when and how they are synced.

Before any build work, check if `bifrost watch` is already running:

```bash
pgrep -f 'bifrost watch' > /dev/null 2>&1 && echo "RUNNING" || echo "NOT RUNNING"
```

If not running, tell the user: "Please run `bifrost watch` in a terminal to start syncing." Wait for confirmation before writing files. Once watch is running, the agent writes files locally and watch auto-pushes them.

### Discovery: Query the Platform

| To find... | Command |
|---|---|
| Workflows / tools / data providers | `bifrost workflows list --json` (`bifrost workflows get <ref> --json` for one) |
| Forms | `bifrost forms list --json` / `bifrost forms get <ref> --json` |
| Agents | `bifrost agents list --json` / `bifrost agents get <ref> --json` |
| Apps | `bifrost apps list --json` / `bifrost apps get <ref> --json` |
| Organizations | `bifrost orgs list --json` / `bifrost orgs get <ref> --json` |
| Roles | `bifrost roles list --json` / `bifrost roles get <ref> --json` |
| Integrations | `bifrost integrations list --json` / `bifrost integrations get <ref> --json` |
| Tables | `bifrost tables list --json` / `bifrost tables get <ref> --json` |
| Configs | `bifrost configs list --json` / `bifrost configs get <ref> --json` |
| Event sources | `bifrost events list-sources --json` / `bifrost events get-source <ref> --json` |
| Event subscriptions | `bifrost events list-subscriptions <source-ref> --json` |

For anything without a dedicated command, fall back to `bifrost api GET <path>` — the authenticated REST passthrough. **Do not read `.bifrost/*.yaml` for discovery — it is an export artifact, not the source of truth.**

For YAML field formats and DTO schemas, grep `/tmp/bifrost-docs/llms.txt` for `WorkflowUpdateRequest`, `FormCreate`, etc. Every CLI verb's full flag list is also available via `bifrost <entity> <verb> --help` (generated from the DTOs so it is always in sync).

### Creation Flow

1. **Pick the right surface for what you're creating:**
   - **Code entities** (workflow `.py`, app `.tsx`): write the file in the workspace; let `bifrost watch` sync it; then for workflows run `bifrost workflows register --path workflows/foo.py --function-name foo` to index the decorated function (the server assigns the UUID and returns it).
   - **Renaming or moving a workflow:** Do NOT re-register after a rename/move — that mints a new UUID and breaks form/agent references. Instead: write the new file, then `bifrost workflows replace <old-uuid> --path <new-path> --function-name <new-func>`. Find the orphaned UUID first with `bifrost workflows list-orphaned --json` if needed.
   - **Content entities** (forms, agents, integrations, tables, configs, event sources, roles, orgs): use the entity's `create` command — `bifrost <entity> create --name foo ...`. The server assigns the UUID and returns it. File-loaded fields (form schemas, agent system prompts, etc.) accept `@path/to/file` syntax (e.g. `--system-prompt @prompt.md`, `--form-schema @schema.yaml`, `--config-schema @schema.yaml`).
2. **Capture the returned UUID** from the create command's JSON output if anything else needs to reference it (forms reference workflows, agents reference tools, etc.). Cross-references can also be portable refs (`name`, `path::func`, slug) — the CLI resolves them at submit time.
3. **Verify by re-querying:** `bifrost <entity> get <uuid>` (or by name) confirms the platform sees what you intended.
4. **For discovery of what already exists:** `bifrost <entity> list` / `bifrost <entity> get <ref>` — never read `.bifrost/*.yaml`.

### MCP Tool Naming Convention (CRITICAL for Discoverability)

When workflows are exposed as MCP tools (via agents), their `name` field becomes the MCP tool name and `description` becomes the MCP tool description. Claude.ai uses a deferred tool search system where tools compete on relevance ranking across ALL connected MCP servers. Generic names like `list_findings` or `review_runs` get buried by other servers' tools.

**Tool name format:** `{context}_{action}` — prefix every tool name with a distinctive context word from the agent/feature domain.

**Tool description format:** Must include the agent/feature name and enough distinctive vocabulary to win search ranking. Lead with what it does, include the domain context.

Example — Agent Tuning tools:

| Bad name | Good name | Bad description | Good description |
|----------|-----------|-----------------|------------------|
| `list_findings` | `list_agent_tuning_findings` | "List findings with filtering" | "List agent tuning findings from Bifrost AI agent run reviews with filtering and pagination" |
| `review_agent_runs` | `review_agent_tuning_runs` | "Review an agent's recent runs" | "Review a Bifrost AI agent's recent conversation runs and create tuning findings for prompt issues" |
| `dry_run_prompt` | `dry_run_agent_tuning_prompt` | "Test a candidate prompt" | "Generate a candidate prompt from confirmed agent tuning findings and dry-run test it against historical runs" |

**Rules:**
1. Every tool name MUST contain a context prefix that identifies its agent/feature (e.g. `agent_tuning_`, `halopsa_`, `documentation_`)
2. The `description` field MUST mention the agent or feature name — this is what `tool_search` ranks on
3. Descriptions should be self-contained — someone seeing ONLY the description (no server name) should know what domain this tool belongs to
4. Follow the convention used by professional MCP servers: `microsoft_docs_search`, `outlook_email_search`, `execute_halopsa_sql`

### CLI Commands Reference

| Command | Purpose |
|---------|---------|
| `bifrost watch` | Primary dev command — starts interactive watch session, syncs file changes on save |
| `bifrost sync` | One-shot bidirectional sync — **interactive TUI, user must run manually** |
| `bifrost run <file> -w <name> --org <UUID>` | Execute workflow from a local `.py` file (no sync required). Best for active iteration. |
| `bifrost workflows execute <ref> --params '{...}'` | Execute a registered workflow remotely; streams logs via WebSocket; exits on terminal status. |
| `bifrost requirements install [pkg[==ver]] \| list \| remove <pkg>` | Manage workspace `requirements.txt` for Python workflow deps. Workers recycle async after install/remove. |
| `bifrost api <METHOD> <path>` | Bifrost platform API client ONLY — inspect executions, validate apps, check platform state. NOT for third-party APIs. |
| `bifrost push` | One-shot upload — **interactive TUI, user must run manually** |
| `bifrost pull` | One-shot download — **interactive TUI, user must run manually** |
| `bifrost <entity> list \| get \| create \| update \| delete` | Discover and mutate entity records (orgs, roles, workflows, forms, agents, apps, integrations, tables, configs, events). Server assigns the UUID on create. Flag list per verb: `bifrost <entity> <verb> --help` (generated from the DTO, always in sync). |
| `bifrost workflows register --path workflows/foo.py --function-name foo` | Register a decorated workflow function from a `.py` file that `bifrost watch` has already synced. |
| `bifrost workflows list-orphaned` | List workflows whose source file was deleted or function renamed without using `replace`. These are invisible to `bifrost workflows list`. |
| `bifrost workflows replace <uuid> --path <new-path> --function-name <new-func>` | Repoint an orphaned workflow UUID to a new file location. UUID is preserved — form/agent/app references stay intact. File must exist and contain the named decorated function. |
| `bifrost migrate-imports` | Rewrite `import from "bifrost"` statements to use `lucide-react` / `react-router-dom` / relative paths. **Always review the diff** before applying — the classifier uses regex, not AST, so a local binding that shadows a platform name can be misclassified. |

### Platform Operations

| Need | Command |
|------|---------|
| Run a workflow (local file, fastest iteration) | `bifrost run <file> -w <name> --org <UUID> --params '{...}'` |
| Run a registered workflow remotely (streams logs as it runs, exits on terminal status) | `bifrost workflows execute <ref> --params '{...}' [--org <ref>]` |
| Check execution logs (one-shot) | `bifrost api GET /api/executions/{id}` |
| List executions | `bifrost api GET /api/executions` |
| List workflows / discover by id | `bifrost workflows list --json` / `bifrost workflows get <ref> --json` |
| Validate an app | `bifrost api POST /api/applications/{id}/validate` |
| Download platform docs | `bifrost api GET /api/llms.txt > /tmp/bifrost-docs/llms.txt` |

**`bifrost run` vs `bifrost workflows execute` — pick the right one:**

- **`bifrost run <file> -w <name>`** — executes a `.py` file from the local workspace against the running platform. No sync required, no platform-side registration required. This is the default for iterating on a workflow you're actively editing.
- **`bifrost workflows execute <ref>`** — executes an *already-registered* workflow on the platform by UUID/name/`path::func`. Use this when the workflow is already deployed and you want to test it as it would run in production (real org context, real triggers, real workers). Streams logs over WebSocket as the workflow runs.

Do not use `bifrost api POST /api/workflows/execute` directly — it returns immediately without logs and forces a manual `GET /api/executions/{id}` follow-up. Use `bifrost workflows execute` instead.

### Python Workflow Dependencies

Workflow `.py` files run on platform workers, which install Python packages from a single workspace-wide `requirements.txt`. App npm dependencies (`bifrost apps update --deps`) are unrelated — those are browser-side packages bundled into apps, not Python imports.

If a workflow imports a third-party Python package, that package must be in `requirements.txt` and the workers must have recycled to pick it up. Symptom of a missing dep: `ModuleNotFoundError: No module named 'reportlab'` (or whichever package).

**Add a dependency:**

```bash
bifrost requirements install reportlab           # append, then recycle workers
bifrost requirements install httpx==0.27.0       # pin version, then recycle
bifrost requirements install                     # no arg = warm cache + recycle (after manual edit)
bifrost requirements list                        # what's installed
bifrost requirements remove reportlab            # drop from requirements.txt + recycle
```

`bifrost requirements install` returns immediately. Worker recycle happens asynchronously — give it a few seconds before re-running the workflow.

**Stdlib and a small set of pre-installed packages** (httpx, pydantic, pyjwt, anthropic, openai, etc. — anything bundled into the worker image) are always available. Don't try to install those.

### `bifrost api` Boundaries (CRITICAL)

`bifrost api` is ONLY for the Bifrost platform API. It does NOT proxy to third-party integration APIs. Prefer the dedicated `bifrost <entity> ...` verbs whenever one exists; `bifrost api` is the escape hatch for endpoints not yet wrapped by a verb.

- **Valid**: `/api/executions/{id}`, `/api/workflows`, `/api/applications/{id}/validate`, `/api/llms.txt`
- **Invalid**: `/Client/237` (HaloPSA), `/companies` (Pax8), or any non-`/api/` path

**If you don't know whether an endpoint exists, check first:**
1. Download the docs if not already cached: `bifrost api GET /api/llms.txt > /tmp/bifrost-docs/llms.txt`
2. Grep for the endpoint: `grep -i "endpoint_name" /tmp/bifrost-docs/llms.txt`
3. If it's not documented, it doesn't exist — do NOT guess URL patterns

**To call a third-party integration API** (HaloPSA, Pax8, NinjaOne, etc.):
- Write a small test workflow using the SDK module and run it with `bifrost run`
- Never try to route integration API calls through `bifrost api`

### MCP Tools (creation and events only)

| Need | Tool |
|------|------|
| Create a form | `create_form` |
| Create an app | `create_app` |
| Create an agent | `create_agent` |
| Event triggers | `create_event_source`, `create_event_subscription` |
| RAG search | `search_knowledge` |
| Validate an app | `validate_app` or `bifrost api POST /api/applications/{id}/validate` |
| App dependencies | `get_app_dependencies`, `update_app_dependencies` |

### Syncing

**`bifrost watch` handles file syncing.** Entity records (forms, agents, etc.) are NOT synced via watch — they are mutated through `bifrost <entity> create | update | delete`. The agent NEVER runs sync commands (`bifrost sync` / `push` / `pull`) directly.

#### NEVER run these commands from the agent (CRITICAL)

`bifrost sync`, `bifrost push`, and `bifrost pull` all launch an **interactive TUI** for conflict resolution. They will hang or fail when run non-interactively. The agent must NEVER execute these commands.

#### Sync rules

1. **Before writing any code files**, verify `bifrost watch` is running:
   ```bash
   pgrep -f 'bifrost watch' > /dev/null 2>&1 && echo "RUNNING" || echo "NOT RUNNING"
   ```
2. **If watch is running**: Write code files locally. Watch syncs file changes. For new entity records, run `bifrost <entity> create ...` — do NOT add `.bifrost/*.yaml` entries by hand.
3. **If watch is NOT running**: Tell the user: "Please run `bifrost watch` in a terminal first." **Do NOT write files until the user confirms watch is running.**
4. **If the user asks to sync manually** (push/pull/sync): Tell them to run the command themselves in their terminal since it requires interactive TUI input.
5. **`bifrost git push`** is for git-integrated deployments. Only mention it when the user explicitly asks about git deployment — and they must run it themselves.

#### What about deploying without watch?

If the user doesn't want to run watch and asks to deploy, tell them to run `bifrost sync` or `bifrost push` in their own terminal. The agent cannot do this for them.

Preflight (runs automatically in watch): manifest YAML, file existence, Python syntax, ruff linting, UUID cross-references, orphan detection.

### Git Source Control

When the user needs to deploy via git (not watch mode), use the `bifrost git` subcommands:

```bash
bifrost git fetch                     # regenerate manifest from DB, fetch remote
bifrost git status                    # show changed files, ahead/behind
bifrost git commit -m "description"   # regenerate manifest, stage, preflight, commit
bifrost git push                      # pull + push + import entities (deploy)
git pull                              # pull platform commits into local repo
bifrost git resolve path=keep_remote  # resolve merge conflicts
bifrost git diff <path>               # show file diff
bifrost git discard <path>            # discard working tree changes
```

Typical workflow: `bifrost git fetch` -> `bifrost git commit -m "msg"` -> `bifrost git push` -> `git pull` (to get the platform's commits locally).

## MCP-Only Mode

Best for: quick iterations, non-developers, no local git repo.

1. Call `get_docs` to get platform reference
2. Use `list_workflows`, `list_integrations`, etc. for discovery
3. Write via `replace_content`, register with `register_workflow`. For forms/apps: `create_form`, `create_app`.
4. Test via `execute_workflow` or preview URL
5. Check logs via `get_execution`
6. Iterate with `patch_content` / `replace_content`

Prefer `patch_content` for surgical edits. Use `replace_content` for full file rewrites.

## Building Apps

### Design Workflow

Before writing any app code, design what you're building.

**New app:**
1. Ask: "What should this app feel like? Any products you'd like it inspired by?"
2. If a product is named, **describe the specific visual patterns** that define it — not abstract qualities ("clean", "modern") but concrete observations: "full-height dark sidebar with icon+label nav items, content area with a sticky toolbar row above the main editor, right panel for live preview with a simulated email client frame, generous whitespace between sections, muted borders instead of heavy dividers."
3. Write a visual spec for each key screen: what elements exist, their spatial relationships, which are fixed vs. scrollable, where the visual weight sits, how the eye flows. This is the design — get it right before writing code.
4. Plan `styles.css` for visual identity — color palette via CSS variables (`:root` + `.dark`), typography scale, spacing rhythm. Use Tailwind v4 features freely: `@apply`, `@layer components`, arbitrary values with `var()`, optional per-app `tailwind.config.ts` for custom theme tokens. See [app-patterns.md](app-patterns.md) §10.
5. Decide what's a custom component vs. pre-included shadcn. shadcn is for standard interactions (settings forms, confirmation dialogs, data tables). Custom components are for the interactions that define the app's identity — a project management app needs a custom kanban board, not a `<Table>`; an email tool needs a simulated inbox, not a textarea in a split pane.
6. Then start building.

**Existing app:**
1. Read existing `styles.css` and `components/` first
2. Match established design patterns

### Critical App Rules

1. **Every `<PascalCase>` tag and identifier needs an explicit import.** There is no auto-injection. See [import-patterns.md](import-patterns.md) for which name comes from which source.
2. **Root layout:** `_layout.tsx` uses `<Outlet />` from `"bifrost"` (or `"react-router-dom"`) — NOT `{children}`.
3. **Workflow hooks:** Always use UUIDs, never names — `useWorkflowQuery("uuid-here")`. Resolve UUIDs with `bifrost workflows list --json` or `bifrost workflows get <ref> --json`.
4. **Fixed-height container:** Your app renders in a fixed-height box — manage your own scrolling (see [app-patterns.md](app-patterns.md) "Custom components" for layout patterns).
5. **Styling:** Tailwind v4 with the full feature set — arbitrary values (`bg-[color:var(--x)]`, `lg:grid-cols-[1fr_360px]`), `@apply` and `@layer components` in `styles.css`, optional per-app `tailwind.config.{ts,js,mjs,cjs}` for custom theme tokens. Dark mode via `.dark` selector. See [app-patterns.md](app-patterns.md) §10.
6. **Dependencies:** Declare npm packages with `bifrost apps create --deps`, `bifrost apps update <ref> --deps`, or `bifrost apps set-deps <ref> --deps` (max 20, loaded from esm.sh at runtime — no `package.json` required at runtime, though `--deps @package.json` works for one-shot import).
7. **Default exports:** Every page file MUST have a default export. Components under `components/` may be default or named; the bundler detects which.
8. **Migrating an older app:** run `bifrost migrate-imports` from the workspace root, then **review the diff** before applying. See [import-patterns.md](import-patterns.md) "Migration notes".

### App Resilience Rules (MANDATORY)

These patterns are required for every data-fetching page. Full code examples in [app-patterns.md](app-patterns.md).

- Handle `isLoading` / `isError` on every `useWorkflowQuery`.
- Null-safe access: `data?.items?.map(...)`, never `data.items.map(...)`.
- Every `useWorkflowMutation` must handle errors (toast + stay on page).
- Verify `useEffect` dep arrays — no stale closures.
- Custom components go in `components/<Name>.tsx`, imported relatively.
- Heavy routes: split with `React.lazy(() => import("./pages/heavy"))` + `<Suspense>`.

**Tables:** When you need to read or write structured data from an app, use the `tables.*` SDK + `useTable` hook directly (see [platform-api.md](platform-api.md) → tables). **If you're about to write a workflow just to read/write a table, configure policies and use the SDK directly.** See [app-patterns.md](app-patterns.md) §11 for the CRUD-with-live-updates pattern.

**Drag-and-drop:** Do NOT use `@dnd-kit/*` or `react-beautiful-dnd`. esm.sh's externalization signatures cause the context-providing module to load twice; `useSortable`'s `listeners` come back empty and the drag handle silently never gets an `onPointerDown`. Use native HTML5 drag-and-drop instead — see [app-patterns.md](app-patterns.md) §12 for the handle-armed pattern and reference implementations.

### Platform API Reference

Every name exported by the `"bifrost"` package is listed in [platform-api.md](platform-api.md) with signature and usage example. The canonical list lives in `api/bifrost/platform_names.py` (`PLATFORM_EXPORT_NAMES`) and a drift test enforces docs match the set.

Common lookups:
- **`useWorkflowQuery` / `useWorkflowMutation`** — [platform-api.md](platform-api.md) § Hooks
- **`useUser` / `useAppState` / `RequireRole`** — [platform-api.md](platform-api.md) § Hooks
- **UI primitives (Button, Card, Dialog, Table, etc.)** — [platform-api.md](platform-api.md) § UI Components
- **`toast` / `cn` / `format*`** — [platform-api.md](platform-api.md) § Utilities
- **React Router (Link, useNavigate, etc.)** — [platform-api.md](platform-api.md) § React Router

### App Workflow (SDK-First)

**Order matters.** `bifrost watch` ignores files under `apps/{slug}/` until an app record exists for that slug. Files written before `bifrost apps create` are silently dropped — the user opens the preview and sees the "Welcome / Start building your app" placeholder. Always create the app record FIRST.

1. **Create the app record FIRST**: `bifrost apps create --name "My App" --slug my-app [--deps @package.json]` — the server assigns the UUID and returns it. Do NOT hand-edit `.bifrost/apps.yaml`. The agent runs this; do not ask the user to.
2. Write files in `apps/{slug}/` (only after step 1).
3. `bifrost watch` syncs file changes (triggers esbuild rebuild + validation after each push).
4. Preview at `$BIFROST_DEV_URL/apps/{slug}/preview`
5. Fix any validation errors shown in watch output. esbuild errors appear as a banner in the preview and in the diagnostics channel — the last good bundle keeps serving underneath until the error is fixed.

If you've dispatched a sub-agent to write app files, the parent agent — not the user — runs `bifrost apps create` immediately when the sub-agent returns. Do not defer to the user "after review"; that's the failure mode where 30+ files exist on disk and the preview is empty.

### App Workflow (MCP-Only)

1. `create_app(name="My App")` — scaffolds `_layout.tsx` + `pages/index.tsx`
2. Edit with `patch_content` / `replace_content`
3. Preview at `$BIFROST_DEV_URL/apps/{slug}/preview`
4. Validate with `validate_app(app_id)`

### Post-Build Validation Checklist (REQUIRED)

After writing all app files, verify:

1. `_layout.tsx` exists and uses `<Outlet />`
2. `pages/index.tsx` exists
3. Every npm import matches an entry in the app's declared dependencies (`bifrost apps get <ref> --json | jq .dependencies`; see [import-patterns.md](import-patterns.md) "User npm deps"; pre-included packages exempt).
4. Every `useWorkflowQuery`/`useWorkflowMutation` uses a valid UUID returned by `bifrost workflows get <ref>` or visible in `bifrost workflows list --json` — do NOT grep `.bifrost/workflows.yaml`.
5. Every `<PascalCase />` JSX tag and every referenced identifier has a matching import — no auto-injection. Cross-reference against [import-patterns.md](import-patterns.md):
   - Platform names → `"bifrost"`
   - Icons → `"lucide-react"`
   - Router primitives → `"react-router-dom"` (preferred) or `"bifrost"` (still works)
   - User components → relative (`./components/Name`)
6. Run validation: `bifrost api POST /api/applications/{id}/validate` (or MCP `validate_app`)
7. Review validation output — fix ALL errors before telling user it's ready
8. Open preview URL and verify pages render (or instruct user to check)

## Testing

- **Workflows (local file):** `bifrost run <file> --workflow <name> --org <UUID> --params '{...}'`
- **Workflows (registered, remote):** `bifrost workflows execute <ref> --params '{...}'` — streams logs, exits on terminal status
- **Forms:** `$BIFROST_DEV_URL/forms/{form_id}`
- **Apps:** Preview at `$BIFROST_DEV_URL/apps/{slug}/preview`, publish with `publish_app`, live at `$BIFROST_DEV_URL/apps/{slug}`
- **Webhooks:** `curl -X POST $BIFROST_DEV_URL/api/hooks/{source_id} -H 'Content-Type: application/json' -d '{...}'`
- **Logs:** `bifrost api GET /api/executions/{id}`

## Debugging

1. Check execution logs: `bifrost api GET /api/executions/{id}`
2. Check `bifrost watch` output for sync errors
3. Verify platform state: `bifrost workflows list --json` / `bifrost <entity> get <ref> --json` (only if sync divergence suspected)

### When Errors Suggest System Bugs

**If BIFROST_HAS_SOURCE is true:**
> "This appears to be a backend bug ({error description}). I have access to the Bifrost source code at $BIFROST_SOURCE_PATH. Would you like me to debug and fix this on the backend?"

**If BIFROST_HAS_SOURCE is false:**
> "This appears to be a backend bug ({error description}). Please report this to the platform team with these details: {error details}"

## Session Summary

At end of session, provide:

```markdown
## Session Summary

### Completed
- [What was built/accomplished]

### System Bugs Fixed (if source available)
- [Bug] -> [Fix] -> [File]

### Notes for Future Sessions
- [Relevant context]
```
