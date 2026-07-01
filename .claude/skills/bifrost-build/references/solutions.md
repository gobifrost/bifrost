# Solution Workspace (v2) — Reference

A Solution is an installable, deployable unit. Every entity it owns — apps, workflows, forms, agents, tables, configs — is **deploy-owned**: the platform writes them at install/deploy time and treats them as read-only afterwards. Declared file locations are also part of the Solution definition; their runtime file bytes are user data. Live entity mutation (the entity create/update CLI verbs) returns a 409 because deploy owns those records. You author content in the workspace and ship it with `bifrost solution deploy` (full replace).

> **For a full worked path (including v1→v2 migration and first-time setup), use the `bifrost:migrate` skill.**

---

## Lifecycle

### 1. Create and bind the workspace

```bash
bifrost solution create . --slug my-solution --name "My Solution"
# `bifrost solution init` is an alias for create.
```

Creates `bifrost.solution.yaml` in the current directory, creates an empty remote install, and writes the install binding to `.env`. The hub uses the descriptor as the mode marker — its presence switches all subsequent commands to solution mode. If you cloned an existing workspace instead, run `bifrost solution bind --solution <id-or-slug>` to write the `.env` binding without creating a new install.

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
async def main():
    return {"greeting": "hello"}
```

A workflow takes its inputs as **parameters** (e.g. `async def add_task(title: str, priority: str = "low")`) — there is **no `ctx` parameter**. The SDK is reached through top-level module imports, not a context object: `from bifrost import tables` then `tables.query(...)` / `tables.insert(...)` (see `references/python-sdk.md` and `references/workflows-python.md`). Prefer `async def`.

In a form, agent, or app, reference this as `functions/hello.py::main`. The platform resolves the portable ref at deploy time.

> **A new workflow needs a `.bifrost/workflows.yaml` entry — deploy creates the row from the manifest, not by scanning `functions/`.** Deploy bundles *all* `functions/*.py` source into the install, but it creates a workflow **row** only for functions listed in `.bifrost/workflows.yaml`. The scaffold writes the sample's entry for you (`functions/hello.py::main`), so the sample works on first deploy. For a workflow you add *yourself*, add an entry then deploy — that's the whole flow (no `register`, no `push`, no capture):
>
> 1. Write `functions/tasks.py` (e.g. a `@workflow def my_task(): ...`).
> 2. Add an entry to `.bifrost/workflows.yaml` under `workflows:` — keyed by a fresh UUID, with `id` (same UUID), `name`, `path`, `function_name`. This is the SAME thing the scaffold does for its sample; mirror that block:
>    ```yaml
>    workflows:
>      <fresh-uuid>:
>        id: <fresh-uuid>
>        name: my_task
>        path: functions/tasks.py
>        function_name: my_task
>    ```
> 3. `bifrost solution deploy` → the row is created from the manifest entry and the source ships in the install bundle. Verified: a hand-added entry + deploy reports the extra workflow upserted and it executes.
>
> This is the one place you DO add a `.bifrost/` entry by hand — adding a NEW workflow row is exactly what the scaffold does programmatically, and there is no CLI command for it. (The "don't hand-edit `.bifrost/`" guidance is about not corrupting an EXISTING managed entity's identity/UUID, not about adding a new workflow.) The UUID you write is provisional — a later `bifrost solution pull` rewrites `.bifrost/workflows.yaml` with the server's canonical UUID, which is expected; don't be surprised when your hand-typed id changes. The capture→pull→deploy road below is for adopting a workflow that already exists as a loose `_repo/` entity — overkill for one you're authoring fresh in the solution.

### 4. Local dev

```bash
bifrost solution start
```

Runs the app's Vite dev server and local workflow functions behind one origin — no deploy required. Hot reload works for both app code and workflow code. `start` requires the workspace's `.env` Solution binding, or an explicit `--solution <id-or-slug>` override; install scope comes from that binding, not `--org`.

Open the origin the command prints (the **proxy** port; `--port` sets it, default 3000). Vite itself binds to **`--port + 1`** behind the proxy — drive the app at the proxy port the command prints, not the Vite port.

### 5. Deploy

```bash
bifrost solution deploy                       # bound install from .env
bifrost solution deploy --solution <id>       # explicit install override
```

Full-replace deploy of the workspace — all entities are written (or overwritten) from the workspace content. `deploy` requires a bound install and never creates a missing install. To target another install of the same definition, bind the workspace to it or pass `--solution <install-id-or-slug>`.

### 5a. Declare Solution file locations

If the Solution needs durable runtime files, declare named locations in `.bifrost/files.yaml`:

```yaml
locations:
  - finance
  - documents
```

These names become policy-addressable file locations for the install. They are the portable declaration, not the file content itself. Rules:

- Use business/domain names (`finance`, `documents`, `attachments`), not platform internals.
- Do not declare `workspace`; it is reserved.
- Do not create or manage `_solutions/` or `_solution_artifacts/` folders yourself. Those are internal storage prefixes for deployed source and export artifacts.
- Deploy seeds each declared location with a solution-scoped root `admin_bypass` file policy so platform admins can seed and maintain runtime files. This does not grant ordinary app users access; add explicit file policies for non-admin read/write/list/delete behavior.
- Runtime files are read and written through the Files SDK (`files`, `useFiles`) or the Files CLI/API with `--solution <install-id-or-slug>`. They are not source files under `apps/` or `functions/`.

### 6. Install from a zip

```bash
bifrost solution install my-solution.zip                     # your own org (home)
bifrost solution install my-solution.zip --global            # global install
bifrost solution install my-solution.zip --org "Target Org"  # a specific org
```

Installs a packaged solution (drag-and-drop equivalent). Use `--set KEY=VALUE` to supply config values at install time. Full-backup zips created with encrypted backup content require `--password`, and `--replace-secrets` / `--replace-data` control whether existing install data is overwritten. **`install` with no org flag installs into your own org** (not global) — pass `--global` for a global install.

### 7. Export and backup

```bash
bifrost solution export <solution-ref> --mode shareable
bifrost solution export <solution-ref> --mode full --password "$PASSWORD" --include-data
```

Export modes:

- **Shareable** exports carry code, manifests, schema, declared file locations, and setup requirements. They do not carry secrets, table rows, or runtime file bytes.
- **Full** exports are backups. With `--password`, they can carry secret/config values; with `--include-data`, they also carry table rows and solution runtime files in the encrypted tier.
- File payloads in a full backup are encrypted payload members referenced from `.bifrost/secrets.enc`; do not expect plaintext runtime files as ordinary zip members.

### The unified `--org` standard

Every org-targeting **write** command (`create`/`update`/`set`/`register`, plus install-creating Solution commands such as `solution create`, `solution install`, and `solution pull`) takes the same flag:

- **Omit it** → your own org ("home"). A bare command never writes a global entity by accident.
- **`--org <uuid|name>`** → that org.
- **`--global`** (or `--org none` / `--org global`) → global scope (organization_id NULL).
- **`--organization` and `--scope`** are permanent synonyms for `--org`.

This applies to install-creating or install-selecting Solution commands (`create`, `install`, `pull`) and to the write verbs of the `_repo`-workspace entity commands (`tables`, `forms`, `agents`, `configs`, `claims`, `workflows`, `events`). `solution start` and `solution deploy` do **not** take `--org`/`--global`; they use the bound install's scope. **Read commands (`list`/`get`) do NOT take `--org`/`--global`** — they return the caller's full combined visibility. Install **kind** (org vs global) is stored on the remote install, not in the descriptor.

---

## One definition, many installs

`bifrost.solution.yaml` is the **definition** descriptor — `slug`, `name`, `version`, `global_repo_access`, and git-source fields (`git_connected`, `git_repo_url`, `repo_subpath`, `git_ref`, `logo`). It intentionally carries **no install id** and **no install scope**. The concrete install binding lives in `.env` (`BIFROST_SOLUTION_ID`, slug, org id, scope), which is local environment state and should not be committed. The descriptor also doubles as the workspace mode marker (its presence is what switches tooling into solution mode).

The install id lives **server-side** (`Solution.id` — the `solution_id` stamped on every managed entity *at deploy time*) and in the local uncommitted `.env` binding. The repo is the **definition**; `solution create` or `solution bind` chooses which concrete install this checkout is working against. Deploy/pull then operate on that install (or an explicit `--solution` override).

**Instance vs fork — the `slug` is the dividing line:**

- **One definition, many installs (instances).** Keep **one repo / one slug** and create/install it per customer-org: `bifrost solution create --org "Customer Org"` for an authoring checkout, or `bifrost solution install pkg.zip --org "Customer Org"` for packaged delivery. Each org gets an **independent install** — its own id, config values, and entity rows — from the *same* source. Bind the checkout to the install you are editing before `start` or `deploy`. This is how you run "the same HR portal" for 3 customers: one codebase, three installs.
- **A genuinely different solution → fork (new slug).** If a customer needs the solution to *diverge* (different features/code, not just different data), **fork the repo and give it a new `slug`**. Different slug = different definition = a separate solution, not an instance of the first. There is no per-customer stamping in the repo to do this for you — forking is the mechanism.
- **Multiple repos / subfolders** for git-connected installs are distinguished by `repo_subpath` (omni-repo: one repo, a folder per solution) and `git_ref` (pin a branch/tag).
- **Natural key collision** (two installs of the same slug in the same org) → resolution raises an ambiguity error; pass **`--solution <install-id>`** to target one install by id.

Install **kind** is chosen when the remote install is created or installed via the unified `--org`/`--global` standard — there is no `scope` in the descriptor. `--global` makes a global install (organization_id NULL); `--org "Customer Org"` makes an org install; omitting both installs into your own org. The same descriptor can back global or per-org installs from the same source, but each checkout should be bound to exactly one install while developing.

---

## Getting forms, agents, tables, configs, and files into a Solution

A solution owns these entities the same way it owns apps and workflows: deploy writes them, and they are read-only afterwards. File location declarations are also deploy-owned. There are two ways entity content arrives in the workspace manifest deploy reads.

**Path A — capture an existing entity (the migration road).** This adopts a loose `_repo`/global entity that already exists OUTSIDE the solution (authored earlier in the `_repo` workspace, where live entity create/update is the normal path — see `references/repo.md`). Capture stamps it into the install, then you pull and deploy:

```bash
bifrost solution capture <solution-id> --table <id> --form <id> --agent <id> --config <KEY>
bifrost solution pull --solution <solution-id>  # bring captured entities into source .bifrost/
bifrost solution bind --solution <solution-id>  # if this checkout is not already bound
bifrost solution deploy                         # ship them to the bound install
```

(Use the same concrete install id you captured into. Binding keeps `start` and `deploy` stamped to that install.)

The capture flags are singular and repeatable: `--table`, `--form`, `--agent`, `--config`, `--claim`, `--workflow`, `--app` (each takes a name or id; `--config` takes a key).

> **Ordering for a form/agent that references a workflow:** a form's `workflow_id` (and an agent's tool refs) must resolve to a **registered** workflow UUID — i.e. a workflow that has a row. The scaffold's sample (`functions/hello.py::main`) ships with a `.bifrost/workflows.yaml` entry, so it's registered on the **first `bifrost solution deploy`**. A workflow you write yourself is registered the same way: add its `.bifrost/workflows.yaml` entry (see "Write workflows in `functions/`" above), then `bifrost solution deploy` creates the row. So for a fresh solution the order is: write the workflow → add its manifest entry → **`solution deploy`** (creates the workflow row) → create the form/agent referencing that workflow → capture the form/agent (`--form`/`--agent`) → pull → deploy. (Reference the workflow by portable `path::function` ref; a bare name like `hello` can collide, so prefer the full `functions/hello.py::main` ref or the UUID.)

**Which install to use on `pull`/`deploy`.** Capture, pull, start, and deploy should all target the same concrete install id. So:

- **Bound checkout** → `bifrost solution deploy` uses `.env`.
- **Different install of the same slug** → run `bifrost solution bind --solution <install-id>` first, or pass `--solution <install-id>` for one command.
- **Ambiguous slug** → pass the install id instead of the slug.

The rule: pick the install once, bind the workspace, and let the binding stamp every local data-plane call and deploy.

**Scope and capture — the loose entity must already be in the install's scope.** `bifrost solution capture` only adopts loose entities **already in the install's own scope**: an org install captures that org's entities; a global install captures global (`organization_id: null`) entities. The CLI resolves your `--table/--form/...` selectors against the install's **candidate list** (`/capture/candidates`) and refuses anything outside its scope — including by id — with "not in /capture/candidates for its scope". A concrete org-A entity is likewise never capturable into an org-B install (cross-tenant).

So if the loose entity is in a different scope, **it must be authored in the install's scope first** — which happens in the `_repo` workspace, not here (see `references/repo.md`). There, the unified `--org` standard sets the entity's scope: an org install needs an entity created in that org (`--org <uuid|name>`), and a global install needs a global entity (`--global`). Don't rely on capture to fix scope for you; set it up front.

(The server-side capture service has a latent global→org re-stamp path, but the CLI's candidate gate makes it unreachable today.)

Capture stamps ownership server-side but does **not** write source. `bifrost solution pull` materializes the captured entities into the workspace `.bifrost/*.yaml` manifest (it touches only `.bifrost/`, never your `apps/` or `functions/` source — safe to run any time). Then deploy ships them.

**The deploy guard:** because deploy is full-replace, an entity captured in the UI/CLI but absent from your source manifest would be deleted by the reconcile sweep. To prevent silent loss, **deploy 409-blocks** if a captured-but-unpulled entity is missing from the manifest, naming it and telling you to `bifrost solution pull` first. An entity you previously pulled and then deliberately removed from the manifest is a genuine delete and proceeds. So the rule is simple: **after any capture, run `bifrost solution pull` before `bifrost solution deploy`.**

**Path B — author from scratch.** The `bifrost:migrate` skill scaffolds a complete solution (including its forms/agents/tables) end-to-end; invoke it as a Claude skill (not a CLI command) when starting fresh.

**Files are different from captured entities.** Add file *locations* to `.bifrost/files.yaml`; do not capture individual files into the source manifest. Runtime files are written later by the installed app or workflows. A full backup can carry those file bytes, but normal deploy is intentionally non-destructive for files absent from the bundle.

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
| `useFiles` | Live file listing for a location/prefix |
| `files` | Low-level file API (`read`, `write`, `delete`, `list`, `exists`, signed URLs) + error classes |

There is no React, shadcn, or router injection from the SDK — import those from the standard packages. See `references/web-sdk-v2.md` for full signatures and examples.

---

## Key constraints

- Workflows must use portable `path::function` refs (e.g. `functions/hello.py::main`), not UUIDs or bare names — UUIDs are environment-specific and break portability.
- Solution file locations live in `.bifrost/files.yaml`; runtime file bytes are user data and only travel in encrypted full backups.
- `_solutions/` and `_solution_artifacts/` are platform internals. Never create those folders in a Solution workspace.
- The `bifrost:migrate` skill covers the v1→v2 migration path (slug swap, import rewrite, entity capture, etc.).
- For table schema and query patterns, see `references/tables.md`. For workflow authoring, see `references/workflows-python.md`.
