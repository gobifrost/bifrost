# Workspace and Dashboard Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mismatched Home/Dashboard title treatment with stable, title-sized Workspace/Dashboard tabs for admins and a plain Workspace title for other users.

**Architecture:** Add an explicit custom title slot to the shared list header so the workspace navigation can own its heading semantics without invalid nested markup. Reuse `WorkspaceTabs` in both routes and standardize both page containers on the dashboard's 1400px maximum width.

**Tech Stack:** React, TypeScript, React Router, Tailwind CSS, Vitest, Testing Library, Playwright

---

### Task 1: Specify the shared title behavior

**Files:**
- Modify: `client/src/components/layout/WorkspaceTabs.test.tsx`
- Modify: `client/src/pages/Home.test.tsx`

- [ ] **Step 1: Write failing component assertions**

Assert that the admin navigation contains `Workspace` and `Dashboard`, marks Dashboard current at `/dashboard`, and exposes Dashboard as the active heading. Assert that an ordinary user sees a `Workspace` heading without navigation.

- [ ] **Step 2: Write the failing Home assertions**

Assert that an ordinary user sees the plain `Workspace` heading, while an admin sees a `Workspace views` navigation whose Workspace link is current and is the page heading. Confirm there is only one Dashboard link in that navigation rather than a separate action.

- [ ] **Step 3: Verify RED**

Run:

```bash
./test.sh client unit src/components/layout/WorkspaceTabs.test.tsx src/pages/Home.test.tsx
```

Expected: failures for the missing `Workspace` label/title navigation and current standalone Dashboard action.

### Task 2: Implement the stable title navigation

**Files:**
- Modify: `client/src/components/layout/ListPageHeader.tsx`
- Modify: `client/src/components/layout/WorkspaceTabs.tsx`
- Modify: `client/src/pages/Home.tsx`
- Modify: `client/src/pages/Dashboard.tsx`

- [ ] **Step 1: Add a custom title slot**

Add an optional `titleSlot` prop to `ListPageHeader`. Render it in place of the normal `<h1>` while preserving the existing title path for all other consumers.

- [ ] **Step 2: Make WorkspaceTabs the visual and semantic title**

Rename `Home` to `Workspace`, apply the shared title typography to both links, and add `role="heading" aria-level={1}` to the active link label. When dashboard access is unavailable, return a plain `<h1>` with the same typography.

- [ ] **Step 3: Use the title slot on both pages**

Pass `<WorkspaceTabs />` through `titleSlot` on Home and Dashboard. Remove Home's standalone Dashboard button and Dashboard's duplicate title. Remove imports that become unused.

- [ ] **Step 4: Standardize the workspace width**

Change Home from `max-w-[1200px]` to `max-w-[1400px]`, matching Dashboard while leaving responsive page padding and each page's actions intact.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
./test.sh client unit src/components/layout/WorkspaceTabs.test.tsx src/pages/Home.test.tsx
```

Expected: all targeted component tests pass.

### Task 3: Update and verify the browser journey

**Files:**
- Modify: `client/e2e/home.admin.spec.ts`

- [ ] **Step 1: Update the existing happy path**

Use the `Workspace views` navigation to switch from Workspace to Dashboard and back. Assert the active tab is the visible heading on each route and both `data-page-workspace` containers have the same 1400px maximum width.

- [ ] **Step 2: Run static checks**

Run:

```bash
(cd client && npm run tsc && npm run lint)
```

Expected: both commands pass.

- [ ] **Step 3: Boot the debug stack and run the focused browser test**

Run:

```bash
./debug.sh status
./debug.sh up
./test.sh client e2e e2e/home.admin.spec.ts
```

Expected: the stack reports an `Open:` NetBird URL and the Home admin journey passes.

- [ ] **Step 4: Inspect the live result**

At the reported URL, verify the header does not change position or container width while switching between Workspace and Dashboard.

