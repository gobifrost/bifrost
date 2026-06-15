# Solution Workspace (v2) — Reference

A Solution is an installable, deployable unit. Every entity it owns — apps, workflows, forms, agents, tables, configs — is **deploy-owned**: the platform writes them at install/deploy time and treats them as read-only afterwards. Live entity mutation (the entity create/update CLI verbs) returns a 409 because deploy owns those records. You author content in the workspace and ship it with `bifrost solution deploy` (full replace).

> **For a full worked path (including v1→v2 migration and first-time setup), use the `bifrost:migrate` skill.**

---

## Lifecycle

### 1. Scaffold the workspace

```bash
bifrost solution init . --slug my-solution --name "My Solution"
```

Creates `bifrost.solution.yaml` in the current directory. The hub uses this file as the mode marker — its presence switches all subsequent commands to solution mode.

### 2. Scaffold a v2 app

```bash
bifrost solution scaffold-app my-app
```

Scaffolds a `standalone_v2` React app under `apps/my-app/` with `package.json`, `vite.config.ts`, `main.tsx`, and `App.tsx`. The `bifrost` SDK is resolved from the running instance (not npm), so no `npm install bifrost` is needed.

To migrate a v1 inline app to standalone_v2, use `bifrost solution migrate-app <source-slug> <v2-slug>` — it ports source + rewrites imports + prints a judgment checklist.

### 3. Write workflows in `functions/`

Python workflows live in `functions/` (e.g. `functions/hello.py`). Reference them by portable `path::function` strings, never by UUID or bare name:

```python
# functions/hello.py
from bifrost import workflow

@workflow
def main(ctx):
    return {"greeting": "hello"}
```

In a form, agent, or app, reference this as `functions/hello.py::main`. The platform resolves the portable ref at deploy time.

### 4. Local dev

```bash
bifrost solution start
```

Runs the app's Vite dev server and local workflow functions behind one origin — no deploy required. Hot reload works for both app code and workflow code. The `--org` flag runs under a specific org context (superuser only).

### 5. Deploy

```bash
bifrost solution deploy
bifrost solution deploy --org "Target Org"
```

Full-replace deploy of the workspace — all entities are written (or overwritten) from the workspace content. The `--org` flag targets a specific org; omit it to deploy to your own org.

### 6. Install from a zip

```bash
bifrost solution install my-solution.zip
bifrost solution install my-solution.zip --org "Target Org"
```

Installs a packaged solution (drag-and-drop equivalent). Use `--set KEY=VALUE` to supply config values at install time.

---

## Entities in a Solution — the open question (TBD)

**Getting forms, agents, tables, and configs into a solution workspace is being pinned down by live validation (Task 11).** Two mechanisms are under consideration:

- **`bifrost solution capture <solution-id>`** — author an entity in a scratch/global context (via CLI or MCP), then capture it into the solution's deploy bundle by ID or name.
- **Deploy-time manifest** — declare entities inline in `bifrost.solution.yaml` so deploy creates them on first install.

What is settled:
- Live entity create/update commands against a solution-managed record **409s** — do not do this.
- Local YAML in the workspace is **not** a mechanism — the manifest is platform-dev plumbing, not a user workspace feature.

Do not assert either candidate mechanism as the one true path until validation confirms it. **Until then, the worked path is the `bifrost:migrate` skill** — a Claude skill you invoke, NOT a `bifrost` CLI command. Use it to scaffold a complete solution instead of guessing the entity mechanism.

---

## The v2 SDK surface

Apps built with `bifrost solution scaffold-app` consume the v2 `bifrost` SDK. Key exports:

| Export | Purpose |
|--------|---------|
| `BifrostProvider` | Root provider — wrap your app |
| `useBifrostContext` | Auth, org, user from context |
| `BifrostHeader` | Pre-built nav header |
| `useWorkflow` / `useWorkflowQuery` / `useWorkflowMutation` | Execute workflows; query-style for data loads, mutation-style for actions |
| `useTable` / `useInfiniteTable` | Direct table read with live updates |
| `tables` | Low-level CRUD (`tables.get`, `tables.insert`, `tables.update`, `tables.delete`) + error classes |

There is no React, shadcn, or router injection from the SDK — import those from the standard packages. See `references/web-sdk-v2.md` for full signatures and examples.

---

## Key constraints

- Workflows must use portable `path::function` refs (e.g. `functions/hello.py::main`), not UUIDs or bare names — UUIDs are environment-specific and break portability.
- The `bifrost:migrate` skill covers the v1→v2 migration path (slug swap, import rewrite, entity capture, etc.).
- For table schema and query patterns, see `references/tables.md`. For workflow authoring, see `references/workflows-python.md`.
