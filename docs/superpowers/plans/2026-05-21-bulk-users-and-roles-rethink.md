# Bulk users + Roles rethink — clearable plan

**Branch:** `phase-b-c-preview`
**Worktree:** `/home/jack/GitHub/bifrost/.worktrees/phase-b-c-preview`
**Debug stack URL:** `http://bifrost-debug-phase-b-c-preview-248-33.netbird.cloud` (current — get from `./debug.sh status`)
**CLI install:** `/tmp/bifrost-cli-226/.venv/bin/bifrost` (worktree-agnostic; logged into the old 226 stack — re-login required, see below)

## How to use this doc

It's a single artifact you (or a fresh Claude session) can read end-to-end, redirect on, or kick off from. Each section has:
- **Decision:** what we picked, with a one-line reason
- **Override hook:** the cheap way to switch later if you change your mind
- **Definition of done:** what "fully wired, no stubs" means for that piece

If a section's decision is wrong, edit just the **Decision** line and Claude can rebuild from there.

---

## Constraint: no stubs at merge time

User said: *"I don't want to leave anything as stubbed before we merge."*

That means each phase below must ship its **full backend + frontend + tests + seed data**. The work order below is structured so that when each block lands, there's nothing half-built behind it. Block 4 (the bulk-unassign / apps / knowledge endpoints) used to be "skip for preview" — it's now required.

## Constraint: previewable along the way

Every block ends with **a working state you can click around in the browser at the netbird URL above**. The branch accumulates commits; the PR description will summarize at merge.

---

## Open design decisions (locked unless you flip them)

Status: **locked** = I'll proceed with this assumption. **Open** = waiting on you.

### Phase B — Bulk user actions

| ID | Question | Decision | Status |
|---|---|---|---|
| B-Q1 | Replace vs additive roles | Replace (overwrite) | locked |
| B-Q2 | Move-org demoting platform admins | Refuse + put them in `failed` | locked |
| B-Q3 | Self in selection | Render checkbox disabled with tooltip | locked |

### Phase C — Roles UX

| ID | Question | Decision | Status |
|---|---|---|---|
| C-Q1 | Assign N: drawer vs dialog | Right-side drawer (~480px), stays open after submit | locked |
| C-Q2 | "What does a role grant" note on detail page | Yes — single-line in header | locked |
| C-Q3 | Counts on Roles list clickable? | Yes — deep-link to `/roles/:id/<type>` | locked |
| C-Q4 | Workflows as a consumer type | Yes — and we build the backend endpoint, not stub | locked (changed from earlier draft) |
| C-Q5 | **NEW:** Single bulk endpoint per consumer type or one super-endpoint? | One per type (`POST /api/roles/{id}/users/bulk-assign`, etc.) — mirrors existing surface | locked |
| C-Q6 | **NEW:** Drawer search — client-side filter on full list, or server-side `?q=` param? | Client-side for ≤500 items, server-side params if perf bites. Start client-side. | locked |

Anything you want flipped: tell me the ID and the new value.

---

## Work blocks (execute in order)

Each block ends with a green-status preview. If a block is too big for one session, split between the **commit boundaries** marked.

### Block 1: B1 — Bulk users backend ✅ DONE

- `PATCH /api/users/bulk` — `BulkUserOperation` discriminated by `operation` field
- Single transaction, returns `{succeeded, failed: [{user_id, reason}]}`
- Audit row `user.bulk_update`
- **Committed:** `2e7ea1ba`

**Definition of done for block 1:**
- [x] Endpoint exists, types in OpenAPI
- [x] Move-org, replace-roles, set-active all handled
- [x] Self / system / platform-admin guards in place
- [x] **Backend e2e test** (`api/tests/e2e/api/test_users_bulk.py`) — added in block 2 (commit `3fee262b`). Caught a real bug: UUID passed to `UserRole.assigned_by` (String(255)) — fixed in the same commit.

### Block 2: B1 e2e + B2 — Bulk users frontend ✅ DONE

**Backend test additions:**
- `api/tests/e2e/api/test_users_bulk.py` covering:
  - move_org happy path
  - move_org refuses platform admin → non-provider
  - replace_roles overwrites previous set (verify before/after counts)
  - set_active false → user disabled, can't log in
  - System user + self appear in `failed`
  - Empty `user_ids` rejected by validator

**Frontend:**
- New first column: checkbox per row + header checkbox for select-all-visible
- `useUserSelection` hook in `client/src/hooks/useUserSelection.ts`
  - Holds `Set<UUID>`
  - When the filter changes, prune ids that are no longer in `sortedUsers` (re-add automatically when filter reverts is *out of scope* — sticky-across-filter is a v2)
  - Shift-click range selection
- Sticky bottom action bar `client/src/components/users/BulkActionBar.tsx`
  - Slides up from bottom of the table card when `selection.size > 0`
  - Shows count + 3 buttons (Move to org, Replace roles, Disable/Enable split-button)
  - Clear-selection link
- 3 modals:
  - `BulkMoveOrgDialog.tsx` — OrganizationSelect + confirmation
  - `BulkReplaceRolesDialog.tsx` — role multi-select (reuses Combobox or similar)
  - `BulkSetActiveDialog.tsx` — Enable / Disable / Mixed cases
- All three call `PATCH /api/users/bulk`
- On partial failure: toast + opens `BulkResultDialog` listing failed rows + reasons
- Vitest for `useUserSelection` (shift-range, filter-prune, self-disabled)
- Vitest for `BulkActionBar` (renders correct buttons per selection state)
- Playwright e2e `client/e2e/users.bulk.spec.ts`: select 3, move-org, assert toast

**Definition of done for block 2:**
- [x] Backend e2e (`test_users_bulk.py`) + UUID/str fix — commit `3fee262b`
- [x] `useUserSelection` hook with shift-range, select-all-visible, filter pruning (11 vitest cases)
- [x] Checkbox column on Users table; self row disabled with tooltip
- [x] `BulkActionBar` with active-mix logic (6 vitest cases)
- [x] `BulkMoveOrgDialog`, `BulkReplaceRolesDialog`, `BulkSetActiveDialog`, `BulkResultDialog`
- [x] Playwright e2e (`users.bulk.spec.ts`) — select 3 → Move to org → toast
- [x] tsc + lint + pyright + ruff clean
- [x] Frontend commit `cc9a9c76` pushed

**Commit boundary:** end of block 2 is a natural commit. Push it. ✅

### Block 3: C4 — Backend endpoints (do BEFORE the Roles UI)

Why first: the new Roles UI calls these endpoints. Building UI against missing endpoints means stubs, which violates the no-stubs constraint.

**3a. List-body bulk-unassign on existing surfaces:**
- `DELETE /api/roles/{role_id}/users` accepting `{user_ids: [...]}` body
- `DELETE /api/roles/{role_id}/forms` accepting `{form_ids: [...]}` body
- `DELETE /api/roles/{role_id}/agents` accepting `{agent_ids: [...]}` body
- Keep the existing single-ID path forms for backwards compat (some callers use them)

**3b. New consumer types — full CRUD pair each:**
- Apps:
  - `GET /api/roles/{role_id}/apps` → `{app_ids: [...]}`
  - `POST /api/roles/{role_id}/apps` accepting `{app_ids: [...]}` body
  - `DELETE /api/roles/{role_id}/apps` accepting `{app_ids: [...]}` body
- Workflows (this is the C-Q4 change — no stub):
  - Same three endpoints against `WorkflowRole`
- Knowledge:
  - Same three endpoints against `KnowledgeNamespaceRole` (need to confirm exact ORM name; check `api/alembic/versions/20260209_fix_knowledge_namespace_roles.py`)

**3c. Inline counts on `GET /api/roles`:**
- Response shape extends with `consumer_counts: {users: int, forms: int, agents: int, apps: int, workflows: int, knowledge: int}`
- Computed via 6 `SELECT role_id, COUNT(*) FROM <join> GROUP BY role_id` queries joined into the response in-memory. Six queries vs N+1 per role.

**Tests:**
- `api/tests/e2e/api/test_roles_bulk.py` — bulk-unassign for each surface
- `api/tests/e2e/api/test_roles_apps.py`, `test_roles_workflows.py`, `test_roles_knowledge.py`
- Existing `test_roles.py` updated for the new count field

**Definition of done for block 3:**
- All 6 consumer types have working list/assign/unassign endpoints
- `GET /api/roles` returns counts that survive a round-trip assignment + unassignment
- All e2e tests green
- Push commit

### Block 4: C1 — Roles list rewrite with inline counts

**Frontend only** (backend from block 3):
- Rewrite `client/src/pages/Roles.tsx` from table to card-grid layout
- Each card: name, description, 6 count chips (`👥 12  📄 4  🤖 2  🧩 1  ⚙️ 3  📚 0`)
- Each chip is a Link to `/roles/:id/<consumer-type>` (deep-link per C-Q3)
- Card hover/focus state per shadcn norms
- Vitest for the count-chip click handler

**Definition of done for block 4:**
- Roles page shows realistic counts (depends on seed data in block 6)
- Clicking a chip lands on the right tab of the detail page (which we build next)
- For now, the click goes to the existing dialog — fine until block 5 deletes the dialog
- Push commit

### Block 5: C2 — Role detail page + per-tab content (+ C3 AssignDrawer)

This is the biggest block. Splitting commit-by-tab.

**5a. New route and page scaffold:**
- Add `/roles/:id/:tab?` to the React Router config
- New `client/src/pages/RoleDetail.tsx`:
  - Breadcrumb: Roles ▸ <name>
  - Header: name + description + the C-Q2 grant note + Edit/Delete actions
  - Tab strip: Users · Forms · Agents · Apps · Workflows · Knowledge
  - `<Outlet />` for the current tab
- Delete `client/src/components/roles/RoleDetailsDialog.tsx` and its test
- Old "click row to open dialog" replaced with "click row to navigate to detail page"

**5b. Shared consumer-tab component:**
- `client/src/components/roles/consumer-tabs/ConsumerTab.tsx`
  - Generic over the consumer type
  - Props: `roleId`, `consumerType`, `useList()` hook, `useUnassign()` hook, row renderer
  - Search input (client-side filter)
  - Multi-select with checkboxes + sticky bottom bar (reusing the pattern from BulkActionBar)
  - "+ Assign X" button → opens `AssignDrawer`
- One concrete tab per type (~50 lines each):
  - `UsersTab.tsx`, `FormsTab.tsx`, `AgentsTab.tsx`, `AppsTab.tsx`, `WorkflowsTab.tsx`, `KnowledgeTab.tsx`

**5c. AssignDrawer (C3):**
- `client/src/components/roles/AssignDrawer.tsx`
  - Right-side drawer (~480px) — use shadcn's Sheet component
  - Search input + per-type filter chips (org, status for users; org for forms/agents/apps; namespace for knowledge)
  - Virtualized list (use react-virtuoso or similar, or simple windowing if the dep cost is too high)
  - "Show already assigned" toggle (default off — i.e. hide already-assigned by default)
  - Submit batches via the corresponding `POST /api/roles/{role_id}/<type>` (bulk-accepting from block 3)
  - Stays open after submit, refreshes the consumer-tab list behind it
- Delete `client/src/components/roles/AssignUsersDialog.tsx`, `AssignFormsDialog.tsx`, and their tests

**5d. Bulk-unassign action bar:**
- Reuses the BulkActionBar pattern (extract to a generic component if reasonable)
- One button: "Unassign N from this role" — calls the `DELETE /api/roles/{id}/<type>` from block 3

**Tests:**
- Vitest for `ConsumerTab` (selection, search filter, "show already assigned" toggle)
- Vitest for `AssignDrawer` (search, virtualization sanity, submit)
- Playwright `client/e2e/roles.detail.spec.ts`: open role → see counts on chips → click chip → land on tab → assign 2 users via drawer → verify list → multi-select → bulk-unassign → verify counts dropped

**Definition of done for block 5:**
- All 6 consumer tabs work end-to-end
- AssignDrawer works for all 6 types
- Old dialog files gone; nothing imports them
- Playwright spec passes
- Push commit

### Block 6: Seed data + manual preview pass

**Goal:** the user opens the URL and sees realistic state.

**Via the CLI (re-login first since 226 stack is gone):**
```bash
cd /tmp/bifrost-cli-226
./.venv/bin/bifrost logout --url http://bifrost-debug-226-users-table-layout-225-115.netbird.cloud  # cleanup old
./.venv/bin/bifrost login --url http://bifrost-debug-phase-b-c-preview-225-115.netbird.cloud --email dev@gobifrost.com --password password
```

**Then seed:**
- 4 orgs: Acme Corp, Northwind Traders MSP, Van Rooy Properties (long-name), Globex (provider)
- 4 roles: Auditor, Operator, Support, ReadOnly
- 4 apps: Help Center, Customer Portal, Internal Wiki, Status Dashboard
- 4 agents: Tier-1 Triage, Renewals Bot, Onboarding Assistant, Reporting Helper
- 4 forms (use whatever's easiest — even copying an existing seed)
- ~12 users across the orgs with varied invite states
- Assignments that produce visible counts:
  - Auditor: 4 users, 2 forms, 1 agent, 0 apps, 1 workflow, 0 knowledge
  - Operator: 6 users, 1 form, 3 agents, 2 apps, 0 workflows, 1 knowledge
  - Support: 2 users, 3 forms, 0 agents, 1 app, 0 workflows, 0 knowledge
  - ReadOnly: 8 users, 0 forms, 0 agents, 0 apps, 1 workflow, 2 knowledge

**Definition of done for block 6:**
- `/users` shows ~12 users including the long-org-name row and the long-email row from block 2's playwright
- `/roles` shows 4 cards with counts populated
- Clicking a count chip lands on the right tab
- Drawer opens, search filters, assign works
- Bulk-unassign works

### Block 7: Polish + PR

- Run `./test.sh all`, `./test.sh client unit`, `./test.sh client e2e`
- Run `npm run tsc`, `npm run lint`
- Run `pyright`, `ruff check`
- Generate fresh types
- Squash-friendly commit history check; rebase main if behind
- Open PR with summary linking to this plan
- Auto-merge **not queued** — wait for explicit user approval (per memory rule)

---

## Failure modes I'm watching for

- **C-Q4 Workflow endpoint scope creep.** The `WorkflowRole` table exists; the question is whether implementing the endpoint requires touching the workflow access-resolution code elsewhere. If it does, I'll surface it before going deep.
- **Knowledge namespace ORM unfamiliar.** I haven't read this code. If the model name or join shape is non-obvious, I'll pause and check in.
- **Virtualization library choice.** If react-virtuoso is already a dep, free. If not, I'll either reuse an existing list pattern or do simple windowed rendering inline rather than add a dep without asking.
- **`./debug.sh` netbird URL.** The full numbered hostname is `bifrost-debug-phase-b-c-preview-225-115.netbird.cloud` per `./debug.sh status` output — but the short form should also work once DNS settles.
- **PR length.** This is a big merge. If you'd rather break it into 3-4 PRs (B → C-backend → C-frontend → seed), say so before block 3. Default is one PR for now.

---

## How to resume / redirect

**To resume after a stop:**
> "Continue the plan at `docs/superpowers/plans/2026-05-21-bulk-users-and-roles-rethink.md`. Pick up from block N."

**To override a decision:**
> "Flip B-Q2 to allow implicit demote." (or whatever)

I'll edit this doc with the new decision and proceed.

**To start a fresh session:**
> "Read `docs/superpowers/plans/2026-05-21-bulk-users-and-roles-rethink.md` and continue from where it left off."

The doc is self-contained; a fresh Claude can pick up without conversation history.
