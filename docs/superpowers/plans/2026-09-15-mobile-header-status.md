# Mobile Header Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Bifrost phone header to one toolbar while preserving workspace status indicators on larger screens.

**Architecture:** Reuse the current media-query breakpoints and status-indicator component. The compact status strip remains in the DOM only from the `sm` breakpoint upward, matching the existing priority assigned to secondary header content.

**Tech Stack:** React, TypeScript, Tailwind CSS, Vitest, Testing Library

---

### Task 1: Prioritize the mobile header toolbar

**Files:**
- Modify: `client/src/components/layout/Header.test.tsx`
- Modify: `client/src/components/layout/Header.tsx`

- [ ] **Step 1: Write the failing test**

Change the mobile assertion to require that `Workspace status` is absent, then add a tablet-width assertion requiring it to remain visible.

- [ ] **Step 2: Run the test to verify it fails**

Run: `./test.sh client unit src/components/layout/Header.test.tsx`

Expected: the phone-width test fails because the status strip is still rendered.

- [ ] **Step 3: Write the minimal implementation**

Render the compact status strip only when the header is compact but not mobile, so it does not consume a second phone-sized row.

- [ ] **Step 4: Run targeted verification**

Run:

```bash
./test.sh client unit src/components/layout/Header.test.tsx
(cd client && npm run tsc && npm run lint)
```

Expected: all commands exit successfully.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/layout/Header.tsx client/src/components/layout/Header.test.tsx docs/superpowers
git commit -m "fix: keep mobile header status on one row"
```
