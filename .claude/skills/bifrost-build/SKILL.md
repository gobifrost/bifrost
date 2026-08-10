---
name: build
description: Build or modify Bifrost apps, workflows, forms, agents, tables, managed files, configs, integrations, events, and related platform resources. Use for Solution-owned v2 projects, loose instance _repo/v1 content, local preview and testing, deployment planning, or MCP-only Bifrost work. Covers instance and install targeting, source ownership, access controls, testing, app design and theming, visual QA, and deployment handoff.
---

# Bifrost Build

## Overview

Bifrost is an open-source platform for building apps, workflows, forms, and agents, with foundational features such as managed tables and files. Use this skill to plan, build, test, preview, and deploy work on the Bifrost platform.

Follow the process below for every task. Load only the references routed for the current artifact.

## Prerequisites

Confirm the CLI exists and inspect the connection from the directory where commands will run:

```bash
command -v bifrost
bifrost auth default
```

If the CLI or authentication is not configured, route the user to `bifrost:setup`.

A sandbox may be unable to read the host credential store. If authentication appears absent, repeat the read-only `bifrost auth default` check with host access before asking the user to log in again.

## Required process

### 1. Choose the working model and target

First choose which of three working models owns the change. `Workspace` here means the selected instance's loose `_repo` source and live loose entities; it does not mean global entity scope.

| Working model | Use it when | Authoring and lifecycle | Read next |
|---|---|---|---|
| **Workspace** | Maintaining a v1 app, building a loose workflow/entity, or intentionally creating source/resources shared at the instance level | Edit `_repo` source with `bifrost files`; create and mutate its scoped entity records directly with dedicated CLI commands. Commands target the selected instance directly; v1 app source remains draft until published. | `references/repository.md` |
| **Solution** | Building a cohesive, portable, versioned app or automation package that should own and deploy its definitions together | Edit local source and `.bifrost` manifests; preview with `bifrost solution start`; deliver with `bifrost solution deploy`. Keep `global_repo_access: false`. | `references/solutions.md` |
| **Solution supported by the Workspace** | A Solution intentionally depends on eligible shared `_repo` modules, registered loose workflows, tables, or files | Author and deploy Solution-owned definitions locally, but manage each shared Workspace dependency separately through its own CLI/file path. Set `global_repo_access: true`; this adds runtime fallback, not ownership or permission. | `references/solutions.md` and `references/solution-resource-access.md` |

Use `bifrost.solution.yaml` at the project root as the ownership marker. If it exists, inspect `global_repo_access` to distinguish the second and third models. If it does not exist, use the Workspace model. If starting inside a nested folder, locate the root with ordinary file discovery; do not use a custom shell loop.

For net-new work, confirm the model with the user before scaffolding. Prefer a Solution for a new product or package that should be portable and lifecycle-managed. Use the Workspace for deliberately loose, shared, or existing v1 content. Enable Workspace support for a Solution only when the shared dependencies are intentional and understood, never as a default convenience.

Then establish the exact instance and, for either Solution model, the bound install. Read `.env` in the intended CLI invocation directory, normally the project or Solution root. Parent-directory `.env` files are not selected implicitly. Inspect these fields when present:

- `BIFROST_API_URL`
- `BIFROST_SOLUTION_ID`
- `BIFROST_SOLUTION_SLUG`
- `BIFROST_SOLUTION_ORG_ID`
- `BIFROST_SOLUTION_SCOPE`

Treat `.env` as local instance/install selection, not portable source. Do not commit it. A normal Bifrost project `.env` should not contain access or refresh tokens; report only the selector and binding fields above.

Compare `Current connection` and `Default connection` from `bifrost auth default`:

- If `.env` selects a non-default instance, tell the user which instance and install are selected and offer the saved default before material work.
- Removing `BIFROST_API_URL` from `.env` returns that directory to the saved default connection. A Solution binding belongs to its instance; rebind it when changing instances.
- Never change the user's saved default merely to target one project.

### 2. Understand and plan the change

If this is a Solution, state its name/slug, bound install, instance, and install scope. Then confirm the requested outcome and intended users.

For every material change, present a concise plan and get confirmation before editing. A small, well-bounded bug fix does not need ceremony. The plan must identify:

- user outcome and acceptance criteria;
- source files and platform entities involved;
- organization, access level, roles, and policies;
- integrations, configs, tables, and managed file locations;
- live-instance or deployment effects;
- for apps: information hierarchy, primary interactions, visual direction, theme behavior, responsive behavior, and non-happy-path states.

Keep the access tuple `(organization, access_level, role_ids)` compatible across an app and every workflow, form, or agent it calls, unless a specific entity intentionally requires different access than the rest. Record that exception and verify that every intended caller can still traverse the dependency chain. Runtime access never bypasses organization, role, policy, or external-user checks.

### 3. Build in the correct ownership model

Load the artifact references routed below and follow the working model selected in step 1. Use curated references for the method and generated appendices or `--help` for exact syntax; never infer a familiar command or flag. Do not mix authoring paths: Workspace entities mutate live; Solution-owned definitions change through local source and deploy. If a Solution supported by the Workspace needs changes on both sides, track them as separate change sets with separate ownership, access, verification, and delivery effects.

Use test-driven development for behavior: define the acceptance result, write a focused failing test when useful, implement the change, and make it pass. Do not create contrived tests for visual-only acceptance; verify the rendered result instead.

Before adding workflow logic, search for an existing module that already owns the domain or integration behavior. Keep workflows as thin orchestration; put reusable business and integration logic in modules.

Treat local Solution preview as connected development, not a data sandbox. Table, managed-file, config, and integration writes can affect the selected instance.

### 4. Verify the complete experience

Run the relevant automated tests, type checks, builds, and end-to-end behavior. Exercise the primary path and important failures.

For user-facing apps, inspect the rendered result. Verify:

- every changed route and core interaction;
- light and dark modes when theme support is declared;
- loading, empty, error, validation, disabled, and success states;
- narrow and wide layouts, overflow, keyboard use, focus visibility, and contrast;
- spacing, typography, hierarchy, and component consistency.

Give the user the exact local preview command or URL when possible. Fix material polish problems before calling the work complete.

### 5. Ship or hand off intentionally

Summarize what changed and what was verified. Separate source changes from live platform mutations and undeployed work. Call out production-sensitive effects, then offer the appropriate deploy, publish, commit, or push step when the user is ready. Never imply that deployment or publication occurred when it did not.

## How the boundaries fit together

Keep these layers distinct:

- **Working model and source ownership** determine where definitions are edited: instance `_repo`/live records or local Solution source.
- **Lifecycle ownership** determines how definitions reach the platform: immediate Workspace mutation or Solution deploy.
- **Dependency resolution** determines where running code may look after its own resources miss. `global_repo_access` changes this layer only.
- **Authorization** determines whether a resolved entity or row/file operation is allowed. Organization, access level, roles, policies, and external-user rules always apply.
- **Environment data** such as table rows, runtime files, config values, and integration mappings can be live even while Solution source is running locally.

Therefore, a Solution supported by the Workspace may call an eligible loose workflow without owning it; that workflow keeps its own registration and access controls. A globally scoped Solution entity is still deploy-managed. Shared fallback can make a table or file readable without making it writable or Solution-owned.

These relationships produce the following hard boundaries:

- Solution-owned apps, workflows, forms, agents, tables, configs, claims, and file-location declarations change through local source plus `bifrost solution deploy`; do not live-mutate their managed records.
- Instance `_repo` source changes through direct `bifrost files` commands. Writing source does not replace entity creation or workflow registration.
- `.bifrost/*.yaml` is Solution source inside a Solution. Outside a Solution, discover and mutate live entities with their CLI commands rather than treating an exported manifest as live state.
- `global_repo_access` is a runtime fallback gate, not install scope, global entity permission, or an authorization bypass.
- Prefer dedicated entity commands. Before using `bifrost api`, confirm the endpoint in `generated/openapi-digest.md`; it addresses the Bifrost platform API only, never a third-party integration API.
- Keep integration credentials and decrypted config values behind Python workflows; do not expose them to browser code.

## Reference routing

| Need | Load for the task |
|---|---|
| Create, bind, start, capture, deploy, export, or install a Solution | `references/solutions.md` |
| Read or write instance `_repo` source; maintain loose modules, workflows, or v1 apps | `references/repository.md` |
| Author, register, execute, rename, or expose a Python workflow | `references/workflows.md` and the relevant section of `references/python-sdk.md` |
| Build or modify a v2 Solution app | `references/apps-v2.md`, `references/app-quality.md`, and `references/web-sdk-v2.md` |
| Maintain an existing inline v1 app | Read `references/apps-v1.md` and `references/app-quality.md`; search `references/platform-api.md` only for the exact v1 export being used |
| Create or change forms, agents, configs, integrations, events, orgs, roles, or claims | `references/entities.md` |
| Define a table, write policies, or use table data | `references/tables.md` |
| Store, list, upload, download, or permission managed runtime/user files | `references/files.md` |
| Use resources outside a Solution install | `references/solution-resource-access.md` |
| Work with Bifrost through MCP without the CLI or local source | `references/mcp-mode.md` |
| Need an exact command or flag | `generated/cli-reference.md` |
| Need an exact Python or web SDK signature/export | the matching file under `generated/` |
| Need to confirm a platform endpoint | `generated/openapi-digest.md` |
