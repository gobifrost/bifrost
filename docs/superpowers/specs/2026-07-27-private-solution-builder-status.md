# Private Solution Builder — Status & Handoff

**Date:** 2026-07-27
**Branch:** `code-builder` (integration branch; `main` untouched)
**Worktree:** `.worktrees/ai-solution-builder-spec`
**Design:** `2026-07-25-private-solution-builder-design.md` (revised 2026-07-27)

Read this first, then the design doc. This file says what is real, what is
not, and what to do next.

## Where to start

The builder is **not usable yet**. It authors files; it cannot yet produce a
running app. The next work is the build plane (deploy + preview), then the UX
rebuild. See "Next" below.

## What is real, verified live

Driven against a dev stack with OpenRouter configured (Claude Sonnet 4.5),
not just tested:

- Create a private Solution → it scaffolds revision 0 automatically.
- Open a builder chat session.
- Run an agent turn → the model edits the workspace through safe file tools
  and a new immutable revision is written. Two turns observed: 5 tool calls
  (13.3s) and 3 tool calls (13.3s).
- Download a revision → sha256 of the bytes matches the recorded hash.
- Undo to an earlier revision → content restored,
  `restored_from_revision_id` recorded, history grew rather than losing rows.

Gates at last full run: backend e2e 1730 passed / 0 failed; backend unit
5475 passed; pyright + ruff 0 errors; client tsc/lint 0 errors; client
vitest 1727 passed.

## What exists in code

| Area | State |
|---|---|
| `Solution.owner_user_id` / `visibility` + partial slug indexes | built |
| `Config.solution_id` + exact-match index | built |
| `SolutionAccessService` (central private gate) + list criterion | built |
| `solutions.build` capability service | built |
| Builder ORM (project / revision / session / turn / build job) | built |
| `SolutionRevisionStorage` (streamed, content-addressed) | built |
| Safe workspace fs + archive library | built |
| Private-Solution REST surface (CRUD, sessions, revisions, undo, turns) | built |
| Turn lifecycle + scaffold + undo | built |
| Agent tool loop (`InternalLoopRuntime`) | built, **being replaced** — see decisions |
| App-host launch codes / session / token / exact-scope resolution | built |
| `actor_type` default-deny in core auth | built |
| Private deploy side-effect suppression + post-condition assertion | built |
| Builder UI (chat, revision list, preview shell) | built, **unreachable in practice** |

## What is NOT built (the honest gaps)

1. **The build plane (WP3).** No runner container, no build job execution, no
   deploy from a builder revision. This is why a built Solution shows **no
   apps, workflows, forms, or tables** — the builder writes source files; only
   deploy materializes entities. Everything else downstream is blocked on this.
2. **Live preview.** `SolutionBuilder.tsx` hardcodes `const appOrigin: string |
   null = null`, so the preview pane always renders its empty state. Depends
   on (1).
3. **`builder_model` setting.** The design promises `builder_model ?? global
   model`; `model_gateway.py` just calls the global client. No setting, no UI
   field. The builder currently uses whatever the platform model is, with
   nothing in the UI explaining why.
4. **Discoverability / UX.** The builder lives at `/solutions/{id}/builder`
   with only a "New with AI" button on the Solutions admin page. There is no
   edit-an-existing-app flow at all. Agreed replacement: `/build` as a
   top-level nav destination (prompt box + your builds), app-first language,
   `Edit in Builder` loading existing state with full history.
5. **Skill bundles.** `Agent.bundle_path` and `read_skill_asset` do not exist
   anywhere in the codebase. Required before the builder can be a bundle-backed
   agent.

## Decisions that changed the design (2026-07-27)

- **The builder is an Agent with a skill bundle**, not a bespoke loop. Its
  `system_prompt` is the `bifrost-build` SKILL.md body; `bundle_path` points at
  that skill directory; it executes through the existing `AgentExecutor`.
  `BuilderAgentRuntime` / `InternalLoopRuntime` are superseded.
- **Bundles travel with Solutions.** `bundle_path` is relative to the Solution
  root (like an app's source path) so `bifrost solution deploy` on a repo ships
  the agent's bundle with it.
- **Model-authored Python is inert until a human reviews it.** In-house
  sandboxing was rejected (`2026-06-17-code-execution-decision.md`): running
  model-authored code with real credentials is RCE-by-hallucination. A generated
  workflow is authored and deployed inert. Once a person reviews it and binds it
  as a workflow tool it runs like any other workflow — existing engine, full
  `ExecutionContext` and SDK, caller's normal permissions. **The gate is
  authorship and review, not a technical sandbox**, and nothing about a reviewed
  workflow stays second-class. WP8 (a sandboxed runtime) is deleted from the
  design; the `external` runner is the only untrusted path and is protocol-only.
- **Scope is every Solution entity type**, not just apps: apps, tables, files,
  workflows, forms, agents, events/schedules, configs, claims. Authoring is
  universal; *execution* of Python stays human-gated.

## Next, in order

Work-package numbers below refer to the design doc's
"Implementation work packages" section.

1. **Build plane (WP3).** Runner container (fixed toolchain, no credentials),
   build job protocol over RabbitMQ, staged artifacts, deploy moved off
   in-process `BackgroundTasks`. Ends with: a turn produces real entities and a
   preview that renders. **Everything else is blocked on this** — it is the
   reason a built Solution currently looks empty.
2. **Skill bundle prerequisites (WP4).** `Agent.bundle_path` (ORM, contracts,
   portable `ManifestAgent` field, manifest round-trip, CLI/MCP flags, and
   Solutions deploy carrying bundle files) plus `read_skill_asset` (reuse
   `WorkspaceRoot._resolve` in `fs_tools.py` as the traversal barrier). Both are
   unbuilt anywhere in the codebase and the design says they are unblocked.
3. **Builder agent execution (WP5).** Create the bundle-backed builder agent
   record, wire the workspace tools into `resolve_agent_tools()`, and retire
   `InternalLoopRuntime` / `BuilderAgentRuntime`.
4. **UX rebuild (WP7).** `/build` as a top-level nav destination (prompt box +
   your builds), app-first language throughout, `Edit in Builder` loading
   existing state with full history, live preview wired to the real app origin.
5. **`builder_model`.** Setting + AI-settings field. It selects the model for
   the builder *agent*, so that agent's `llm_model` is the natural home.

## Environment state (as of 2026-07-27, survives until torn down)

- **Dev stack** is UP for this worktree: `http://localhost:30463`
  (`dev@gobifrost.com` / `password`, port mode). `./debug.sh status` confirms.
- **OpenRouter is configured** as the platform LLM on that dev stack
  (provider `openai`, endpoint `https://openrouter.ai/api/v1`, model
  `anthropic/claude-sonnet-4.5`). The key came from 1Password item
  "OpenRouter Bifrost Production Key" (Integration Services vault) via
  `op read` and is encrypted in the stack DB — reconfigure the same way after
  a `./debug.sh down`.
- **Test stack** is UP (project `bifrost-test-ce8540c1`), template DB at
  migration `20260725_build_jobs`.
- A live-drive Solution `live-drive-tracker` (4 revisions, incl. an undo)
  exists on the dev stack — useful as an existing fixture, safe to delete.
- Scratch CLI venv: `/tmp/bifrost-cli-builder` (API-matched build, logged in).
- `BIFROST_APP_ORIGIN` is wired in both compose files but **unset** in the dev
  stack env, so the app host correctly reports unavailable there.

## Traps worth knowing

- **Never run raw `docker compose` in a worktree without
  `COMPOSE_PROJECT_NAME`** — it spawns a duplicate stack under the directory
  name and can drop the test database. Never pass `--no-deps` to a
  `run test-runner` (it severs DNS to postgres and produces bogus
  name-resolution errors).
- **Builder bookkeeping tables must stay in
  `_OPERATIONAL_SOLUTION_ROW_NAMES`** in `services/solutions/guard.py`, or the
  solution-managed `before_flush` guard rejects every builder write. This
  surfaced once as a mysterious 500 on promotion-request; the real blast radius
  was every turn, undo, and revision write.
- **`BIFROST_APP_ORIGIN`** must be set for anything app-host related; unset, the
  app host fails closed with 503 by design. It is wired into the test and debug
  compose files.
- The `api` container's exit-0 flake makes `./test.sh` refuse to run; bypass with
  `COMPOSE_PROJECT_NAME=<project> docker compose -f docker-compose.test.yml run
  --rm test-runner python -m pytest ...` (with deps).
