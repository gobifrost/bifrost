# Global `_repo` Workspace (v1) — Reference

The global `_repo` workspace is the original Bifrost development model. Entities (workflows, forms, agents, apps, tables, configs) are **mutated live** via the CLI entity verbs. File-backed entities (workflow `.py`, app `.tsx`) are synced by the watch daemon after the user starts it. This is also the entry point for **MCP-only mode** — see the pointer at the bottom.

> **Mode contrast:** the live entity create/update CLI verbs below are the CORRECT path *here*. In a **Solution** workspace (a directory with `bifrost.solution.yaml`) the same verbs return **409** — entities there are deploy-owned. If you find yourself in a solution workspace, stop and read `solutions.md` instead.

> For the access-tuple rule (org + access_level + role_ids consistency), see the hub (`../SKILL.md` → "Step 2: Confirm Org + Access Before Scaffolding"). That rule is mode-agnostic — apply it in repo mode too.

---

## File Sync (the watch daemon)

The user runs the watch daemon in their own terminal to sync file changes to the platform. The agent NEVER runs watch, push, pull, sync, or git commands unsolicited — these have broad blast radius or launch interactive TUIs. Tell the user to run the watch command in a separate terminal before you write any code files.

Before writing any code files, check whether the watch daemon is already running:

```bash
pgrep -f 'bifrost watch' > /dev/null 2>&1 && echo "RUNNING" || echo "NOT RUNNING"
```

If not running: tell the user "Please start the watch daemon in your terminal before I write files." Wait for confirmation.

The watch daemon is **workspace-specific** — it syncs the directory it was started in. A `pgrep` hit only means *some* watch is running, NOT that it's watching *this* workspace. If you're in a fresh or different directory, confirm with the user that watch is running **against this directory** (or have them restart it here). A file watch hasn't synced isn't on the platform yet, so the next step (`workflows register`) will 404 with `File not found`.

Once watch is confirmed running **against this workspace**, write files to `workflows/` and `apps/` — watch picks them up automatically and syncs on save.

---

## Creation Flow

### Code entities (workflows and apps)

Write the file into the workspace first; **watch syncs it to the platform** (see "File Sync" above — confirm watch is running against THIS workspace, else the file isn't on the platform and the next step 404s). Then register:

```bash
bifrost workflows register --path workflows/foo.py --function-name foo
```

`register` reads the file from the platform (where watch put it), not from local disk — so "File not found" means watch hasn't synced this workspace yet. The server assigns the UUID and returns it. Capture it — forms, agents, and apps reference workflows by UUID (or portable `path::function` refs).

**Renaming or moving a workflow:** do NOT re-register — that mints a new UUID and breaks all references. Instead, write the new file, then repoint the existing UUID:

```bash
bifrost workflows list-orphaned --json
bifrost workflows replace <old-uuid> --path workflows/new_path.py --function-name new_func
```

`list-orphaned` finds workflows whose source file was deleted or function renamed without using `replace`. Their UUID is preserved and all form/agent/app references remain intact.

### Content entities (forms, agents, tables, configs, integrations, events)

Use the entity's create command — the server assigns the UUID:

```bash
bifrost forms create --name "Submit Invoice" --workflow <uuid> \
  --form-schema @schema.yaml \
  --org "Org A" --access-level role_based --role-ids finance

bifrost agents create --name "Support Bot" --system-prompt @prompt.md \
  --organization "Org A" --access-level authenticated

bifrost tables create --name "leads" --schema @schema.json

bifrost configs create --key "api_key" --value "..." --organization "Org A"
```

File-loaded fields accept `@path/to/file` syntax: `--system-prompt @prompt.md`, `--form-schema @schema.yaml`, `--config-schema @schema.yaml`, etc. The server reads the file content at submit time.

Verify after creation:

```bash
bifrost forms get <uuid-or-name> --json
```

Do NOT add entries to `.bifrost/*.yaml` by hand — that file is an export artifact, not the source of truth.

### Apps

Create the app record BEFORE writing files. Watch ignores files under `apps/{slug}/` until the app record exists:

```bash
bifrost apps create --name "Finance Dashboard" --slug finance-dashboard \
  --organization "Org A" --access-level role_based --role-ids finance
```

Then write files into `apps/finance-dashboard/`. Watch syncs and triggers esbuild rebuild + validation after each push.

---

## Discovery

Never read `.bifrost/*.yaml` for discovery — it is an export artifact. Use entity list/get commands:

```bash
bifrost workflows list --json
bifrost workflows get <ref> --json

bifrost forms list --json
bifrost forms get <ref> --json

bifrost agents list --json
bifrost agents get <ref> --json

bifrost apps list --json
bifrost apps get <ref> --json

bifrost tables list --json
bifrost tables get <ref> --json

bifrost configs list --json
bifrost configs get <ref> --json

bifrost orgs list --json
bifrost orgs get <ref> --json

bifrost roles list --json

bifrost integrations list --json
bifrost integrations get <ref> --json

bifrost events list-sources --json
bifrost events list-subscriptions <source-ref> --json
```

For anything without a dedicated command, fall back to the authenticated REST passthrough:

```bash
bifrost api GET /api/executions/{id}
bifrost api GET /api/executions
bifrost api POST /api/applications/{id}/validate
```

`bifrost api` is the Bifrost platform API only — NOT for third-party integration APIs (HaloPSA, Pax8, NinjaOne, etc.). Call those from within a workflow using the SDK.

---

## Execution

Two paths — pick the right one:

```bash
# Execute a local .py file (no sync or registration required — fastest iteration)
bifrost run workflows/foo.py -w foo --org <uuid> --params '{"key": "val"}'

# Execute an already-registered workflow remotely (streams logs, exits on terminal status)
bifrost workflows execute <ref> --params '{"key": "val"}' --org <ref>
```

- **`bifrost run`** — local file, against the running platform, no registration required. Use this while actively editing a workflow.
- **`bifrost workflows execute`** — registered workflow, real org context, real workers. Use this to test as production would run.

Do not use `bifrost api POST /api/workflows/execute` directly — it returns immediately without log streaming.

---

## Python Workflow Dependencies

Workflow `.py` files run on platform workers, which install Python packages from a workspace-wide `requirements.txt`. If a workflow imports a third-party package, it must be in requirements and workers must have recycled to pick it up.

```bash
bifrost requirements install reportlab
bifrost requirements install httpx==0.27.0
bifrost requirements list
bifrost requirements remove reportlab
```

`bifrost requirements install` returns immediately; worker recycle is async — allow a few seconds before re-running.

---

## MCP Tool Naming

When a workflow is exposed as an MCP tool (via an agent), its `name` field becomes the MCP tool name and `description` the tool description. Use `{context}_{action}` format with a distinctive domain prefix so tools rank well in deferred tool search across multiple MCP servers. See the hub (`SKILL.md` → "MCP Tool Naming Convention") for the full rules and examples.

---

## Updating and Deleting

```bash
bifrost workflows update <ref> --access-level authenticated
bifrost forms update <ref> --name "New Name"
bifrost agents update <ref> --system-prompt @updated-prompt.md
bifrost apps update <ref> --deps "recharts@2,date-fns@3"
bifrost tables update <ref> --name "new-name"
bifrost configs update <ref> --value "new-value"
```

For apps, also set npm dependencies separately:

```bash
bifrost apps set-deps <ref> --deps "recharts@2,date-fns@3"
```

App dependencies are browser-side packages bundled via esm.sh. Worker Python deps are entirely separate (`bifrost requirements`).

---

## Git Source Control (user-driven)

When the user needs to deploy via git rather than watch, they run the git subcommands themselves. The agent describes these and asks the user to run them — they are NOT agent-executed. Typical flow: generate manifest + stage + commit, then pull + push + import entities. The git subcommands handle regenerating the manifest and running preflight checks automatically before commit.

---

## MCP-Only Mode

No local source, no watch daemon. See `references/mcp-mode.md` for the full flow (discovery via MCP tools, editing via `replace_content` / `patch_content`, registration via `register_workflow`, creation via `create_form` / `create_app` / `create_agent`).
