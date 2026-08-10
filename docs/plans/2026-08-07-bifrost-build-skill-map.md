# Bifrost Build Skill — Review Map

Date: 2026-08-07

Status: Implemented on `codex/build-skill-map` for review; not merged

## Purpose

This document maps the `bifrost:build` rewrite now implemented on the review branch. It remains the review contract for the merge: what the skill should make an agent do, what each file teaches, what was removed, and how the finished skill should be tested.

The [June rebuild](2026-06-09-build-skill-rebuild-plan.md) made the skill substantially more accurate. It added Solutions, generated CLI and SDK appendices, source freshness tracking, and forward-testing with lower-cost agents. Those are worth keeping. Its remaining problem is structural: the skill is organized around platform facts and accumulated corrections rather than the sequence a builder needs to follow. A model can retrieve many true details and still fail to produce a coherent, polished result.

This proposal preserves the truth infrastructure and replaces the reading experience.

## Approval scope

The branch implementation follows these boundaries:

- Replace the current hub with a short, mandatory build process.
- Reorganize curated references around working context and artifact type.
- Make app design, theming, interaction states, accessibility, and visual verification first-class requirements.
- Make direct CLI file operations the normal way to work with instance `_repo` source.
- Preserve the generated, source-derived appendices and reference freshness checks.
- Remove repeated explanations, giant command catalogs, and paths that are no longer part of the recommended workflow.

The branch changes the canonical packaged skill source and its generated Codex mirror. It does **not** change CLI behavior, platform behavior, or the currently released plugin until merged and released.

## Design diagnosis

The current material is fact-rich but weak at wayfinding:

1. The top of the skill explains connection internals before establishing what the agent is trying to build.
2. Workspace detection is presented as shell machinery rather than a simple context decision.
3. Solution authoring, instance `_repo` authoring, runtime resource access, and entity scope are easy to conflate.
4. Curated references repeat exact commands that already exist in generated appendices.
5. `apps.md` mixes scaffolding, imports, React correctness, layout, v1 compatibility, theming, CRUD, and drag-and-drop. Theming is present but buried.
6. Passing tests is treated as stronger evidence of app quality than it is. A compile-successful app can still be visually incoherent, incorrectly themed, inaccessible, or unpleasant to use.
7. Corrections discovered during validation were appended near the symptom. They were not consistently folded back into a single mental model.

The new structure should let an agent answer these questions in order:

1. Which instance and, if applicable, which Solution install am I targeting?
2. What is the user trying to add or change?
3. Who owns the source: this Solution or the instance `_repo`?
4. Which platform entities and environment state does the change require?
5. What proves that the behavior and user experience are finished?
6. What live changes, deployment steps, or handoff remain?

## Design principles

1. **Process before reference.** The hub teaches the operating sequence; references answer questions encountered during it.
2. **Action before implementation detail.** Tell the agent what to inspect or do and give only the reason needed to avoid a mistake.
3. **One authoritative home per rule.** Other files link to the rule instead of paraphrasing it.
4. **Progressive disclosure.** Load only the references needed for the current artifact and context.
5. **Generated facts stay generated.** Exact flags, endpoint existence, SDK signatures, and export names belong in generated appendices.
6. **Design quality is part of correctness.** A user-facing app is not complete until its hierarchy, states, theme behavior, responsiveness, accessibility, and rendered result have been checked.
7. **Prefer one normal path.** Alternative mechanisms are omitted unless the agent genuinely needs them to complete normal work.
8. **Separate source ownership from runtime access.** A Solution being able to read a shared resource does not make that resource Solution-owned or freely mutable.
9. **State production effects plainly.** Local preview and direct CLI mutations can touch real instance data.

## Core mental model

The skill should define these terms once, near the top.

| Term | Meaning |
|---|---|
| Instance | A Bifrost deployment selected by `BIFROST_API_URL` or the user's saved default connection. |
| Solution definition | Local, versioned source rooted at `bifrost.solution.yaml`. It describes a portable package. |
| Solution install | A concrete copy of a Solution on one instance, bound locally through `.env`. It has an install scope and environment-specific state. |
| Instance `_repo` | Instance-level source storage for loose modules, v1 apps, and workflow files. It is not the same thing as global entity scope. |
| Loose entity | A live platform record not owned by a Solution install. It still has organization, access-level, role, and policy boundaries. |
| Solution runtime file | User or application data in a location declared by `.bifrost/files.yaml`. It is not Solution source code. |
| `global_repo_access` | Permission for a Solution runtime to fall back to eligible shared modules, registered loose workflows, tables, and files. It is not install scope and not an authorization bypass. |

The source-ownership decision is presented as three working models:

| Working model | Why to use it | Primary authoring path |
|---|---|---|
| Workspace | Maintain v1 content or deliberately create loose/shared instance resources | Use entity CLI commands and `bifrost files` directly against the selected instance. File creation alone does not create or permission the corresponding platform entity; v1 app source remains draft until published. |
| Solution | Build a cohesive, portable package that owns and deploys its definitions together | Edit local Solution source; use `bifrost solution start`; deploy the Solution with `global_repo_access: false`. |
| Solution supported by the Workspace | Let a Solution intentionally depend on eligible shared modules, registered workflows, tables, or files | Keep Solution-owned changes local/deploy-managed and manage Workspace dependencies separately. Set `global_repo_access: true`; it grants fallback, not ownership or authorization. |

## Proposed high-level process

This is the controlling workflow every build follows. It should appear immediately after the overview and prerequisites in `SKILL.md`.

### 1. Choose the working model and target

- Present the three working models first, including when and why each should be used. If `bifrost.solution.yaml` exists, use its `global_repo_access` flag to distinguish the two Solution models. Without the marker, use the Workspace model. Confirm the choice before scaffolding net-new work.
- Route Workspace work to `references/repository.md`, Solution work to `references/solutions.md`, and a Solution supported by the Workspace to both `references/solutions.md` and `references/solution-resource-access.md` for commands and exact boundaries.
- Read `.env` in the intended CLI invocation directory, normally the project or Solution root, before discovery or mutation. Parent-directory `.env` files are not selected implicitly. Use these Bifrost selector and binding fields to understand the target:
  - `BIFROST_API_URL`
  - `BIFROST_SOLUTION_ID`
  - `BIFROST_SOLUTION_SLUG`
  - `BIFROST_SOLUTION_ORG_ID`
  - `BIFROST_SOLUTION_SCOPE`
- Treat `.env` as local instance/install selection, not portable source. A normal Bifrost project `.env` should not contain access or refresh tokens; report only the selector/binding fields above and do not commit the file.
- Run the read-only connection check and compare the selected connection with the user's default.
- If `.env` selects a non-default instance, tell the user which instance/install is selected and give them the opportunity to switch before doing meaningful work. Removing `BIFROST_API_URL` restores the normal default-connection behavior.
- A sandbox may not be able to read the host credential store. If authentication appears absent, retry the read-only connection check with host access before asking the user to log in again.
- `bifrost.solution.yaml` in the project root means this is a Solution workspace. Do not include a custom shell loop in the skill. If the agent starts in a nested directory and context is ambiguous, locate the project root with its ordinary file tools.

### 2. Understand and plan the change

- If this is a Solution, state the Solution name/slug, bound install, instance, and install scope.
- Confirm what the user is trying to build or change and who will use it.
- For anything beyond a small, well-bounded fix, make a short plan before editing.
- The plan identifies:
  - the user outcome and acceptance criteria;
  - source files and platform entities involved;
  - organization, access level, roles, and policies;
  - external integrations, configs, tables, and file locations;
  - for apps, information hierarchy, primary interactions, visual direction, theme behavior, responsive behavior, and non-happy-path states;
  - live-instance or deployment effects.

The agent should not force a ceremony for an obvious typo or small bug. It should not begin a material feature while the outcome or ownership model is unclear.

Apply a compatible `(organization, access_level, role_ids)` tuple across a dependency chain unless a particular entity intentionally requires different access. Record and verify every exception with a representative caller.

### 3. Build and validate in the correct ownership model

- **Solution-owned work:** edit local Solution files and manifests. Use tests and `bifrost solution start` for local app/workflow iteration. Do not live-mutate Solution-owned entities.
- **Instance `_repo` work:** use `bifrost files list`, `search`, `read`, `exists`, `write`, and `delete` to inspect and modify source directly. Use entity CLI commands to create or update the live records that make source usable.
- Use curated references for the method and generated appendices or `--help` for exact command syntax; do not infer familiar flags.
- Use test-driven development for behavior: define the acceptance result, write a focused failing test when the behavior can be expressed usefully, implement the change, and make it pass. Add the appropriate unit, integration, or UI coverage. Visual-only acceptance still requires rendered inspection rather than a contrived test.
- Treat `bifrost solution start` as connected development, not an isolated data sandbox. Table, file, config, and integration writes can change the selected instance.
- Inventory required platform changes while building. Policies, tables, configs, integrations, roles, and registrations are part of the feature, not deployment cleanup.

### 4. Verify the complete experience

- Run the relevant automated tests and type/build checks.
- Exercise the primary workflow and important failure states.
- For apps, inspect the rendered result rather than stopping at tests:
  - every route and core interaction;
  - light and dark modes when theme support is declared;
  - loading, empty, error, disabled, validation, and success states;
  - narrow and wide layouts, overflow, keyboard use, focus visibility, and contrast;
  - consistency of spacing, typography, hierarchy, and component treatment.
- Invite the user to preview locally when possible and give a precise URL or command.
- Fix material polish issues before calling the work complete.

### 5. Ship or hand off intentionally

- Summarize what changed and what was verified.
- Separate source changes already made from live platform mutations still required.
- Call out production-sensitive effects, especially shared configs/integrations, existing tables or policies, registered loose entities, and changes to an installed Solution.
- Offer the appropriate deploy/publish/commit/push step when the user is ready; do not imply deployment or publication occurred if it did not.
- Include unresolved work or follow-up context in the same final handoff. Do not maintain a separate boilerplate “session summary” section in the skill.

## File and registration model

This needs to be explicit because storage and executability are different concerns.

### Direct `_repo` file authoring

The curated skill should teach only the direct file commands for ordinary `_repo` work:

```bash
bifrost files list <path>
bifrost files search <query>
bifrost files read <path>
bifrost files exists <path>
bifrost files write <path> --from-file <local-file>
bifrost files delete <path>
```

Exact flags come from `generated/cli-reference.md`; examples in the curated reference must be verified against it. The direct commands operate on the selected instance. `files write` replaces the complete text content, so read first, preserve unrelated content, write the replacement, and read it back to verify. Confirm the exact target before deletion.

From a Solution directory, the unqualified commands still address instance `_repo`; on commands that accept it, `--solution` addresses installed Solution runtime files, not local Solution source.

The curated build skill should not route agents through a local synchronization daemon. Exhaustive generated CLI documentation may still list every product command, but the builder journey should not present synchronization as an authoring choice.

### Loose workflow registration

Writing Python into `_repo` does not make its decorated functions executable. For each callable that should exist as a loose workflow:

```bash
bifrost workflows register \
  --path workflows/example.py \
  --function-name run \
  --org "Org A" \
  --access-level role_based \
  --role-ids operators
```

The registration creates the stable, scoped, permissioned workflow record. One source file may contain multiple decorated functions and therefore multiple registrations.

- Editing the body of an already registered function does not require registration again.
- Renaming or moving the function uses `bifrost workflows replace` so the UUID and dependents survive.
- Registering the new path instead creates a different workflow entity.
- A Solution with `global_repo_access: true` may fall back only to an eligible registered loose workflow. A source file with no registration is not callable through workflow resolution.
- Solution-owned workflows are declared in the Solution manifest and created or updated by deploy. They are not registered live.

The same source-versus-record distinction should be stated for v1 apps: source under `_repo` requires a corresponding `inline_v1` app record with its own organization/access/roles before it can be served.

## App quality contract

This is a dedicated reference and a mandatory load for any user-facing app change. It should describe what “done” means without becoming a general React textbook.

### Product and layout intent

Before implementing a material UI, the agent records:

- the audience and primary job;
- the most important information and action on each screen;
- page/route hierarchy and navigation;
- expected data density and long-content behavior;
- the existing visual language to preserve, or the visual direction for a new app.

The implementation should make the primary action and status legible at a glance. It should not default every feature to interchangeable cards, excessive chrome, or a generic dashboard when the task calls for a focused workflow.

### Theme contract

- Start from the scaffolded token layer and use semantic tokens such as `background`, `foreground`, `card`, `muted`, `border`, `primary`, and `destructive`.
- Do not use hardcoded light-only colors for ordinary surfaces or text.
- Define paired light/dark variables for intentional brand or data-visualization colors.
- `supportsTheme` is an app-wide promise, not a header decoration. Keep it only when every route, overlay, control, table, chart, and state works in light and dark modes.
- Verify the rendered app after toggling both modes. If full support is outside scope, remove the theme toggle instead of shipping a partially themed app.

### Interaction and resilience contract

- Every data-backed view has intentional loading, empty, error, and success behavior.
- Mutations expose progress, prevent accidental duplicate submission where needed, and show actionable errors.
- Forms have labels, validation, and sensible focus behavior.
- Destructive actions communicate impact and require confirmation proportional to risk.
- Layouts work inside the Bifrost mount container and at narrow widths; scrolling belongs to the content region that actually overflows.
- Semantic HTML, keyboard access, visible focus, and adequate contrast are required.

### Visual verification contract

Automated tests prove behavior; they do not approve the design. The agent must preview the app, inspect representative routes and states, and correct visible hierarchy, spacing, clipping, contrast, theme, or interaction problems. The final handoff states what was visually checked.

## Proposed skill package

```text
bifrost-build/
├── SKILL.md
├── references/
│   ├── solutions.md
│   ├── repository.md
│   ├── workflows.md
│   ├── apps-v2.md
│   ├── apps-v1.md
│   ├── app-quality.md
│   ├── entities.md
│   ├── tables.md
│   ├── files.md
│   ├── solution-resource-access.md
│   ├── python-sdk.md
│   ├── web-sdk-v2.md
│   ├── mcp-mode.md
│   ├── platform-api.md        # exact legacy-v1 lookup; coverage-checked
│   └── sources.yaml
└── generated/
    ├── cli-reference.md
    ├── openapi-digest.md
    ├── python-sdk-signatures.md
    └── web-sdk-surface.md
```

The proposed package has three layers:

1. `SKILL.md` controls behavior and routing.
2. Curated references teach decisions, lifecycle, traps, and completion criteria.
3. Generated appendices answer exact factual questions and remain machine-checked.

### Compression guardrails

The current human-authored hub and references are roughly 25,800 words before the generated appendices. The rewritten operating guidance should target 10,000–12,000 words, excluding the coverage-checked legacy-v1 symbol lookup:

- `SKILL.md`: no more than about 1,500 words or 200 lines.
- Most curated references: 500–1,000 words; exceed that only for a real decision matrix such as Solution resource access.
- One example per non-obvious rule, not one example per command.
- If exact syntax or a complete surface already exists in `generated/`, link there instead of copying it.
- A correction that changes the mental model must rewrite the authoritative section; it must not be appended as another warning elsewhere.

## Proposed `SKILL.md` map

Target: roughly 150–200 lines. The first screen should contain the overview, prerequisites, and the beginning of the required process.

### Frontmatter

The description should enumerate the artifacts and both ownership models so the skill triggers for Solutions and instance-level work. “Global `_repo`” should be replaced with “instance `_repo`” to avoid conflating storage with access scope.

### Overview

Near-final wording:

> Bifrost is an open-source platform for building apps, workflows, forms, and agents, with foundational features such as managed tables and files. This skill guides coding agents and users through planning, building, testing, previewing, and deploying work on the Bifrost platform.

### Prerequisites

Keep this short:

- Confirm the CLI exists and the current connection is authenticated.
- If setup is incomplete, route to `bifrost:setup`.
- Explain only the sandbox/keyring exception needed to prevent unnecessary re-login.

### Required process

Use the five stages above verbatim or in a shorter equivalent form. This is the dominant content of the hub.

### How the boundaries fit together

Explain that source ownership, definition lifecycle, runtime dependency resolution, authorization, and environment data are related but separate. Then add these hard boundaries:

- Solution-owned entities change through local source and deploy.
- Instance `_repo` source changes through direct `bifrost files` commands.
- Runtime access never bypasses organization, role, policy, or external-user checks.
- Dedicated entity commands are preferred over raw platform API calls.
- Third-party APIs are called from workflows through integrations, never through `bifrost api`.

### Reference router

| Need | Load |
|---|---|
| Create, bind, start, capture, deploy, or install a Solution | `references/solutions.md` |
| Read/write instance `_repo` source or maintain loose v1 content | `references/repository.md` |
| Author or register Python workflows | `references/workflows.md` and the relevant part of `references/python-sdk.md` |
| Build or modify a v2 Solution app | `references/apps-v2.md`, `references/app-quality.md`, and `references/web-sdk-v2.md` |
| Maintain an existing inline v1 app | `references/apps-v1.md` and `references/app-quality.md` |
| Create or change forms, agents, configs, integrations, events, orgs, roles, or claims | `references/entities.md` |
| Read/write tables or reason about policies | `references/tables.md` |
| Design or use managed runtime/user files | `references/files.md` |
| Use shared resources from a Solution | `references/solution-resource-access.md` |
| Work without the CLI/source filesystem | `references/mcp-mode.md` |
| Need an exact command/flag | `generated/cli-reference.md` |
| Need an exact SDK signature/export | the matching generated SDK appendix |
| Need to verify a platform endpoint | `generated/openapi-digest.md` |

### Handoff

End the hub with the ship/handoff requirements, not a fixed Markdown template. The response should tell the user what changed, what was verified, what touched live state, and what remains to deploy.

### Content intentionally absent from the hub

- A long explanation of credential storage internals.
- A shell loop for detecting a Solution.
- Full organization/access compatibility examples.
- The MCP naming essay.
- Entity command catalogs.
- Exact SDK or API signatures.
- Skill-maintainer instructions.
- A standalone session-summary template.

Those items either move to the relevant reference/generated source or disappear.

## Curated reference contracts

Each reference gets an explicit job and a “do not include” boundary so it cannot grow back into a catch-all.

### `references/solutions.md`

**Load when:** creating or changing Solution-owned work.

**Contains:** descriptor versus install binding; expected folder structure; create/bind/scaffold flow; manifests as Solution source of truth; workflow manifest registration; direct net-new manifest authoring versus capture/pull for adoption; `solution start`; sealed deploy-time module vendoring versus runtime fallback; deployment/install lifecycle; one definition and multiple installs; logo/file declarations; production-impact warning.

**Does not contain:** general app design rules, full SDK tutorials, instance `_repo` authoring, or a repeated `global_repo_access` matrix.

### `references/repository.md`

**Load when:** working with loose source on the selected instance, whether or not the current directory is a Solution.

**Contains:** `_repo` terminology; direct file discovery/read/write/delete; paths for workflows/modules/v1 apps; source-versus-entity distinction; loose workflow registration; v1 app-record creation; live entity mutation model; rename/replace safety; verification after mutation.

**Does not contain:** a local synchronization workflow, Solution source authoring, exhaustive entity commands, git procedures, or MCP-only details.

### `references/workflows.md`

**Load when:** writing or changing Python workflow code.

**Contains:** decorator choice; input/output and context; local execution; tests; error behavior; dependencies; Solution manifest registration versus loose live registration; multiple functions per file; execute/replace/remap semantics; portable `path::function` references; MCP tool naming only when a workflow is exposed as a tool.

**Does not contain:** a full Python SDK catalog or duplicate CLI help.

### `references/apps-v2.md`

**Load when:** building a Solution `standalone_v2` app.

**Contains:** scaffold-first structure; source locations; mount/provider/router contract; real import boundaries; workflow reference format; local preview; dependency rules; Bifrost mount-container constraints; pointers to app quality and web SDK.

**Does not contain:** v1 platform globals, generic UI design guidance, long CRUD examples, or repeated SDK signatures.

### `references/apps-v1.md`

**Load when:** maintaining an existing `inline_v1` app. Never as the route for a new app.

**Contains:** app-record-first requirement; `_repo` path; injected platform imports; user component and npm dependency rules; available v1-only hooks; targeted migration notes toward v2.

**Does not contain:** v2 scaffolding or language that makes v1 look like an equal default.

### `references/app-quality.md`

**Load when:** any app changes user-visible UI.

**Contains:** the complete app quality contract above; semantic theme tokens; light/dark acceptance; visual hierarchy; responsive/overflow behavior; interaction states; accessibility; visual preview checklist; focused Bifrost-specific examples.

**Does not contain:** app lifecycle, CLI entity creation, SDK reference material, or a grab bag of unrelated React tips.

### `references/entities.md`

**Load when:** creating or changing non-code platform entities.

**Contains:** the common organization/access/role model; deploy-owned versus loose mutation boundary; task-level semantics for forms, agents, configs, integrations, events, orgs, roles, and claims; `@file` input; dependency/reference safety; verify-after-write behavior.

**Does not contain:** every command flag. It links to the generated CLI appendix for exact syntax.

### `references/tables.md`

**Load when:** defining a table, writing a policy, or using table data.

**Contains:** schema/policy design; environment and ownership implications; Python-versus-web operation differences; the table-versus-row delete trap; query/filter semantics; row identity and audit fields; live updates; Solution shared-table fallback and its read-only boundary.

**Does not contain:** a duplicate catalog of all table signatures.

### `references/files.md`

**Load when:** an app or workflow stores, lists, uploads, downloads, or permissions runtime/user files.

**Contains:** the distinction between `_repo` source and managed data; text versus binary operations; instance and Solution file locations; `.bifrost/files.yaml` declarations; CLI, Python SDK, and web SDK responsibilities; file policies; signed read/write URLs; Solution fallback and write ownership; live-data implications.

**Does not contain:** source-code authoring procedures, duplicated SDK signatures, or the general Solution lifecycle.

### `references/solution-resource-access.md`

**Load when:** a Solution depends on anything not owned by its install.

**Contains:** the exact resource matrix for modules, registered loose workflows, tables, files, configs, integrations/OAuth, and knowledge; install-org/global lookup order; read/write differences; security and portability consequences; `solution start` parity expectations.

**Does not contain:** general Solution lifecycle or vague “global access” shorthand.

### `references/python-sdk.md`

**Load when:** workflow code needs a Bifrost service.

**Contains:** one concise, decision-oriented example per namespace; return-shape traps; scope behavior; links to generated signatures.

**Does not contain:** manually copied complete signatures or workflow lifecycle commands.

### `references/web-sdk-v2.md`

**Load when:** a v2 app uses Bifrost runtime services.

**Contains:** provider/context behavior; selecting query/mutation/low-level workflow hooks; tables/files surfaces; error classes; portable references; theme context behavior with a pointer to the quality contract.

**Does not contain:** general app anatomy beyond the minimum needed to explain the SDK, or duplicated theme auditing instructions.

### `references/mcp-mode.md`

**Load when:** the CLI or source filesystem is unavailable and Bifrost MCP tools are present.

**Contains:** tool discovery; verified tool-name patterns; read-before-write behavior; patch/replace semantics; entity creation and registration; validation and execution; known parity caveats.

**Does not contain:** CLI fallback instructions or an independently maintained copy of every tool schema.

### `references/platform-api.md`

**Load when:** confirming an exact injected export while maintaining an existing inline v1 app.

**Contains:** the coverage-checked v1 runtime export catalog. Search for the required symbol; do not read it as narrative guidance.

**Does not contain:** v2 app guidance or general builder workflow.

## Generated appendix contracts

| File | Source of truth | Use |
|---|---|---|
| `generated/cli-reference.md` | Real CLI command tree and help | Exact verbs, flags, and argument shapes |
| `generated/openapi-digest.md` | FastAPI OpenAPI schema | Confirm endpoint existence before `bifrost api` |
| `generated/python-sdk-signatures.md` | Python SDK introspection | Exact workflow-side signatures |
| `generated/web-sdk-surface.md` | v2 package exports/types | Exact v2 web SDK surface |

Generated references may be long because they are lookup material. They should not be read front-to-back or used as the skill's narrative.

## Current-to-proposed migration

### Current `SKILL.md`

| Current section | Disposition |
|---|---|
| Header and opening paragraph | Replace with the concise overview. |
| Prerequisites | Keep, shorten, and put the actual connection check in “Establish the target.” |
| Connection model | Reduce to `.env` selection, default comparison, and sandbox/keyring exception. Remove the rest from builder-facing prose. |
| Workspace detection shell loop | Delete. Replace with the root marker and ordinary project-root discovery when needed. |
| Solution versus `_repo` branch | Keep as the three-row working-context table, with direct file commands replacing synchronization. |
| Organization/access compatibility section | Move the general rule into planning and `entities.md`; retain a short hard boundary in the hub. |
| Global hard rules | Rewrite as the five working-context boundaries. |
| MCP naming convention | Move to `workflows.md`, where it applies only when exposing a workflow as a tool. |
| Reference index | Replace with the artifact/context router. |
| Reference maintenance instructions | Remove from the builder skill; keep in maintainer tooling/documentation. |
| Session summary | Fold into the final handoff stage. |

### Current curated references

| Current file | Disposition |
|---|---|
| `references/apps.md` | Split v2 lifecycle/anatomy into `apps-v2.md`; move design/theme/states/layout/accessibility into `app-quality.md`; move v1-only material into `apps-v1.md`; delete overly specific grab-bag sections that are not platform constraints. |
| `references/entities.md` | Reduce from a command catalog to common semantics and non-code entity decisions; exact flags remain generated. |
| `references/import-patterns.md` | Merge into `apps-v1.md`; keep v2 imports in `apps-v2.md`. |
| `references/mcp-mode.md` | Retain and tighten around the MCP-only operating flow. |
| `references/platform-api.md` | Retain as a coverage-checked legacy-v1 lookup, relabel it clearly, and route to it only from `apps-v1.md`. Its existing export-name drift test remains authoritative. |
| `references/python-sdk.md` | Retain but shorten to decisions, examples, and return-shape traps. |
| `references/repo.md` | Replace with `repository.md`, centered on direct `bifrost files` operations and source/entity registration. Remove synchronization and git guidance. |
| `references/rest-api.md` | Remove as a standalone reference. Keep the platform-only boundary in the hub and route exact endpoint checks to the OpenAPI digest. |
| `references/solution-resource-access.md` | Retain; edit for terminology and eliminate duplication with Solutions and SDK refs. |
| `references/solutions.md` | Retain; make it the Solution ownership/lifecycle reference and move app/workflow quality elsewhere. |
| `references/tables.md` | Retain; emphasize design/policy decisions and cross-SDK traps. |
| Managed-file guidance currently spread across `solutions.md`, `solution-resource-access.md`, `python-sdk.md`, and `web-sdk-v2.md` | Consolidate decisions and boundaries in new `references/files.md`; leave only context-specific pointers in the source references. |
| `references/web-sdk-v2.md` | Retain; remove duplicated app anatomy and theme-audit prose. |
| `references/workflows-python.md` | Rename to `workflows.md`; integrate the registration model and MCP naming at the point of use. |
| `references/sources.yaml` | Retain and update paths/globs after the split. |

### Generated references

Keep all four current generated appendices and their CI freshness enforcement. Keep the legacy v1 platform catalog as a separately coverage-checked exact lookup until its descriptive content has a real deterministic generator. Generated output should remain exhaustive; curated references should link to lookup material only for exact facts.

## Scenario routing tests

The rewrite is not complete when the prose looks cleaner. Fresh agents should complete representative tasks without reading unrelated references or inventing a second path.

| Scenario | Expected route and critical behavior |
|---|---|
| Net-new Solution app | Establish bound install → plan user experience → `solutions.md` + `apps-v2.md` + `app-quality.md` + web SDK → scaffold → tests → `solution start` → inspect both themes/states → offer deploy |
| Existing Solution workflow change | Confirm Solution/install → `solutions.md` + `workflows.md` + relevant Python SDK section → edit local source/manifest → test locally → no live workflow registration → offer deploy |
| New loose `_repo` workflow | Confirm instance/org/access → `repository.md` + `workflows.md` → write source with `bifrost files` → register each callable → execute registered workflow → verify permissions |
| Edit registered loose workflow body | Read source with CLI → write replacement → execute existing registration → do not register again |
| Rename loose workflow | Write new path/function → `workflows replace` existing UUID → verify dependents; do not mint a replacement entity |
| Existing v1 app change | Confirm app record and scope → `repository.md` + `apps-v1.md` + `app-quality.md` → direct file read/write → validate rendered app |
| Solution using shared workflow | Confirm `global_repo_access: true` → inspect loose registration and access tuple → verify fallback in local and deployed contexts; source presence alone must fail the test |
| Solution using shared table/file | Load resource-access reference → verify lookup tier, policies, and read/write boundary → test against selected live instance |
| Form or agent build | Plan org/access and backing workflow refs → use `entities.md` plus exact CLI appendix → verify created/updated entity and its dependencies |
| MCP-only edit | Route directly to `mcp-mode.md` → discover available tools → read before patch/replace → validate/execute with no invented CLI filesystem |

## Acceptance criteria for the rewrite

### Information architecture

- The hub is no more than about 200 lines and reads as an operating method.
- Every mandatory rule has one authoritative home.
- No curated reference is an exhaustive CLI, API, or SDK dump.
- v1 and v2 app guidance cannot be mistaken for one another.
- `_repo`, entity global scope, Solution install scope, and `global_repo_access` are never used interchangeably.

### Behavior

- The normal `_repo` path uses direct `bifrost files` operations.
- Loose workflow registration and permissions are never skipped.
- Solution-owned records are never live-mutated.
- The agent reads `.env`, reports the selected instance/install, and compares it with the default before material work.
- The agent plans material changes, but does not over-process small fixes.
- Local preview warnings accurately distinguish source isolation from live data effects.

### App design and polish

- A new-app plan includes audience, task hierarchy, visual direction, theme behavior, states, and responsiveness.
- Semantic theme tokens are used by default.
- `supportsTheme` is retained only after app-wide light/dark verification.
- Automated checks and rendered visual QA are both required.
- Loading, empty, error, validation, disabled, and success states are evaluated where relevant.
- The final handoff reports the rendered routes/states/themes actually checked.

### Accuracy and maintainability

- Generated references reproduce deterministically and pass existing freshness gates.
- Every curated command example is checked against the generated CLI reference.
- Source mappings are updated for every renamed or split reference.
- The Claude and Codex packaged copies remain identical through the existing sync mechanism.
- Forward tests use fresh, lower-cost agents and realistic user prompts rather than prompts that restate the desired process.

## Implementation sequence after approval

1. Rewrite `SKILL.md` first so the process and router are stable.
2. Replace `repo.md` with the direct-file `repository.md` and correct loose workflow/app registration guidance.
3. Split app guidance into v2, v1, and app-quality references.
4. Tighten Solutions, workflows, resource access, tables, SDK, entities, and MCP references against their contracts.
5. Relabel and isolate the coverage-checked v1 platform catalog; remove the standalone REST reference.
6. Update `sources.yaml`, generators, routing links, and drift tests.
7. Run deterministic generation and documentation checks.
8. Run the scenario matrix with fresh agents. Record failures by violated contract, fix the authoritative section, and rerun until the required clean threshold is reached.

## Decisions requested before implementation

1. Approve the five-stage builder process as the hub's controlling structure.
2. Approve direct `bifrost files` operations as the only curated `_repo` authoring path.
3. Approve the v2/v1/app-quality split, including rendered visual QA as a completion requirement.
4. Approve moving exact platform surfaces to generated appendices and shrinking curated references to decisions and traps.
5. Approve the terminology change from “global `_repo` workspace” to “instance `_repo` source” wherever storage, rather than entity access scope, is meant.
