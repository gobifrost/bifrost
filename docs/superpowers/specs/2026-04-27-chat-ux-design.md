# Chat UX Overhaul — Design Spec

**Sub-project:** (1) of the Chat V2 program
**Status:** Design draft
**Author:** Jack Musick + Claude
**Date:** 2026-04-27
**Program spec:** `2026-04-27-chat-v2-program-design.md`
**Master plan:** `../plans/2026-04-27-chat-v2-master-plan.md`

## Goal

Bring the Bifrost chat experience to feature parity with Claude.ai for everyday use. Add the foundational UX primitives — workspaces, attachments, lossless compaction, model curation — that the rest of the Chat V2 program (Code Execution, Skills, Artifacts, Web Search) will build on.

## Non-goals

- Voice input/output.
- Conversation-level full-text search (sidebar filter only in v1).
- Auto-routing between models (user picks; admin curates).
- Real-time collaboration (multiple users in one chat).
- Skills, Artifacts, Code Execution surfaces — those are later sub-projects, but this spec leaves the seams.

## Scope summary

| Feature | Shape | Section |
|---|---|---|
| Edit user message + retry | Edit and retry both branch — old version preserved, sibling navigation | §1 |
| Workspaces | First-class scoped destinations (not sidebar folders) | §2 |
| Tool layering | Workspace ∩ Agent (intersection) | §2.4 |
| Attachments | Files only — images, PDFs, CSVs, text, screenshots | §3 |
| Lossless compaction | Auto at threshold + manual button; DB unchanged | §4 |
| Context budget indicator | Per-model-aware tokens + symbolic cost tier badges | §4.2, §5.5 |
| Model resolver | Shared infrastructure; allowlist chain with provenance | §5 |
| Per-conversation instruction override | Hidden behind settings; not a primary affordance | §6 |
| Multi-agent within turn | Within-turn delegation via existing `delegated_agent_ids` | §7 |
| Conversation rename/delete/export | Inline rename, soft-delete, markdown/JSON export | §8 |
| Sidebar search | Title + last-message-preview filter only | §9 |
| **UX surfaces** | Sidebar / workspace mode / floating composer / model picker / etc. | §16 |

## 1. Edit user message + retry

### 1.1 Edit user message
Editing creates a **sibling user message** under the same parent. The original message and any subsequent assistant replies remain in the database; the active branch flips to the new sibling. Both branches are accessible via sibling navigation arrows (`< 2/2 >`).

UI: pencil icon on user messages on hover. Click → message becomes editable inline. Submit creates the sibling and runs a new turn. Cancel reverts. **No confirmation dialog** — edit is non-destructive.

Implementation: every Message row carries `parent_message_id`; every Conversation carries `active_leaf_message_id`. The edit endpoint creates a new user Message with the same parent as the original and updates `active_leaf_message_id`. The agent loop's `_load_active_branch` walks the parent chain from the leaf to load the active path.

### 1.2 Retry last response
Regenerates an assistant message. The new response is created as a **sibling** under the same user message; the original is preserved and accessible via sibling navigation.

UI: refresh icon on the assistant message. Single click → retry with the conversation's current model. **No model override dropdown.** Per-conversation model switching happens via the chat header's model picker (M2).

Implementation: the retry endpoint walks `active_leaf_message_id` back to the user message that prompted the target assistant message, then runs a fresh turn. The new assistant message is saved as a sibling of the original.

## 2. Workspaces

### 2.1 What a workspace is
A workspace is a folder for chats plus optional shared configuration (instructions, tools, knowledge sources, default agent, default model). **Workspaces are explicit and optional.** A chat may belong to a workspace OR live in the **general pool** — the unscoped default chat list reachable from `/chat`. There is no synthetic "Personal" workspace; users create private workspaces explicitly when they want one.

### 2.2 Workspace fields
| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | |
| `name` | str | yes | |
| `description` | str | no | |
| `scope` | enum: `personal` / `org` / `role` | yes | UI labels these as **Private** / **Shared (org)** / **Shared (role)** |
| `role_id` | UUID | yes if scope=role | |
| `organization_id` | UUID | yes if scope=org/role | |
| `user_id` | UUID | yes if scope=personal | Owner of the private workspace |
| `default_agent_id` | UUID | no | Workspace's default agent for new chats |
| `enabled_tool_ids` | list[UUID] | no | If set, tools available in this workspace are intersected with agent's tools (see §2.4) |
| `enabled_knowledge_source_ids` | list[UUID] | no | Knowledge sources added to model context for chats in this workspace |
| `instructions` | text | no | Free-text appended to system prompt for chats in this workspace |
| `default_model` | str | no | See §5 |
| `allowed_models` | list[str] | no | See §5 |
| `created_by` | str | yes | Email of the creator |
| `created_at` / `updated_at` | datetime | yes | |
| `is_active` | bool | yes | Soft-delete |

### 2.3 Scope semantics
- **personal** (UI: "Private"): visible only to `user_id`. Users may create as many private workspaces as they like.
- **org** (UI: "Shared with my organization"): visible to anyone in `organization_id`. The default Shared option.
- **role** (UI: "Shared with a role"): visible to members of `role_id`. Always also belongs to an organization.

Mirrors the existing scoping model used by forms, agents, workflows, tools. Permissions match agents: org users can create/manage workspaces in their own org; platform admins can target any org.

### 2.4 Tool intersection rule
When a chat runs in a workspace, the **effective tool set** for an agent is `agent.tool_ids ∩ workspace.enabled_tool_ids` (if `enabled_tool_ids` is set; otherwise just `agent.tool_ids`). Workspaces can restrict but **never expand** an agent's tool set. This gives admins predictable safety guarantees: if a workspace says "no code execution," that's true regardless of which agent the user picks.

### 2.5 Workspace UI
- A `Workspaces` destination in the sidebar's primary nav opens `/workspaces` — a directory of every workspace the user can see.
- Clicking a workspace enters **workspace mode**: the URL becomes `/chat?workspace=<id>`, the sidebar's `Workspaces` row swaps for a workspace-identity row (gear + `×` exit), the chat list filters to that workspace, and a right-rail context view shows the workspace's defaults (default agent, instructions, knowledge, tools).
- Workspace settings open in a Sheet from either the sidebar identity row's gear or the right-rail's edit affordance.
- Chats are movable between workspaces via the chat row's overflow menu ("Move to" → workspace, or → "General chats" to remove from any workspace).
- Soft-delete is available for any workspace the user can manage (admins for org/role; owner for private). Deleted workspaces don't move their chats — chats keep their `workspace_id` until moved.

### 2.6 Conversation default
When the user starts a new chat from the unscoped sidebar (no workspace active), it lands in the **general pool** (`workspace_id = null`). When the user starts a new chat from inside a workspace, it lands in that workspace. The chat URL (`/chat/:conversationId`) is unaffected — workspace membership is metadata, not URL structure. Sidebar filters:

- Unscoped sidebar Recent: `workspace_id IS NULL` only.
- Workspace mode Recent: `workspace_id = <active>`.
- Search (a future M7 polish item) is the only surface that crosses both.

## 3. Attachments

### 3.1 Supported types
- **Images**: PNG, JPEG, WebP, GIF — sent as vision content blocks if the model supports them.
- **PDFs**: text-extracted server-side; first page rendered as preview thumbnail.
- **CSVs**: parsed; first ~20 rows shown as preview; full contents available to the model as text.
- **Text files**: `.txt`, `.md`, `.json`, `.yaml`, code files. Inline.

### 3.2 Upload flow
- Drag-and-drop into the chat input area, or paperclip button.
- Reuses existing `client/src/stores/uploadStore.ts` plumbing.
- Files upload to S3 under `_attachments/{conversation_id}/{uuid}_{filename}`.
- Per-attachment limits: 25 MB per file, 5 files per message. Total per conversation: configurable per org, default 500 MB.

### 3.3 Wire format
New `ChatStreamChunk` types are not needed for attachments themselves — they're part of the user message at send time, not a streaming chunk. The Message ORM gets a new `attachments` relationship to a new `MessageAttachment` table:

| Field | Type |
|---|---|
| `id` | UUID |
| `message_id` | UUID FK |
| `s3_key` | str |
| `filename` | str |
| `content_type` | str |
| `size_bytes` | int |
| `extracted_text` | text (nullable; for PDFs and the like) |
| `created_at` | datetime |

### 3.4 What attachments aren't
Bifrost-internal entities (tickets, workflow runs, form submissions, table rows) are **not** attachments. Those reach the agent through knowledge sources or tools, not through the attachment system. This keeps the attachment plumbing simple and reserves "attachment" for "the user picked a file from outside Bifrost."

## 4. Compaction & context management

### 4.1 Lossless compaction
Today, when a conversation exceeds 120k tokens, old messages are *deleted* from the model's context. For Chat V2:

- The **database is the source of truth and is never modified by compaction.** All original messages remain visible in the conversation scrollback.
- When the working context exceeds the threshold (some fraction — say 85% — of the current model's context window), the older messages are **summarized into a `[Conversation history summary]` block** which replaces them in the *model's working context only*.
- The summary is generated by a small auxiliary call to a cheap model (the summarizer call uses the model resolver to pick — typically the org's "fast" tier model).
- Result: the model sees a coherent summary plus recent turns; the user still sees their full conversation.

### 4.2 Per-model-aware threshold
The 120k constant becomes per-model. The model resolver (§5) exposes the model's `context_window_tokens` field; the auto-compaction threshold is `0.85 * context_window_tokens`. Switching to a smaller model mid-conversation can immediately put the working context over budget — in that case, compaction runs at the next turn (or earlier if explicitly invoked).

### 4.3 Manual compaction button
"Compact older turns" button in the chat header. Visible always; suggested visually when the budget indicator approaches 70%. Click → runs the same summarization the auto path would, immediately. User feedback: "Compacted N earlier turns into a summary."

### 4.4 Tool output protection
The existing `TOOL_OUTPUT_PROTECT_TOKENS=10000` heuristic carries over: recent tool outputs are kept verbatim during compaction (within a budget). Older tool outputs get summarized along with the surrounding messages.

### 4.5 What compaction sees
A compacted block in working context looks like:

```
[Conversation history summary, covering turns 1-12 (~28k tokens originally):
- User asked about onboarding flow for new MSP clients...
- Assistant explained step-by-step process and provided a checklist...
- Tool calls: created_ticket, sent_email...
- ...]
```

Recent N turns (where N is determined by remaining budget) are kept verbatim.

## 5. Model resolver — shared infrastructure

### 5.1 Why shared
Today, model selection happens ad-hoc in chat (`agent_executor.py`), in workflows (model passed via SDK), in summarization (hardcoded). Chat V2 introduces multi-level model curation. Doing this once, correctly, in a shared utility avoids drift and makes future sub-projects (Skills, Artifacts) inherit the right behavior for free.

### 5.2 Resolver location
`api/shared/model_resolver.py` — exports `resolve_model(context: ModelResolutionContext) -> ModelChoice`.

### 5.3 The chain
Most-specific wins:

```
Platform allowlist  (Bifrost ships supporting these)
        ↓ intersect
Org allowlist       (admin's chosen subset)
        ↓ intersect
Role allowlist      (optional)
        ↓ intersect
Workspace allowlist (optional)
        ↓ intersect
Conversation override (optional, set when user picks via picker)
        ↓ intersect (effectively a single choice at this point)
Message override    (optional, used by retry-with-different-model)
```

At each level, two fields:
- `allowed_models: list[str] | None` — narrows the set
- `default_model: str | None` — picks from the current allowed set

Resolution: walk top-down, intersect allowlists, then walk bottom-up to find the most specific `default_model` that's still in the allowed set. If no level specifies a default, use the org default; if no org default, the platform default.

### 5.4 Used by
v1: chat (this sub-project), summarization-on-compaction (this sub-project's compaction logic).
Future: workflows, skills, artifacts, web search — they all gain it for free by calling the same resolver.

### 5.5 UI: provenance for restricted models
The model picker in chat shows the user's full allowed set (after intersection). Models that *would* exist at a higher level but were restricted at a lower level are shown grayed out with a tooltip naming the restricting level:

- "Claude Opus 4.7 — restricted by your org admin"
- "GPT-5o — restricted by this workspace ('Customer Replies' allows only Haiku and Sonnet)"
- "Llama 3.3 — restricted by your role (Help Desk)"
- "Mistral Large — not enabled on this Bifrost installation"

The picker UI is the OpenRouter-style affordance: visible-but-disabled options communicate availability and access controls in the same view.

### 5.6 Cost surfacing — symbolic, not dollars
Each model in the platform registry has a `cost_tier: "fast" | "balanced" | "premium"` field, with glyphs ⚡ / ⚖ / 💎. Admins can override the tier when adding a model to the org allowlist (e.g., "we treat Sonnet as our balanced default, even though Anthropic prices it as premium").

In the chat UI:
- Model picker shows tier glyph next to each model name.
- Per-message footer on the assistant response shows which tier handled it.
- Per-conversation header shows aggregate ("⚡ ⚡ ⚖ ⚖ 💎 across 5 messages").
- **No dollar amounts in the user-facing chat.** Dollars exist in the admin cost dashboard (`AIUsage` is already there); chat is anxiety-free.

### 5.7 Platform model registry
Platform-level model list lives in `api/shared/model_registry.py` — a static (initially) module that lists every model Bifrost knows about, with: provider, model_id, display_name, context_window_tokens, default_cost_tier, capabilities (vision, tool_use, etc.). Org admins pick from this list when configuring their allowlist.

Future: this becomes dynamic (orgs can register custom-hosted models, e.g., self-hosted Llama via vLLM endpoint). v1 is static.

### 5.8 Provider/model migration — guarding against deprecation

Real-world: providers retire models. Customers switch providers. Anthropic deprecates Claude 3.5 Sonnet → Claude Sonnet 4.6 → Claude Sonnet 5. If we store raw provider model IDs in workspaces, orgs, roles, agents, conversations, and workflow code, every deprecation is a multi-table find-and-replace nightmare with no safe automation. Two layers prevent that.

**5.8.1 Logical model aliases (primary handle).**

The model registry includes first-class logical aliases that point at real models:

| Logical alias | Description | Current target |
|---|---|---|
| `bifrost-fast` | Cheap, fast, suitable for short-context tasks | `claude-haiku-4-5` |
| `bifrost-balanced` | General-purpose default | `claude-sonnet-4-6` |
| `bifrost-premium` | Highest quality, most expensive | `claude-opus-4-7` |
| `bifrost-vision-fast` | Cheap model with vision support | `claude-haiku-4-5` |
| (orgs can define their own aliases too, e.g., `acme-default`) | | |

Workspaces, roles, orgs, and agents reference aliases by default in their `default_model` / `allowed_models` fields. The model picker UI shows both ("⚖ Balanced — Claude Sonnet 4.6") so the alias is the *stable handle* and the underlying real model is *visible*. When Anthropic deprecates Sonnet 4.6, an admin (or Bifrost upstream releases) updates `bifrost-balanced`'s target to the new model. **Everything downstream keeps working with no row rewrites.**

Aliases live in the platform registry plus an `org_model_aliases` table for org-defined ones. The resolver follows alias → target as part of the lookup chain.

**5.8.2 Raw model IDs are still valid.**

An org or user who wants to pin to a specific raw model ID (e.g., for compliance reasons — "we're approved to use Sonnet 4.6 specifically") can do that. They then own the migration when that model deprecates.

**5.8.3 Deprecation remap table.**

The platform ships a `model_deprecations` registry — a list of `{old_model_id, new_model_id, deprecated_at}` entries maintained upstream. The resolver applies remaps at *lookup time* (not by rewriting stored data). When `claude-3-5-sonnet-20240620` is deprecated and remapped to `claude-sonnet-4-6`:

- A workspace whose `default_model` is `claude-3-5-sonnet-20240620` resolves to `claude-sonnet-4-6` automatically.
- The picker shows the deprecation explicitly: "Claude 3.5 Sonnet (deprecated → using Claude Sonnet 4.6)" so admins know to update.
- Optional: a background sweep rewrites stored references after a grace period (e.g., 90 days post-deprecation) so the lookup-time indirection doesn't accumulate forever. Configurable per-org.

**Who maintains what:**
- *Platform-wide remaps* are maintained by Bifrost upstream as part of the model registry. New deprecations ship with Bifrost releases. Self-hosters automatically pick up new entries on upgrade.
- *Org-level remaps* (`org_id` set on the row) are managed by org admins via the admin UI. They override platform-wide remaps for that org. Use case: "we don't use Anthropic anymore, redirect every Anthropic model ID to our self-hosted Llama."
- The resolver applies *org-level first*, then *platform-wide*, so an org override wins.

**5.8.4 `Message.model` is immutable history.**

The `model` field on a Message records *what actually handled this turn at the time*. It is **never remapped or migrated**. "This message was handled by `claude-3-5-sonnet-20240620` on 2025-08-15" is an audit fact, not a configuration. The deprecation remap applies only to *configuration* fields:

| Field | Subject to alias resolution? | Subject to deprecation remap? |
|---|---|---|
| `Org.default_chat_model` | yes | yes |
| `Org.allowed_chat_models[]` | yes | yes |
| `Role.default_chat_model` | yes | yes |
| `Role.allowed_chat_models[]` | yes | yes |
| `Workspace.default_model` | yes | yes |
| `Workspace.allowed_models[]` | yes | yes |
| `Conversation.current_model` | yes | yes |
| `Agent.default_model` | yes | yes |
| `Message.model` | NO (historical record) | NO (historical record) |

**5.8.5 Save-time validation in AI settings (the safety net).**

The aliases and deprecation table cover the *expected* migration paths. The save-time validator catches the *unexpected* ones — admin changes that orphan model references without realizing it.

Triggering events:
- Admin removes a model from `Org.allowed_chat_models`.
- Admin switches the AI provider integration (OpenRouter → direct Anthropic, etc.) such that previously-reachable models are no longer reachable.
- Admin disables an integration that was the path to certain models.
- Admin deletes an org-level alias that has downstream references.

When any of these happens at save time, the system runs a **reference audit** that scans every place a model ID can be stored:

| Location | Field |
|---|---|
| Workspace | `default_model`, `allowed_models[]` |
| Role | `default_chat_model`, `allowed_chat_models[]` |
| Org | `default_chat_model`, `allowed_chat_models[]` |
| Conversation | `current_model` |
| Agent | `default_model` |
| Workflow code | grep for hardcoded model IDs (heuristic, surfaced as warnings) |

`Message.model` is **excluded** from the audit — it's immutable history (per §5.8.4).

If references to soon-to-be-orphaned models exist, the admin sees a remediation UI:

> ⚠ Saving these changes will orphan some model references:
> - **`minimax-m1`** is referenced in 3 places (1 workspace, 2 conversations). Choose a replacement: [picker, suggested default = closest available model in same cost tier]
> - **`gpt-4o`** is referenced in 5 places (1 role default, 4 conversations). Choose a replacement: [picker]
>
> [ Cancel ] [ Apply replacements and save ]

On apply, the chosen replacements are written *both* to the affected rows AND to the org-level deprecation remap table so any future references to the old IDs (e.g., from a workflow that hadn't been updated yet) also resolve. This dual-write is intentional — the row rewrite cleans up *known* references, the remap entry catches *unknown* ones (workflow strings, conversation overrides set after the migration started, etc.).

**Suggested replacement heuristic:** for each orphaned model, find the closest available model in the same `cost_tier`. Tie-break by provider preference (an admin setting). The admin can override every suggestion individually before applying.

**API:** `POST /api/admin/models/migrate-references` takes `{replacements: {old_model_id: new_model_id, ...}}` and returns a summary of what was changed where. Idempotent — running twice with the same input is a no-op.

**5.8.6 Display name overrides.**

Independently of provider/migration concerns, an org admin can override the user-facing display name of any model. Useful for white-labeling or for using internal language ("Acme Pro" instead of "Claude Opus 4.7"). The override lives on the org's model entry; the actual provider model ID stays correct because that's what's used to call the API.

| Field | Purpose |
|---|---|
| `org.allowed_chat_models[].model_id` | Real provider ID — used for API calls |
| `org.allowed_chat_models[].display_name_override` | User-facing label, defaults to platform registry's display name |
| `org.allowed_chat_models[].cost_tier_override` | Optional, defaults to platform registry's tier |

This is a thin org-wide UX customization; doesn't intersect with the resolver's model-selection logic at all.

**5.8.7 Why this is better than the alternatives.**

Claude.ai and ChatGPT hide the model entirely. Power users have no idea what they're running. OpenRouter exposes raw IDs with no indirection — every change is a manual update. Vercel AI SDK has `customProvider` aliases but no curation, no per-org views, no save-time validation. Bifrost's aliases-with-visibility model plus the save-time orphan check is a real differentiator and the implementation cost is moderate (the audit logic is mostly SQL queries; the UI is a single modal at admin-save time).

## 6. Per-conversation custom instructions

A conversation gets an optional `instructions: text` field (separate from workspace.instructions). When set, it's appended to the system prompt for that conversation only.

UI: settings menu in conversation header → "Customize this chat" → text area. Not surfaced as a primary affordance; users who don't open the menu won't know it exists. Suitable for "this one chat needs to behave differently from the rest of my Customer Replies workspace."

When set, system prompt assembly is: `agent.system_prompt + workspace.instructions + conversation.instructions`. (Plus knowledge sources, tools schema, etc.)

## 7. Multi-agent within turn

The existing `Agent.delegated_agent_ids` field (declared, partially wired) gets fully wired into chat:

- An agent can call into a delegated agent during its own turn, like a tool call.
- The user sees: the primary agent's response, with a small "✓ consulted [Delegated Agent]" badge near the start. Optional expansion shows the delegated agent's contribution.
- The conversation's *active* agent stays the primary. Delegation is invisible to the conversation's continuity — the user keeps talking to Agent A.

This is distinct from `@-mention agent switching`, which **persistently** changes the conversation's active agent. Both behaviors exist:
- @-mention: "I want to talk to Agent B from now on" — switches.
- Delegation: "Agent A handles it, calling Agent B internally for one part" — invisible.

## 8. Conversation operations

### 8.1 Rename
Click the title in the sidebar to edit inline. Replaces the auto-generated title.

### 8.2 Delete
Soft-delete: sets `is_active=False`, hidden from the sidebar but the DB row is preserved. Hard-delete is admin-only.

### 8.3 Export
"Export" menu item per conversation:
- **Markdown** (default) — formatted message-by-message, with tool calls as collapsible code blocks.
- **JSON** — structured, suitable for re-importing or feeding to other tools.

Workspace-level export (zip of all conversations) is deferred to v2.

## 9. Search

### 9.1 v1 sidebar search
Sidebar gets a search box. Filters conversations by title and `last_message_preview`. Client-side filtering on already-loaded conversation summaries; cheap, no backend changes needed.

### 9.2 v2 (deferred)
Full-text search across message bodies and tool outputs. Requires Postgres FTS index on `messages.content` plus careful permission filtering. Out of scope for this sub-project but not blocked by it.

## 10. Wire format additions

The existing `ChatStreamChunk` union (`api/src/models/contracts/agents.py`) gets new chunk types:

- `compaction_started` — model context is being compacted; UI shows a brief "Compacting older turns..." indicator.
- `compaction_complete` — replaces the in-flight indicator with "Compacted N earlier turns" (passive).
- `delegation_started` — the active agent is delegating to another agent. Includes delegated agent's id/name. UI shows the "✓ consulted" badge being assembled.
- `delegation_complete` — delegated agent finished; result included.

The existing `context_warning` chunk stays but its semantics are now "compaction is approaching/imminent" rather than "messages will be deleted."

## 11. Data model changes

### 11.1 New tables

**workspaces** — fields per §2.2

**message_attachments** — fields per §3.3

**org_model_aliases** — per §5.8

| Field | Type |
|---|---|
| `id` | UUID |
| `org_id` | UUID FK |
| `alias` | str (e.g., `acme-default`) |
| `target_model_id` | str |
| `display_name` | str |
| `cost_tier` | enum |
| `created_at` / `updated_at` | datetime |

**model_deprecations** — per §5.8.3

| Field | Type |
|---|---|
| `old_model_id` | str (PK or unique) |
| `new_model_id` | str |
| `deprecated_at` | datetime |
| `org_id` | UUID NULL — null = platform-wide; set = org-specific override |
| `notes` | str (optional message shown in admin UI) |

### 11.2 Modified tables

**conversations**:
- ADD `workspace_id UUID NULL FK workspaces.id ON DELETE SET NULL` — null = general pool (unscoped chat list).
- ADD `instructions TEXT NULL` (per §6)
- ADD `current_model VARCHAR NULL` (per §5)

**messages**:
- ADD `model VARCHAR NULL` — already exists, repurposed: the actual model used for this turn.
- ADD `cost_tier VARCHAR NULL` — denormalized from model for easy aggregation.
- (No `parent_message_id` — linear only.)

**orgs** (existing):
- ADD `allowed_chat_models JSONB DEFAULT '[]'` — list of model IDs available for chat in this org.
- ADD `default_chat_model VARCHAR NULL`

**roles** (existing):
- ADD `allowed_chat_models JSONB NULL`
- ADD `default_chat_model VARCHAR NULL`

### 11.3 Migration plan
Single Alembic migration adds all the new tables and columns. No backfill needed for `conversations.workspace_id` — it's nullable, and existing chats land in the general pool (NULL). Existing messages have no `cost_tier`; populated lazily on next access via the model registry lookup. No existing data is destroyed.

## 12. Implementation phases (within this sub-project)

The Chat UX sub-project is itself sizable. To make it tractable in a worktree, split into shippable milestones:

### M1 — Foundations (~1.5 weeks)
- Workspace ORM + API + migration (`workspaces` table; `conversations.workspace_id` nullable FK).
- `/api/workspaces` CRUD with personal/org/role scopes; org-user permissions matching Agents.
- `/api/chat/conversations` PATCH for `workspace_id` (move-to-workspace).
- Sidebar primary nav: New chat / Workspaces destination / Toolbox + Artifacts placeholders.
- Workspace mode (sidebar identity row swap, right-rail context view, settings Sheet).
- `/workspaces` directory with search and inline edit/delete.
- Move-to-workspace affordance on every chat row.
- Chats default to the **general pool** (workspace_id IS NULL); workspaces are explicit.

### M2 — Model resolver + curation (~1 week)
- `api/shared/model_resolver.py` shared utility.
- Org/role/workspace model allowlist fields.
- Platform model registry with cost tiers.
- Chat picker UI with provenance tooltips.
- Conversation `current_model` set by picker; per-message override via retry dropdown.

### M3 — Edit + retry + per-conversation instructions (~0.5 week)
- Edit user message inline.
- Retry last assistant response with optional model override.
- Per-conversation `instructions` field + settings UI.

### M4 — Attachments (~1.5 weeks)
- Upload flow (drag/drop, paperclip, paste from clipboard).
- S3 storage; new `message_attachments` table.
- Server-side text extraction for PDFs.
- Image, PDF, CSV, text rendering in chat.
- Vision content blocks for image-capable models.

### M5 — Compaction (~1 week)
- Lossless summarization replacing the current pruning.
- Per-model-aware threshold via resolver.
- Manual compact button.
- New chunk types in wire format.

### M6 — Multi-agent delegation in chat (~0.5 week)
- Wire `delegated_agent_ids` into `agent_executor.py` as a callable from within a turn.
- New chunk types `delegation_started` / `delegation_complete`.
- "✓ consulted" badge + expandable detail.

### M7 — Polish (~1 week)
- Sidebar search.
- Conversation rename / soft-delete / export.
- Context budget indicator (real-time, model-aware).
- Cost tier badges per message.

**Estimated total: 6-7 weeks of focused work** (compared to the program spec's "4-6 weeks" estimate; the model resolver and workspaces work pushed it higher than I'd initially guessed).

## 13. Testing

Each milestone ships:
- Unit tests for backend logic (`api/tests/unit/`).
- E2E tests for new endpoints (`api/tests/e2e/`).
- Vitest tests for new frontend modules (workspace store, model picker logic, attachment upload helpers, etc.).
- Playwright tests for user-facing flows: create workspace, send chat with attachment, edit user message, switch model mid-conversation, manual compact.

The model resolver gets a thorough unit-test suite — it's the kind of thing where a subtle bug in the intersection logic ships a customer in the wrong tier. Test the chain at every level.

## 14. Cross-cutting concerns (program-wide reminders)

Per the program spec, this sub-project also addresses:

- **Permissions / org control**: workspaces respect scope (personal/org/role); model curation enforces org→role→workspace cascade; tool intersection ensures workspace can restrict but not expand.
- **Cost accounting**: cost tier surfaced in chat (symbolic), dollars in admin dashboard. Per-message tier saved via `messages.cost_tier`. Per-conversation aggregate is a SUM query.
- **Testing**: pre-completion verification (pyright, ruff, tsc, lint, full test suite) is mandatory before merging this sub-project to main.

## 15. Future-proofing for later sub-projects

What this spec leaves as seams for sub-projects (2)–(5):

- **(2) Code Execution** — the model resolver already exists; the new run_code tool will use it. Workspace `enabled_tool_ids` already gates tool availability per workspace. Code Execution becomes "just another tool" with a sandboxed backend (no chat plumbing changes).
- **(3) Skills** — new wire-format chunks (`skill_loaded`, `skill_invoked`) extend `ChatStreamChunk` cleanly. Workspaces can be extended with `enabled_skill_ids` paralleling `enabled_tool_ids` without schema disruption.
- **(4) Artifacts** — workspaces become the natural home; `Workspace.artifacts` relationship added later. Wire format gets new `artifact_created` / `artifact_updated` chunk types.
- **(5) Web Search** — same shape as Code Execution: a tool, gated by workspace's `enabled_tool_ids`. Can also be exposed at the workspace level (e.g., "this workspace's web searches use only the org's approved provider").

## 16. UX descriptions for major surfaces

These ground the spec in Bifrost's existing visual language (shadcn/ui + Tailwind, OKLch teal primary, Lucide icons, the existing chat header / sidebar / picker patterns). Where an existing component fits, it's named explicitly. Where a new pattern is needed, it's described — not constrained by what already exists, but consistent with the surrounding style.

### 16.1 Sidebar — primary nav, not workspace folders

Replaces today's flat `ChatSidebar` conversation list. Width unchanged (`w-72`/`w-80`). **Workspaces are not folders here.** Workspaces are a destination — the user navigates *into* one (see §16.2). The sidebar's job is fast access to chats and global navigation.

**Structure top-to-bottom (matches Claude.ai's sidebar shape):**

1. **Primary nav block.** Stacked rows, each `flex items-center gap-2.5 px-2.5 py-1.5 rounded-md`:
   - **+ New chat** (font-medium primary entry)
   - **Workspaces** (FolderKanban icon → workspaces directory page)
   - **Artifacts** (Sparkles icon → artifacts directory, when sub-project (4) lands)
   - **Customize** (Settings2 icon → user-level customization, instructions, etc.)

   Bordered below for visual separation.

2. **Search input.** `Input` with leading `Search` icon, height 8, `text-sm`. Filters across all chats by title and last-message-preview, client-side.

3. **Pinned section** (conditional). Heading row: `text-[10px] font-medium tracking-wider uppercase text-muted-foreground` reading "Pinned." Below: chat rows with a `Pin` icon (size-3, opacity 50% / 100% when active) instead of `MessageSquare`. Pins are user-scoped, max ~10 to avoid sidebar bloat.

4. **Recent section.** Same heading style, "Recent." Flat list of all chats the user has access to, sorted by `updated_at` desc. Each row uses `MessageSquare` icon, `font-medium` title, `text-xs muted-foreground` preview, hover-revealed timestamp on the right (`text-[10px] opacity-0 group-hover:opacity-70`).

5. **User block** at the bottom. Bordered above. Avatar + name + "{org} · {role}" subtitle. `MoreHorizontal` opens user/account/sign-out menu.

**No collapsible folders. No workspace tree.** The mental model is: "I have chats; some are pinned; one of them might be inside a workspace, but the workspace doesn't fragment the sidebar list." When the user enters a workspace, the sidebar re-scopes (§16.2).

**Pinning a chat** is one menu item on the chat row's overflow menu. Pinned chats appear in the Pinned section; unpinning moves them back to Recent in chronological position.

### 16.2 Workspace mode — the chat re-scoped, not a settings page

Workspaces aren't a sidebar folder structure. They're a *mode the chat enters*. When the user clicks a workspace from the Workspaces directory (or jumps to one via a chat that lives in it), the entire chat surface re-scopes:

**Sidebar changes (left):**
- A `← All chats` button replaces the primary nav block (one click to exit the workspace).
- Below: workspace identity card — small icon tile, name, scope badge (`Org` / `Role: Senior Tech` / no badge for personal), `N conversations` summary.
- Workspace-scoped action buttons: `+ New chat in this workspace`, `Workspace settings` (opens a Sheet for editing, see §16.3).
- Search input scoped to this workspace.
- Chat list now shows only this workspace's conversations (pinned ones grouped at top if any).
- User block stays at the bottom.

**Right rail appears (new):**
The chat surface gains a third pane on the right (`w-80`, `border-l`, `bg-card`) showing workspace context. Sections from top to bottom:
- **Workspace** header — name, description, "Edit" link top-right.
- **Default agent** — agent row (icon + name + "default" subtitle).
- **Instructions** — collapsed snippet (line-clamp-3) + "Show full" link, with token cost badge in the section header.
- **Knowledge** — list of sources, each with name + token cost.
- **Tools** — wrap of small `Badge`-styled tool name chips, plus a footnote on the intersection rule with the agent.
- **Models** — current default model row + a footnote on which models are restricted/permitted in this workspace.
- **Baseline cost** (bottom block, slightly emphasized) — total tokens / message with a breakdown.

The right rail is the OPPOSITE of a settings page: it's a *passive context view* showing what's already configured. Editing happens in the Workspace Settings Sheet (§16.3).

**Center pane (chat itself):** standard chat view with the workspace's context already loaded into the model — same component as the global chat, no UI difference except the floating composer's model pill defaults to the workspace's default model and the context budget indicator already includes the workspace's baseline.

**This re-scoping is the differentiator** vs. Copilot (where projects are storage-location based) and parity with Claude (whose Project view has a similar right-rail context pane). The combination of org/role/personal scoping, intersection-with-agent tool gating, and the visible baseline cost makes it more useful than either.

### 16.3 Workspace settings (the editable Sheet)

Triggered by `Workspace settings` in the workspace mode sidebar, or `Edit` in the right rail's Workspace header. Right-side `Sheet` (modeled on the existing `ExecutionDrawer`, `side="right"`, `max-w-2xl`). Tabs (existing `Tabs` component) match the global Settings page pattern:

- **General**: name, description, scope (read-only — scope is set at creation, not editable later), default agent (Combobox of agents the workspace's audience has access to).
- **Tools**: MultiCombobox of available tools, with chip count. Help text below: "If set, only these tools are available in this workspace. The agent's tools must include each enabled tool — tools you select here that the agent doesn't have are still hidden in chats." Chips show a warning glyph for any selected tool the chosen default agent doesn't have access to (so admins see the intersection at config time).
- **Knowledge**: MultiCombobox of knowledge sources, same shape.
- **Instructions**: textarea (auto-resize), labeled "Custom instructions appended to system prompt." Below: "**Baseline cost: ~3.2k tokens / message**" — small text-xs muted line, computed live as the admin types, summing instructions + per-message overhead from knowledge + tools schema.
- **Models**: model resolver UI for this workspace (see §16.6).

Footer: "Save" (default) and "Cancel" (outline). Changes are debounced/preview-able before save.

### 16.4 Floating composer

Replaces today's bordered card with a floating pill that hovers above the chat canvas. Borrowed shape from Claude.ai. Implementation:

- **Container:** `rounded-3xl bg-card border shadow-lg`. Slight shadow elevates it from the message stream below. Positioned `absolute inset-x-0 bottom-0` over the chat scroll area, with the chat content having `pb-40` so it scrolls underneath the composer cleanly. Max width matches the chat content area (`max-w-3xl mx-auto`).
- **Inner layout:**
  - Top row (when present): attachment chips, see §16.8.
  - Middle: `<textarea>` autosize, no border, transparent background, `placeholder:text-muted-foreground`. Min-height fits one line; max-height `200px` then scrolls.
  - Bottom row: left side has the `+` (Plus) attach button (round button, hover bg-accent). Right side has the model picker pill (tier glyph + model name + ChevronDown), then a `Mic` voice-input button (round). **No explicit Send button** — Enter sends, Shift+Enter inserts a newline. The whole pill is the affordance.
- **Below the pill:** small `text-[10px] text-muted-foreground text-center pt-2` reading "Bifrost is AI and can make mistakes." (or org-customized) — the "AI disclaimer" line that Claude/ChatGPT both have.
- **Drag-and-drop overlay:** when files are dragged anywhere over the chat surface, a fixed full-window backdrop (`bg-primary/10 backdrop-blur-sm`) appears with a centered card showing `Upload` icon + "Drop files to attach." Pointer-events-none so it doesn't intercept the drop.

This composer ships in v1; per-conversation instructions and other affordances reach the user via the conversation header overflow menu (§16.5), not via additional inline composer buttons.

### 16.5 Conversation header

Today's chat header is `h-14, border-b`, with title + agent name on the left and admin-only model/tokens/cost stats on the right. Updates:

**Left side (unchanged structure, refined):**
- Conversation title (h1, font-medium, truncated, click to inline-rename).
- Subtitle row (text-xs, muted): workspace name (with `Folder` icon, click → enter that workspace's mode per §16.2) → agent name (with bot avatar, click → @-mention picker for agent switch).

**Right side (visible to all users, not just admins):**
- Model pill (clickable). Shows tier glyph (⚡/⚖/💎) + model display name. Click opens model picker (§16.6). This replaces today's admin-only `Cpu` icon + model name.
- Context budget indicator (compact). Mini progress bar (Tailwind `Progress` from shadcn): `[████░░░░] 32k / 200k`. Color is muted at <70%, primary at 70-85%, destructive past 85%. Hover shows tooltip with breakdown ("System prompt: 3.2k, Knowledge: 8k, History: 21k").
- Cost tier strip. Up to 8 most recent message tier glyphs in a row, e.g., `⚡ ⚡ ⚖ ⚖ ⚖ 💎 💎 ⚡` — text-xs, gap-0.5. Hover tooltip: "12 messages this session: 8 fast, 3 balanced, 1 premium."
- Overflow `MoreHorizontal` → DropdownMenu: "Compact older turns" (with usage badge), "Customize this chat" (per-conversation instructions), "Export…" (Markdown / JSON), "Delete conversation" (destructive).

When the budget indicator is past 85%, a subtle inline button "Compact" appears alongside the bar (ghost variant, text-xs). One-click runs manual compaction.

### 16.6 Model picker

Triggered from: the model pill in the chat header, the workspace settings, the org admin AI settings, and the retry-with-different-model dropdown.

Built on existing `Popover + Command` (matches `MentionPicker` and the workflow Combobox). Differences from a standard combobox:

- Each item shows: tier glyph, display name (font-medium), real provider model ID (text-xs muted, on a second line — visible by default, no hover needed).
- Items the user cannot select are **rendered grayed out** (`opacity-50 cursor-not-allowed`) at the bottom of the list, sorted under a divider with header "Restricted." Each restricted item shows a `Lock` icon and a one-line tooltip on hover: "Restricted by your org admin" / "Restricted by this workspace" / "Restricted by your role (Help Desk)" / "Not enabled on this Bifrost installation."
- Search filter (CommandInput) operates across both available and restricted entries.
- Selection: click → close picker, switch model. New chat header pill updates.

**Special row in the picker:** a divider above the list reading "Aliases" lists `bifrost-fast`, `bifrost-balanced`, `bifrost-premium`, plus any org-defined aliases. Each shows the alias name (font-medium) and the resolved real model on the second line ("⚖ Balanced • Claude Sonnet 4.6"). Below them, a "Specific models" section with raw IDs.

### 16.7 Org admin AI settings

Existing settings live at `/settings/llm` as a tabbed sub-page (`LLMConfig.tsx`). Refactor that page to:

**Top section: provider configuration** (existing — choose Anthropic / OpenAI / OpenRouter / etc., enter API keys).

**Middle section: model availability for chat.**
- DataTable (existing component, used in Forms list) with columns: Display Name (with override field if set), Model ID, Provider, Cost Tier (Select dropdown to override platform default), Available for Chat (Switch).
- Above the table: "+ Add model" button → Dialog with Combobox of all platform-known models not yet in the org list, plus an option to add a raw model ID for self-hosted endpoints.
- Below the table: defaults panel — "Default model for new chats" (Combobox restricted to enabled-for-chat models), "Allowed for fallback compaction" (MultiCombobox).

**Bottom section: aliases.**
- Smaller DataTable of org-defined aliases. Columns: Alias, Display Name, Target Model, Cost Tier, Notes. "+ Add alias" button. Existing `bifrost-*` platform aliases shown grayed out as informational rows ("inherited from platform; click 'Override' to redirect").

**Save flow with reference audit (§5.8.5):**
On Save click, before persisting, run the reference audit. If references would be orphaned, an `AlertDialog` opens (existing component, used for destructive confirmations) — not a Sheet, because this is a blocking decision. Body:

> ⚠️ Saving these changes will orphan model references.
>
> **`minimax-m1`** is referenced in 3 places.
> Replace with: [Combobox: pre-filled with closest available in same tier, restricted to currently-enabled models]
>
> **`gpt-4o`** is referenced in 5 places.
> Replace with: [Combobox]
>
> [ Cancel ] [ Apply replacements and save ]

Each affected model has its own row with the count and a Combobox. The "View affected items" disclosure expands to show which workspaces / roles / conversations reference each (existing Accordion pattern).

### 16.8 Attachment chips in the floating composer

Today's `ChatInput` has a Paperclip button (already wired up) and supports auto-resize textarea. Updates:

**Drag-and-drop overlay.** When the user drags a file over the input area (or anywhere in the chat window), a full-window dashed-border overlay appears with `Upload` icon centered and text "Drop files to attach" (matches the existing `ImportDialog` upload affordance vocabulary). On drop, files queue to upload.

**Attachment chips above the textarea.** As files upload, each shows as a chip — a Card with: thumbnail (image preview, or icon for PDF/CSV/text — `FileText` / `FileSpreadsheet` / `FileImage`), filename (truncated), size (text-xs muted), progress bar (during upload), and X to remove. Multiple attachments wrap into rows. Max 5 chips before a "+N more" overflow.

**Paste support.** Pasting an image into the textarea triggers the same upload path with auto-generated filename `screenshot-YYYY-MM-DD-HHmm.png`.

**Server-side text extraction.** For PDFs, after upload completes, the chip's bottom text changes from "1.2 MB" to "1.2 MB • 3 pages, ~2.1k tokens" — communicating the cost contribution before the user sends.

### 16.9 Editing a user message

Hover a user message → small `Pencil` icon appears at the top-right of the message bubble (ghost button, opacity-0 on default, opacity-100 on group-hover — same pattern as inline-edit affordances elsewhere). Click → message text becomes editable inline (textarea, autosize, same width as the bubble), with "Send" (default button) and "Cancel" (ghost) below.

On Send: the existing message stays in the DB; a sibling user message is created with the new text, and the conversation's active branch flips to the new sibling. Sibling navigation (`< 2/2 >`) renders below both versions so the user can revisit the original.

### 16.10 Retry button

Hover an assistant message → small `RotateCcw` icon at top-right of the bubble. Single click → retry. The new response is a sibling under the same user message; sibling navigation renders below both versions.

### 16.11 Compaction indicators

When auto-compaction triggers mid-stream, a brief inline system event renders in the message stream — same component as today's `ChatSystemEvent`, with `Layers` icon and text "Compacted N earlier turns to free context space" (text-xs muted-foreground, italic, centered, with subtle horizontal divider lines extending from each side). Persistent — stays in the scrollback as a bookmark of when compaction happened.

When the user clicks the manual "Compact" button (in the header, when budget is high): same system event renders, plus a one-line toast (`sonner`): "Older turns summarized. ~30k tokens freed."

When compacted summary content is shown to the model in subsequent turns, the user **never** sees the summary — they see the original messages in their scrollback. The summary is purely a model-context construct.

### 16.12 Per-conversation custom instructions

Triggered from the conversation-header overflow menu → "Customize this chat." Opens a small Dialog (not Sheet — this is a single-field, fast-in-fast-out action):

- Title: "Customize [conversation title]"
- One textarea (autosize, label: "Instructions for this conversation (in addition to the workspace's instructions and agent's prompt)").
- Live cost preview below the textarea: "+ ~120 tokens / message" — same pattern as the workspace instructions cost.
- Existing instructions (if any) are pre-loaded.
- Footer: "Save" / "Cancel" / "Reset to workspace defaults" (ghost, destructive variant).

### 16.13 Multi-agent delegation badge

When an agent delegates within a turn (per §7), the assistant message renders the delegated-agent contribution as a collapsible inline card embedded in the message stream:

```
┌─ ✓ Consulted [Specialist Agent name] ──────────────┐
│  [agent avatar] [agent description, text-xs muted] │
│  ▶ Show details                                    │
└─────────────────────────────────────────────────────┘
```

Click "Show details" → expands to show the delegated agent's contribution (markdown-rendered, indented, with subtle left-border in primary color to distinguish). Card uses the existing `Card` shadcn primitive, slim padding (p-3), rounded-md, border-muted.

Delegation card appears *inline within* the primary agent's response, not as a separate message. The conversation's active agent stays the primary.

### 16.14 Empty states

- **Empty Personal workspace, no conversations yet:** centered (existing pattern from Forms list), `MessageSquare` icon (large, muted), h3 "Start your first chat", text "Pick an agent to chat with, or just start typing." (muted-foreground), "+ New chat" button.
- **Workspace with no conversations:** indented italic "No conversations in this workspace yet."
- **Search returns no results:** centered, `Search` icon (large, muted), h3 "No conversations match your search", text "Try different keywords or check another workspace."
- **No workspaces (impossible after first launch — Personal is auto-created — but a defensive empty state):** falls through to the Personal workspace empty state.

### 16.15 Error states

- **Upload fails:** chip turns red with destructive-tinted border; "Upload failed — retry?" link replaces the size info; X to dismiss.
- **Model unavailable mid-conversation (e.g., provider went down):** inline `ChatSystemEvent`-style banner with `AlertCircle` icon, text "Couldn't reach [model]. Switching to [fallback]." Auto-resolves; no user action needed.
- **Compaction fails (rare — summarizer model unreachable):** banner: "Context near limit. Compaction unavailable. Try a different model or shorten your message." User can manually compact later.
- **Workspace permission lost (admin removed user from role):** existing forbidden pattern — workspace disappears from sidebar; if user was viewing one of its conversations, redirects to Personal workspace with toast: "Access to [Workspace] was revoked."

## 17. References

- Program spec: `2026-04-27-chat-v2-program-design.md`
- Master plan: `../plans/2026-04-27-chat-v2-master-plan.md`
- Sandbox findings (will matter for sub-project (2)): `2026-04-27-chat-v2-sandbox-bwrap-findings.md`
- Existing chat ORM: `api/src/models/orm/agents.py:166-254`
- Existing chat contracts: `api/src/models/contracts/agents.py:180-435`
- Existing agent executor: `api/src/services/agent_executor.py`
- Existing chat store: `client/src/stores/chatStore.ts`
- Existing upload store (reused for attachments): `client/src/stores/uploadStore.ts`
- Existing settings page (informs admin AI settings refactor): `client/src/pages/settings/LLMConfig.tsx`
- Existing execution drawer (informs Workspace Settings sheet): `client/src/components/execution/ExecutionDrawer.tsx`
- Existing combobox / multi-combobox (model picker, tool/knowledge selectors): `client/src/components/ui/combobox.tsx`, `multi-combobox.tsx`
- Existing data table (informs admin model availability table): `client/src/components/ui/data-table.tsx`
- Existing import dialog (informs file-upload affordances): `client/src/components/ImportDialog.tsx`
- Existing chat sidebar (extended for workspaces): `client/src/components/chat/ChatSidebar.tsx`
- Existing chat layout / header (refactored for visible model+budget): `client/src/components/chat/ChatLayout.tsx`
