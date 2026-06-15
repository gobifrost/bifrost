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

Scaffolds a `standalone_v2` React app under `apps/my-app/`. Config files sit at the app root (`package.json`, `vite.config.ts`, `tsconfig.json`, `components.json`, `index.html`); **source files live under `apps/my-app/src/`** (`main.tsx`, `App.tsx`, `index.css`, `lib/utils.ts`). The `bifrost` SDK is resolved from the running instance (not npm), so no `npm install bifrost` is needed.

> The scaffold wires up Tailwind (`@tailwindcss/vite` + shadcn theme tokens in `src/index.css`) but the generated `App.tsx` uses minimal **inline styles** (`style={{ padding: 24 }}`) as a plain starting point. Replace them with Tailwind classes (`className="p-6 ..."`) before building — the infrastructure is ready. See `references/apps.md` for v2 styling patterns.

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

## One definition, many installs

`bifrost.solution.yaml` is the **definition** descriptor — `slug`, `name`, `scope`, `version`, `global_repo_access`, and git-source fields (`git_connected`, `git_repo_url`, `repo_subpath`, `git_ref`, `logo`). It is **stateless** and intentionally carries **no install id**. It also doubles as the workspace mode marker (its presence is what switches tooling into solution mode).

The install id lives **server-side** (`Solution.id` — the `solution_id` stamped on every managed entity *at deploy time*). **Nothing in the repo carries install identity** — not the descriptor, not `.bifrost/*.yaml` (its entries deliberately omit org/access/identity for portability). The repo is the **definition**; deploy is what mints an install and stamps identity onto the entity rows it writes. Deploy/pull resolve *which install* at runtime by matching **`(slug, scope, org)`** against the server's installs, creating one if none matches.

**Instance vs fork — the `slug` is the dividing line:**

- **One definition, many installs (instances).** Keep **one repo / one slug** and deploy it per customer-org: `bifrost solution deploy --org "Customer Org"` (or `bifrost solution install pkg.zip --org "Customer Org"`). Each org gets an **independent install** — its own id, config values, and entity rows — from the *same* source. Deploy refuses to let one org clobber another org's install of the same slug. This is how you run "the same HR portal" for 3 customers: one codebase, three installs.
- **A genuinely different solution → fork (new slug).** If a customer needs the solution to *diverge* (different features/code, not just different data), **fork the repo and give it a new `slug`**. Different slug = different definition = a separate solution, not an instance of the first. There is no per-customer stamping in the repo to do this for you — forking is the mechanism.
- **Multiple repos / subfolders** for git-connected installs are distinguished by `repo_subpath` (omni-repo: one repo, a folder per solution) and `git_ref` (pin a branch/tag).
- **Natural key collision** (two installs of the same slug+scope+org) → resolution raises an ambiguity error; pass **`--solution <install-id>`** to target one install by id.

`scope` in the descriptor only selects the **kind** of install at create time (`global` → org-NULL, ignoring `--org`; `org` → requires an org); the **which-org** coordinate is the deploy-time `--org`. (On export the server even recomputes `scope` from the install's org, so treat the descriptor's `scope` as the default for a fresh deploy, not a stored truth.)

---

## Getting forms, agents, tables, and configs into a Solution

A solution owns these entities the same way it owns apps and workflows: deploy writes them, and they are read-only afterwards. There are two ways content arrives in the workspace manifest deploy reads.

**Path A — capture an existing entity (the migration road).** This adopts a loose `_repo`/global entity that already exists OUTSIDE the solution (authored earlier in the `_repo` workspace, where live entity create/update is the normal path — see `references/repo.md`). Capture stamps it into the install, then you pull and deploy:

```bash
bifrost solution capture <solution-id> --table <id> --form <id> --agent <id> --config <KEY>
bifrost solution pull --org "Target Org"     # bring captured entities into source .bifrost/
bifrost solution deploy --org "Target Org"   # ship them
```

The capture flags are singular and repeatable: `--table`, `--form`, `--agent`, `--config`, `--claim`, `--workflow`, `--app` (each takes a name or id; `--config` takes a key).

> **Ordering for a form/agent that references a workflow:** a form's `workflow_id` (and an agent's tool refs) must resolve to a **registered** workflow UUID, and a `functions/*.py` workflow in a brand-new solution isn't registered until its **first `bifrost solution deploy`**. So for a fresh solution, the order is: write the workflow in `functions/` → **deploy once** (registers it, gives it a UUID) → create the form/agent referencing that workflow → capture → pull → deploy again. (If you reference the workflow by portable `path::function` ref, it must be unambiguous — a bare name like `hello` can collide with other workflows; prefer the full `functions/hello.py::main` ref or the UUID.)

**When `--org` is needed (and when it isn't).** `pull` and `deploy` resolve *which install* by `(slug, scope, org)`. The descriptor fixes `slug` and `scope`; the org defaults to **your own** org. So:

- **Global install** (`scope: global`) or **install in your own org** → `--org` is **not needed**; resolution is unambiguous.
- **Install in a *different* org** (you deployed it with `--org "Target Org"`) → you must pass the **same `--org`** to `pull` and `deploy`, because resolution only looks in your own org by default. Without it, `pull`/`deploy` won't find that install (and may resolve a stale same-slug install in your default org instead), so a deploy can keep 409-blocking even after you "pulled".

`--org` is really about **targeting**: pick it once (commonly when first deploying a fresh install into a customer's org) and use the same value for that install's `pull`/`deploy`. To skip org resolution entirely, pass **`--solution <install-id>`** to target an install by id.

**Scope and capture — author the entity in the install's scope first.** `bifrost solution capture` only adopts loose entities **already in the install's own scope**: an org-scoped install captures that org's entities; a global install captures global (`organization_id: null`) entities. The CLI resolves your `--table/--form/...` selectors against the install's **candidate list** (`/capture/candidates`) and refuses anything outside its scope — including by id — with "not in /capture/candidates for its scope". A concrete org-A entity is likewise never capturable into an org-B install (cross-tenant).

So the rule is simply: **if the entity is in a different scope, change its scope before capturing** — give it the install's org when you author it:

- **Org-scoped install:** create the entity with `--organization <uuid>` matching the install's org (`bifrost orgs list` to find it).
- **Global install:** leave the entity global (no `--organization`).

(The server-side capture service has a latent global→org re-stamp path, but the CLI's candidate gate makes it unreachable today — so don't rely on capture to fix scope for you; set it up front.)

Capture stamps ownership server-side but does **not** write source. `bifrost solution pull` materializes the captured entities into the workspace `.bifrost/*.yaml` manifest (it touches only `.bifrost/`, never your `apps/` or `functions/` source — safe to run any time). Then deploy ships them.

**The deploy guard:** because deploy is full-replace, an entity captured in the UI/CLI but absent from your source manifest would be deleted by the reconcile sweep. To prevent silent loss, **deploy 409-blocks** if a captured-but-unpulled entity is missing from the manifest, naming it and telling you to `bifrost solution pull` first. An entity you previously pulled and then deliberately removed from the manifest is a genuine delete and proceeds. So the rule is simple: **after any capture, run `bifrost solution pull` before `bifrost solution deploy`.**

**Path B — author from scratch.** The `bifrost:migrate` skill scaffolds a complete solution (including its forms/agents/tables) end-to-end; invoke it as a Claude skill (not a CLI command) when starting fresh.

### Updating an already-owned entity

Once an entity is solution-managed, the live entity update verbs **409** (deploy owns it). The update path is to **edit its field in the corresponding `.bifrost/*.yaml` and redeploy**:

```bash
# e.g. change an agent's prompt:
$EDITOR .bifrost/agents.yaml      # edit the system_prompt under that agent's UUID
bifrost solution deploy           # redeploys the changed content
```

This is the intended, correct update surface — `.bifrost/*.yaml` is generated by `capture` + `pull` on first adoption, but **its content fields are yours to edit thereafter**. The one thing you must NOT do by hand is add or remove entity **UUID keys** (that changes entity identity and trips the deploy guard / reconcile sweep) — use `capture` + `pull` to introduce a new entity, and a manifest-omission deploy to delete one you previously pulled.

What is settled:
- Live entity create/update commands against a solution-managed record **409** — deploy owns those records; edit `.bifrost/*.yaml` + redeploy instead.
- `.bifrost/*.yaml` is generated by `capture` + `pull` on first adoption; after that, edit entity **content fields** there to update them. Do not hand-add/remove entity **UUID keys** — capture/pull introduces entities, manifest-omission deletes them.

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
