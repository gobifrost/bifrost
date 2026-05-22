# Roles page UX rethink

**Date:** 2026-05-21
**Status:** draft — awaiting user review before implementation
**Predecessors:** #226 (Users table redesign — landed in PR #275), #276 (Bulk user actions — spec only)

## Problem

The current Roles page (`client/src/pages/Roles.tsx` + `client/src/components/roles/`) has several UX problems that compound:

1. **Hidden affordance.** Clicking a role row opens `RoleDetailsDialog`, which is the only way to see what users/forms a role is bound to. Nothing on the row indicates the dialog exists or that there's anything to drill into.

2. **Two-consumer model that's already wrong.** The detail dialog has tabs for "Users" and "Forms" only. Reality:
   - Roles can be assigned to: **users**, **forms**, **agents** (router endpoint exists), **workflows** (DB model exists, no router), **apps** (DB model exists as `AppRole`, no router), and **knowledge namespaces** (per migration `20260209_fix_knowledge_namespace_roles`).
   - The Users/Forms tabs were the original surfaces. Agents got added to the backend. Apps and knowledge fell behind.
   - As we add more, tabs will keep multiplying. This isn't a tabbed-dialog problem — it's an information-architecture problem.

3. **The assign dialogs are unusable past ~20 items.** `AssignUsersDialog` and `AssignFormsDialog` render every available user/form as a clickable card with no filter, no search, no virtualization. In a tenant with 500 users picking 5 to add to a role means scrolling through 500 cards. Same for forms. Same will be true for any future consumer type.

4. **Per-row remove only.** To unassign 10 users you click X 10 times.

5. **The "Roles" navigation entry implies role administration, but the actual workflow is consumer-administration.** When an admin wants to add a user to a role, they currently go to Users → click user → open the role-management dialog (which lives somewhere else entirely) → assign roles to the user. Going Roles → open role → assign users is the *other* direction. Both should be supported but the page conflates the two.

## Goals

- Make the consumer model extensible — adding a new role-bound entity type (apps, knowledge) requires only a per-type list component, not a redesign.
- Make "assign N items to this role" usable at 500+ items via real search + virtualization.
- Bulk operations (multi-select, batched assign/unassign) instead of per-row clicks.
- The Roles page itself should make clear what each role does and how many things use it, without a click.

## Out of scope

- Permission model rework (scopes / RBAC). Tracked separately in [[project_rbac_scope_migration]].
- The Users-side role assignment flow (clicking a user → assigning roles to them). That's a different page; this spec covers Roles → assigning entities to a role.
- A "what does this role grant" permissions matrix — roles are membership labels in this codebase, not permission carriers, so there's nothing to display.

## Proposed direction (recommendation marked)

### A. Roles page (the list itself)

Replace the current table with a card-grid or expanded-row table that shows, per role, the **counts** of bound consumers across all types, so admins can see at a glance "Auditor role: 12 users, 4 forms, 2 agents, 0 apps."

Row layout:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Auditor                                                          ⋮     │
│ Read-only access to compliance reports                                  │
│ 👥 12 users    📄 4 forms    🤖 2 agents    🧩 0 apps    📚 1 namespace │
└────────────────────────────────────────────────────────────────────────┘
```

Clicking a count chip opens the detail page focused on that consumer type. Clicking the row opens the detail page on a default tab.

**Per-row backend impact**: list endpoint returns the counts inline (one query per type with `GROUP BY role_id`, joined into the response). No N+1.

### B. Role detail page (replaces the dialog)

**Recommendation: detail page, not dialog.** The dialog format forced everything into a cramped 600px-wide modal with two tabs and inline lists. A page gives:

- Real estate for a search-driven assignment flow.
- Per-consumer-type tabs that scale beyond five types without UI strain.
- Deep-linkable URLs (`/roles/<id>/users`) for support tickets and shared screenshots.
- Standard breadcrumbs back to Roles.

Layout sketch:

```
Roles  ▸  Auditor                                              ✏️ Edit  🗑 Delete
─────────────────────────────────────────────────────────────────────────
Read-only access to compliance reports.

[ Users (12) ] [ Forms (4) ] [ Agents (2) ] [ Apps (0) ] [ Knowledge (1) ]
─────────────────────────────────────────────────────────────────────────
 🔍 Search users assigned to this role…              [ + Assign users ]

  ☐  Alex Chen          alex.chen@acme.example       Acme Corp        🗑
  ☐  Bart                bart@vr.example               Van Rooy…        🗑
  ☐  Catherine V-J       …@vanrooyproperties-holdings  Van Rooy…        🗑
  …
─────────────────────────────────────────────────────────────────────────
[3 selected]  [ Unassign 3 from role ]                    [ Clear ]
```

The selection bar mirrors the Users-page bulk pattern from #276 — consistent multi-select UX across the whole product.

### C. The "Assign N items" flow

The current modal-of-cards goes. Replacement: a **drawer or right-side panel** opened by the "+ Assign users" button on the consumer tab.

```
                                          ╔══════════════════════════════╗
                                          ║  Assign users to Auditor     ║
                                          ║  ───────────────────────────  ║
                                          ║  🔍 Search by name or email…  ║
                                          ║  Filters: [Org ▾] [Status ▾]  ║
                                          ║  ───────────────────────────  ║
                                          ║  ☑  Alex Chen   acme          ║
                                          ║  ☐  Bart        van rooy      ║
                                          ║  ☑  Priya K     northwind     ║
                                          ║  …virtualized…                 ║
                                          ║  ───────────────────────────  ║
                                          ║  [2 selected]  [Assign 2 →]   ║
                                          ╚══════════════════════════════╝
```

Key behaviors:
- **Search debounces against the existing users/forms/agents list endpoints** (already supported — `GET /api/users` returns the full set; the frontend filters). For 500+ items, switch to server-side search params later if perf bites; client-side is fine to start.
- **Filters per consumer type.** Users get an org filter + status filter. Forms get an org filter. Agents get an org filter + provider toggle. Apps get an org filter. Knowledge gets a namespace filter.
- **Already-assigned items are hidden** by default with a "Show already assigned" toggle (so an admin doesn't have to scroll past them).
- **Submit assigns the batch in one API call** — the existing `POST /api/roles/{role_id}/users` etc. already accept `{user_ids: list}`. No new endpoints needed for assign.
- **Drawer stays open** after submit, just refreshes the consumer-tab list behind it. Multiple assign batches in one session don't need open/close cycles.

### D. Bulk unassign

The consumer tab itself supports multi-select (checkbox per row + select-all-visible, matching the Users page). The bottom action bar shows "Unassign N from role." That hits a new endpoint per consumer type, or — better — extends each consumer-type unassign endpoint to accept a body `{ids: [...]}` instead of just a path param.

**Recommendation: extend the existing unassign endpoints to accept a list body.** Today they're `DELETE /api/roles/{role_id}/users/{user_id}`. Change to `DELETE /api/roles/{role_id}/users` with `{user_ids: [...]}` body, **and keep the single-ID path form for backwards compat**. Same for forms and agents. Apps and knowledge get the list form from the start.

### E. Reverse direction: assigning roles to users (separate page, mention only)

The dual flow — "go to user, add this role" — currently lives in `EditUserDialog` somewhere. Out of scope here; flagging that #276's Users page redesign should include a "Replace roles" bulk action that reuses the same role-picker drawer pattern from C. The drawer becomes a shared component.

## Component plan

| Component | Status |
|---|---|
| `pages/Roles.tsx` | Rewrite as card-grid with per-type counts |
| `pages/RoleDetail.tsx` | **New.** Replaces `RoleDetailsDialog`. Route `/roles/:id`. Tabs per consumer type. |
| `components/roles/RoleDetailsDialog.tsx` | **Delete.** |
| `components/roles/AssignUsersDialog.tsx` | **Replace with `AssignDrawer<T>`** (generic) — see below. |
| `components/roles/AssignFormsDialog.tsx` | **Delete** (replaced by generic). |
| `components/roles/AssignDrawer.tsx` | **New.** Generic drawer parameterized by consumer type. Props: `roleId`, `consumerType`, plus a render-prop or type-specific config for the row shape and per-type filters. |
| `components/roles/consumer-tabs/UsersTab.tsx` | **New.** Renders the assigned-users list inside the role detail page. Has the multi-select bulk-unassign pattern. |
| `components/roles/consumer-tabs/FormsTab.tsx` | **New.** |
| `components/roles/consumer-tabs/AgentsTab.tsx` | **New.** |
| `components/roles/consumer-tabs/AppsTab.tsx` | **New.** (Stub — wait on backend endpoint.) |
| `components/roles/consumer-tabs/KnowledgeTab.tsx` | **New.** (Stub — wait on backend endpoint.) |

The `AssignDrawer<T>` and `ConsumerTab<T>` generic shapes are key. If we get them right, adding a sixth consumer type (e.g. integrations) becomes ~50 lines of config, not a new dialog.

## Backend changes required

1. **Add count-per-consumer-type to `GET /api/roles`** so the list page can show inline counts without a per-row fetch.
2. **Apps endpoints** (`GET /api/roles/{role_id}/apps`, `POST` to assign, `DELETE` to unassign). Mirror agents.
3. **Knowledge endpoints** (same shape, against `knowledge_namespace_roles` join table).
4. **Bulk unassign on existing endpoints** (users/forms/agents) — accept list body, keep path-param single-form.

No DB migrations. The data model already has every join table.

## Open questions

1. **Drawer vs dialog for assign?** Drawer matches modern admin tools (Linear, Vercel) and stays open across batches. Dialog is what's there today. **Recommendation: drawer.** Cheaper to dismiss accidentally with click-outside, but the saved trips for repeated assigns are worth it.

2. **Where does "what does this role grant"?** Roles in this codebase don't carry permissions — they're labels matched against `access_level` on the consumer. So the answer is "nothing, intrinsically." Should the role detail page surface this with a note ("Roles are labels; access is granted on the consumer's settings tab")? **Recommendation: yes**, prevents the inevitable "where do I configure what this role can do" support ticket.

3. **Counts shown on the Roles list — clickable?** A 12-user chip could (a) open the role detail page focused on the Users tab, or (b) jump straight to the Users tab without changing focus on the row. **Recommendation: (a)** — fewer states, deep-linkable.

4. **Workflows.** `WorkflowRole` exists in the data model but doesn't surface in the current dialog. Are role↔workflow assignments managed elsewhere, or is this a fifth (sixth?) consumer type we need a tab for? Needs the user's confirmation before I assume.

## Implementation phasing

Each phase is its own PR.

1. **#PR-N**: Add count-per-type to `GET /api/roles` + render counts on the existing Roles page. No UX change yet; just observable counts.
2. **#PR-N+1**: Add `pages/RoleDetail.tsx` + `/roles/:id` route. Move existing Users + Forms tab content from the dialog into the page. Delete the dialog. (No new consumer types yet — keep the diff bounded.)
3. **#PR-N+2**: Replace assign dialogs with `AssignDrawer<T>` generic + per-type filters/search. Apply to Users and Forms.
4. **#PR-N+3**: Multi-select + bulk-unassign on each consumer tab. Backend list-body unassign endpoints.
5. **#PR-N+4**: Add Agents tab (router exists). Add Apps + Knowledge endpoints and their tabs.
6. **#PR-N+5** (if Q4 answered): Workflows tab + endpoints.

## Risks

- **`AssignDrawer<T>` generic complexity.** A wrong abstraction here is worse than five tab-specific drawers. If the per-type filter shapes diverge sharply, drop the generic and accept N nearly-identical components. Decide at PR-N+2.
- **`GET /api/roles` count subqueries.** Could be slow if a tenant has thousands of role assignments. Index per join table on `role_id` (probably already present — verify).
- **Existing dialog tests.** `RoleDetailsDialog.test.tsx`, `AssignUsersDialog.test.tsx`, `AssignFormsDialog.test.tsx` get deleted in phases 2-3. Replace with page + drawer tests.

## Acceptance

The page is "done" when:
- All five consumer types render (or have a coherent "not yet supported" stub).
- 500-item assign drawer is responsive (search filters in <100ms client-side).
- Bulk-unassign 100 users from a role takes one click + confirmation, not 100.
- A new consumer type can be added with one config object + one list endpoint, no UI scaffolding.
