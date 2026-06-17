# Agent Skill Bundles + Capabilities — Converged Design

**Status:** Draft (supersedes Chat V2 sub-project 3 "Skills"; reframes sub-projects 4 "Artifacts" and 5 "Web Search")
**Author:** Jack Musick + Claude
**Date:** 2026-06-17
**Supersedes / folds in:**
- `docs/superpowers/specs/2026-04-27-chat-v2-program-design.md` sub-project (3) Skills — **superseded** by this doc.
- The 2026-06-13 "Agent Skill Bundles" brainstorm (Obsidian `Platform Overhaul/subplans/Agent Skill Bundles.md`) — **carried forward and revised**.
- Chat V2 sub-project (4) Artifacts — **reframed** here as a tool-return contract (the heavy three-tier subsystem is dropped).
- Chat V2 sub-project (5) Web Search — **reframed** here as a default-injected tool + a community skill bundle, not a bespoke sub-project.

## The thesis

Three Chat V2 sub-projects (Skills, Artifacts, Web Search) were scoped as separate subsystems. Two design conversations collapsed them into **one primitive**:

> **Capabilities are tools. Agents are skill bundles. Some tools return a renderable artifact contract.**

Concretely:

1. **An agent already *is* a skill** — it has a prompt body, tool bindings, knowledge, and runtime settings. We make that explicit and portable as an Agent-Skill-spec-compatible bundle. There is **no separate "user authors a Skill" concept** to build: the agent authoring surface (CLI / MCP / UI) *is* the skill authoring surface. This is the supersession of sub-project 3.

2. **Web search, document generation, and friends are not platform features** — they are tools. Core ships a thin always-on baseline (default chat/agent tools), and richer capabilities are **rolled by orgs/community as workflow tools or skill bundles**. This is the reframe of sub-project 5 (and the binary half of 4).

3. **Artifacts are a tool-return contract**, not a content-block subsystem. Any tool/skill can return files (+ optional inert preview); core renders them uniformly. This is the reframe of sub-project 4.

The win: instead of building four subsystems with four sets of plumbing, we build **one capability/return contract** and let the existing agent + tool + Solutions machinery carry it.

---

## Part A — Agents are skill-compatible bundles

### A.1 Today's reality (verified against the code)

An agent (`api/src/models/orm/agents.py`) already carries everything a skill needs:

| Skill concept | Agent field today | Binding mechanism |
|---|---|---|
| Instruction body | `system_prompt` (Text) | inline |
| Tools | `tools` (M2M → Workflow via `agent_tools`) | FK |
| System tools | `system_tools` (ARRAY[str]) | static registry by name |
| MCP grants | `mcp_connections` (M2M via `agent_mcp_connections`) | FK |
| Delegation | `delegated_agents` (self M2M) | FK |
| Knowledge | `knowledge_sources` (ARRAY[str]) | namespace refs |
| Model / budget | `llm_model`, `llm_max_tokens`, `max_iterations`, `max_token_budget`, `max_run_timeout` | inline |
| Identity/scope | `access_level`, `organization_id`, `roles`, `owner_user_id` | env-specific |

The manifest layer (`manifest_generator.serialize_agent` → `ManifestAgent`) already splits **portable content** (`system_prompt`, `tool_ids`, `system_tools`, `mcp_connection_ids`, `delegated_agent_ids`, `knowledge_sources`, `llm_*`) from **env-specific** (`access_level`, `organization_id`, `roles`, `created_by`, timestamps, `owner_user_id`). That split is exactly the Agent-Skills "portable SKILL.md content vs. local bindings" boundary.

**There is no platform `Skill` model today** (confirmed). `api/bifrost/skill.py` is a CLI dev-environment tool (installs `.claude/skills/<name>/` from the Bifrost repo) — unrelated. So we are not reconciling two platform concepts; we are giving agents a bundle representation.

### A.2 The bundle shape

```
agents/ticket-triage/
  SKILL.md          # frontmatter (name, description) + body == agent.system_prompt
  references/       # companion docs the agent may read
  scripts/          # portable scripts (may be inert; see A.5)
  assets/           # other bundle files
```

`SKILL.md` is **derived from the bundle root**, not the source of truth field-by-field. Export prepends Agent-Skills frontmatter to `system_prompt`:

```md
---
name: ticket-triage
description: Triage incoming HaloPSA tickets
---

<agent.system_prompt>
```

Bifrost runtime bindings (tool_ids, mcp_connection_ids, knowledge_sources, model/budget) live in the **bundle's manifest entry** (the `.bifrost/agents.yaml` inline content we already generate), NOT inside SKILL.md — so a SKILL.md stays portable across tools (Claude Code, Codex, etc.) while Bifrost-specific wiring rides alongside. This matches the existing portable/env-specific split verbatim; we are just adding a file projection of it.

**Decision (carried from 2026-06-13):** prefer a `bundle_path` (bundle root) over pointing at SKILL.md directly. SKILL.md is derived from the root.

### A.3 Storage & locality — reuse Solutions, do not invent

Solutions (merged #347) already defines installable bundle locality:
- `repo_subpath` on `Solution` ("subfolder within the connected repo holding this solution") — the omni-repo pattern.
- `solution_id`-scoped entity management with `uuid5(install_id, manifest_id)` remapping and full-replace reconcile (`WHERE solution_id = sid AND id NOT IN bundle_ids`).
- `SolutionStorage` writing source under `_solutions/{solution_id}/`.

An agent skill bundle is **a bundle path under the repo/solution** the same way an app's `path` points at `apps/<name>`. Bundle files (`references/`, `scripts/`, `assets/`) are read from the same S3 source of truth (`RepoStorage`/`SolutionStorage`), never `get_module()` (that's Python-only).

**We add `bundle_path` to the Agent record + `ManifestAgent`** (portable). When set, `references/scripts/assets` are resolved relative to it. When unset, the agent is "inline-only" (today's behavior) — fully backward compatible.

### A.4 Runtime capabilities

Bifrost injects a small contract into the agent prompt when a bundle is present:

```
You are backed by a skill bundle. Follow SKILL.md naturally.
When it references a relative file, use read_skill_asset.
If script execution is enabled, you may run bundled or temporary scripts with execute_script.
```

Two new **invisible capabilities** hook into `resolve_agent_tools()` (`api/src/services/execution/agent_helpers.py`) as system tools:

- **`read_skill_asset(path)`** — resolves only inside the agent's `bundle_path` root (path-traversal guarded; reuse the CodeQL-recognized realpath+startswith barrier — see `reference_codeql_recognized_barriers`). Reads from S3 source of truth. Available whenever `bundle_path` is set.
- **`execute_script(...)`** — available only when `script_execution != disabled` (A.5). Runs through the execution path.

### A.5 Script execution modes (carried from 2026-06-13, unchanged)

- **`disabled`** (default for imported/community bundles) — scripts are inert files; `read_skill_asset` still works.
- **`trusted`** — scripts run through the **existing Bifrost execution engine** with `ExecutionContext`, SDK access, logs, audit, timeout, same caller/org permission model as workflow code. (NOT the sandbox — trusted == full workflow trust.)
- **`external`** — future: a configured external runner (Azure Functions / Cloud Run / Lambda / local Docker / Firecracker) gets script text + input JSON, runs with no Bifrost credentials, returns stdout/stderr/result. **Protocol design only** unless explicitly prioritized; do not hard-code Azure as the product surface.

**We do NOT promise an OSS built-in sandbox.** Strong sandboxing is the separate Code Execution sub-project (bwrap); see B below. A bundle script that needs untrusted isolation runs in **`sandbox-*` runtime** once Code Execution lands — at which point `execute_script` gains a `runtime` selector (`workflow` = trusted today; `sandbox-python`/`sandbox-node` = isolated). Until then, `trusted` and `external` are the honest options.

### A.6 Import/export boundary (carried from 2026-06-13)

- Export keeps the whole bundle including `scripts/`.
- Import/create-from-bundle: create/update the agent from SKILL.md + manifest metadata, preserve companion files if bundle storage exists, **default `script_execution: disabled`**.
- Admins explicitly opt a bundle up to `trusted` (or configure `external`) after review.

### A.7 What chat "Skills" becomes

Chat V2 sub-project 3 wanted a separate Skill registry with global/org/role/personal scoping and a loader. **That is just agents**:
- Scoping is already global/org/role + personal (via `owner_user_id` + `access_level`) — identical four-tier model.
- "Load a skill into a chat" becomes "attach/select an agent (bundle) in the workspace" — `Workspace.enabled_*` already gates this; we add agents-as-skills to that surface.
- The `skill_loaded`/`skill_invoked` wire chunks the program spec anticipated become **delegation/tool chunks we already emit** (or M6's `delegation_*`), not a new subsystem.

Net: **delete sub-project 3 as separate work.** Its value is delivered by A.1–A.6 + the existing workspace/agent attach surface.

---

## Part B — Code Execution stays its own sub-project (the real sandbox)

This design does **not** absorb Code Execution. The bwrap sandbox (program spec §3, findings doc `2026-04-27-chat-v2-sandbox-bwrap-findings.md`) remains sub-project 2 and is the substrate for:
- Untrusted skill scripts (`script_execution` running in `sandbox-*` runtime).
- Server-side **file generation** for artifacts (Part C) — `pandoc`/`soffice`/`pdftoppm`/`npm`-backed doc generation runs in the sandbox, never the trusted pool.

The relationship: **Part A gives skills a place to declare scripts; Part B gives untrusted scripts a safe runtime; Part C gives their file output a render contract.** Three layers, one story.

---

## Part C — Artifacts as a tool-return contract (reframes sub-project 4)

### C.1 The decision

Drop the spec's three-tier artifact *subsystem*. Replace with: **any tool/skill may return an artifact contract alongside its normal result.** Core ships one renderer + one panel.

**v1 is file-first** (Jack's call, 2026-06-17): the artifact is fundamentally **files**, with optional **inert previews** for kinds the browser renders natively and safely. No executable rendering in v1.

### C.2 The contract

A tool result may include (in addition to its text result the model reads):

```jsonc
artifact: {
  title?: string,
  // inline preview — INERT kinds only in v1:
  preview?: {
    kind: "markdown" | "image" | "pdf" | "csv",   // browser-native, safe
    content_ref?: string,    // for image/pdf/csv: points at one of files[] by name
    inline?: string          // for markdown: the text itself
  },
  files: [
    { name: string, content_type: string, size: int, sha256: string }
    // METADATA ONLY — never URLs (see C.3)
  ]
}
```

- `markdown` renders directly (sanitized markdown → no raw HTML execution).
- `image` / `pdf` / `csv` render natively (`<img>`, pdf.js/`<embed>`, a table component) from the file's scoped URL.
- **No `html` / `svg` / `react` kind in v1.** (See C.4.)

### C.3 Files: who persists, who signs, who authorizes — the load-bearing rule

**A tool returning a `url` would be wrong**, for two reasons:
1. A sandboxed tool has **zero credentials/SDK** (program decision #4) — it cannot mint a signed URL.
2. A baked-in URL bypasses per-request authorization.

So the contract carries **file metadata only**. The flow:

1. Tool writes output files to its working dir (`/work` in the sandbox; the workflow tmp dir in trusted mode).
2. The **trusted layer** (agent executor / execution engine, outside any sandbox) collects them, persists to S3 under the conversation — **reuse the M4 attachment storage path** (`_attachments/{conversation_id}/...` or a sibling `_artifacts/{conversation_id}/...`) and the same signed-URL machinery.
3. The **API mints scoped, expiring, org/role-checked download URLs at render time**, per request — never stored in the artifact, never trusted from the tool.

This makes the artifact **output** path the mirror image of the attachment **input** path (M4). They share storage + signed-URL + content-type handling. That symmetry is why M4 landing first is the right sequencing.

### C.4 Executable rendering (html/svg/react) — deferred, and how it would work

Deferred per Jack (2026-06-17): file-first covers ~90% of MSP value (generate the report → download docx/pdf/xlsx). Executable rendering is the *least* MSP-relevant tier and the most security/maintenance cost. Documented here so a future tier doesn't re-derive it:

- **html / svg (a later v2):** render the model-authored source in a **null-origin `<iframe sandbox="allow-scripts">`** with a strict CSP. Never inserted into the app DOM (stored-XSS in the Bifrost origin otherwise). This is a *frontend* sandbox, unrelated to the bwrap sandbox.
- **inline React (a possible v3, probably skip):** Claude.ai's mechanism, for the record — the model emits JSX **source text**; the browser loads a prebuilt iframe harness (React + ReactDOM + Babel-standalone + a **pinned library allowlist**, e.g. Recharts/lucide/Tailwind-CDN); the harness `Babel.transform()`s the source at runtime and mounts it; `import` is shimmed to the allowlist (no live npm). The boundary is the iframe origin, not code review. **It is not a monolithic HTML page and not server-rendered.** Cost: build + maintain the harness and pin/curate the library set. Verdict: high effort, low MSP relevance — document, don't commit.

### C.5 Durability

v1 artifact = a content block on a message, rendered from the tool-return contract; files are durable in S3, preview is derived. The spec's "edit-in-place, workspace-homed `Workspace.artifacts`" ambition becomes a **v2 promotion**: a rendered artifact can be promoted into a stored, named object. Don't build the heavy version first.

---

## Part D — Default capabilities & web search (reframes sub-project 5)

### D.1 The decision (Jack, 2026-06-17)

No bespoke web-search sub-project. Instead:
- **A thin always-on baseline of default chat/agent tools** that every agent gets (the chat-product floor).
- **Everything richer is rolled by orgs/community** as workflow tools or skill bundles. A skill bundle that ships a `web_search` script *is* the web-search feature.

This is consistent with program decision #1 (first-party tools, not MCP-as-primary) and Part A (agents-are-skills).

### D.2 Default-injected tools

Define a small **default tool set** injected into every agent's `resolve_agent_tools()` result (gated by org policy), e.g.:
- `bifrost.fetch` — a constrained outbound HTTP fetch that runs **outside** the sandbox (program spec networking default) and returns response data the model can pass into a sandbox execution. SSRF-guarded (re.fullmatch allowlist per `reference_codeql_recognized_barriers`).
- Possibly a default search that an org points at **their own provider** config (the org owns the key) — but even this can be "just a bundled skill" rather than core.

The principle: **core ships the floor and the fetch primitive; orgs/community ship the rest as tools/bundles.** Web search, doc-gen, chart-rendering, etc. are all "a tool that may return an artifact (Part C)."

### D.3 What gets deleted as separate work

Sub-project 5 (Web Search) as a bespoke provider-abstraction subsystem: **gone**. Replaced by D.2's default tools + the open tool/bundle pattern.

---

## Consolidated effect on the Chat V2 program

| Original sub-project | Fate |
|---|---|
| (1) Chat UX | Unchanged. ~70% built; M4/M5/M6/M7 gaps in flight separately. |
| (2) Code Execution | **Unchanged** — still its own sub-project (the bwrap sandbox). It is the runtime substrate for untrusted skill scripts (A.5) and artifact file generation (C). |
| (3) Skills | **Superseded by Part A.** Agents are skills; no separate registry/loader/scoping to build. |
| (4) Artifacts | **Reframed by Part C.** A tool-return contract (file-first + inert previews), not a three-tier subsystem. Executable rendering deferred. |
| (5) Web Search | **Reframed by Part D.** Default-injected tools + community bundles, not a provider-abstraction sub-project. |

This turns "four more subsystems" into "one capability/return contract + Code Execution + reuse of agents/Solutions/M4-storage."

---

## Implementation pieces (revised from 2026-06-13)

1. **Agent bundle metadata** — add `bundle_path` to `Agent` ORM + `AgentCreate/Update/Public` contracts + `ManifestAgent` (portable) + manifest_generator/github_sync round-trip + CLI/MCP flags. SKILL.md frontmatter parse + legacy export projection. *(Round-trip test in `test_manifest.py`; DTO parity in `test_dto_flags.py`; contract-version gate per CLAUDE.md.)*
2. **`read_skill_asset`** — system tool, bundle-root-scoped, realpath+startswith barrier, reads S3 source. Add to `get_system_tools()` + `resolve_agent_tools()`.
3. **`script_execution` field** (`disabled|trusted|external`) on Agent + prompt injection + UI trust warning. Imported bundles default `disabled`.
4. **`execute_script` (trusted)** — through the existing engine path with ExecutionContext. Gains a `runtime` selector once (2) Code Execution lands.
5. **Artifact contract (Part C)** — tool-result schema extension; trusted-layer file persistence reusing M4 storage; API render-time signed URLs; frontend renderer for markdown/image/pdf/csv + the artifact panel.
6. **Default tools (Part D)** — `bifrost.fetch` (SSRF-guarded, outside sandbox) + the default-tool injection point in `resolve_agent_tools()`, org-policy gated.
7. **External runner protocol** — design separately before any `external` implementation.

### Sequencing
- **(1) bundle metadata + (2) read_skill_asset** can start now — pure agent/manifest work, no sandbox dependency. Best branched from the Solutions-merged main so `bundle_path` lines up with `repo_subpath`/SolutionStorage.
- **Artifact contract (5)** — design now; the **file-first inert path can ship against trusted workflow tools today** (no Code Execution dependency), reusing M4 storage. Sandbox-backed generation waits on (2).
- **`execute_script` sandbox runtime, untrusted skill scripts, sandbox-generated binary artifacts** — gated on Code Execution (2).
- **`external` runner** — protocol-only until explicitly prioritized.

## Open questions

- **Bundle vs. Solution overlap.** A skill bundle and a single-agent Solution are nearly the same shape. Decide whether agent bundles are a *kind of* Solution (reuse `solution_id` scoping + deploy reconcile) or a parallel-but-aligned concept. Leaning: reuse the Solutions locality/remap machinery; an agent bundle is the degenerate one-agent case.
- **`bifrost.fetch` default-on or default-off per org.** Outbound HTTP from agents is a real egress posture decision; default-off + explicit org enable is the safer call.
- **Artifact storage prefix** — share `_attachments/` with M4 or use a sibling `_artifacts/`. Sibling is cleaner for lifecycle (artifacts may outlive the message; attachments are message-bound).

## Picking this back up

Read Part A first — it's the spine (agents are skills, no separate Skills build). Parts C and D are the "capabilities are tools" corollaries that delete sub-projects 4 and 5 as separate work. Part B (Code Execution) is untouched and remains the sandbox sub-project. The 2026-06-13 brainstorm's MVP honesty still holds: skill-compatible bundles + `read_skill_asset` + `script_execution: disabled|trusted` are the core; `external` and sandbox-runtime scripts are later.
