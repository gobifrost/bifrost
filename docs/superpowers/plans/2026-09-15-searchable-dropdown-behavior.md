# Searchable Dropdown Behavior Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every shared command-based searchable dropdown filter its complete declared item data predictably and return search results to the top of a navigable list.

**Architecture:** Add a deterministic default filter and query-scroll coordination to the shared `Command` primitives while preserving explicit consumer filters and the existing iframe-safe item scrolling. Expand the generic combobox's keyword corpus so descriptions participate in the shared behavior, then verify the workflow selector and a seeded History-page preview.

**Tech Stack:** React 19, TypeScript, cmdk 1.1.1, Vitest, Testing Library, Vite, Bifrost debug stack and CLI.

---

### Task 1: Reproduce the shared command failures

**Files:**
- Modify: `client/src/components/ui/command.test.tsx`

- [ ] **Step 1: Add a failing default-filter contract test**

Render `CommandInput`, `CommandList`, and items whose stable values and keywords contain different searchable fields. Assert that multiple literal terms can match across value and keywords, while a non-contiguous fuzzy abbreviation does not match.

- [ ] **Step 2: Add a failing stale-scroll regression test**

Set a rendered command list's `scrollTop` to a deep position, type a query, wait for cmdk's update frame, and assert that the list returns to `scrollTop === 0` while the matching item remains available.

- [ ] **Step 3: Add a failing visible-scrollbar contract test**

Assert that `CommandList` retains vertical overflow but no longer includes the `no-scrollbar` utility.

- [ ] **Step 4: Run the focused test and confirm RED**

Run:

```bash
./test.sh client unit src/components/ui/command.test.tsx
```

Expected: the fuzzy-abbreviation, stale-scroll, and hidden-scrollbar assertions fail for the intended missing behavior; the existing iframe-safe scrolling test remains green.

### Task 2: Harden the shared command primitive

**Files:**
- Modify: `client/src/components/ui/command.tsx`
- Test: `client/src/components/ui/command.test.tsx`

- [ ] **Step 1: Add the deterministic default filter**

Implement a module-level cmdk filter that lowercases and trims the query, splits it on whitespace, combines item `value` with declared `keywords`, and returns `1` only when every query term is a literal substring. Make it the `Command` default while allowing an explicit `filter` prop to override it.

- [ ] **Step 2: Reset the local list after query changes**

Give `CommandInput` an internal input ref. After forwarding the consumer's `onValueChange`, schedule one animation frame that finds the nearest `[cmdk-root]` and its `[cmdk-list]`, then sets only that list's `scrollTop` to zero. Cancel an outstanding frame on a subsequent query and on unmount.

- [ ] **Step 3: Restore native scrollbar affordance**

Remove `no-scrollbar` from the shared `CommandList` classes while retaining the height cap, `overflow-y-auto`, wheel containment, and item-level `scrollIntoView` containment.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run:

```bash
./test.sh client unit src/components/ui/command.test.tsx
```

Expected: all shared command tests pass without errors.

- [ ] **Step 5: Commit the shared primitive change**

```bash
git add client/src/components/ui/command.tsx client/src/components/ui/command.test.tsx
git commit -m "fix: make searchable command lists predictable"
```

### Task 3: Include complete combobox option metadata

**Files:**
- Modify: `client/src/components/ui/combobox.tsx`
- Modify: `client/src/components/ui/combobox.test.tsx`
- Test: `client/src/components/forms/WorkflowSelector.test.tsx`

- [ ] **Step 1: Add a failing description-search test**

Render a generic combobox whose option ID and label omit `sharepoint` but whose description contains it. Search for `sharepoint` and assert that the option remains visible while unrelated options are filtered out.

- [ ] **Step 2: Run the combobox test and confirm RED**

Run:

```bash
./test.sh client unit src/components/ui/combobox.test.tsx
```

Expected: the description-only result is missing.

- [ ] **Step 3: Declare the description as searchable metadata**

Pass both `option.label` and `option.description` through `CommandItem.keywords`; keep the stable option ID in `value`.

- [ ] **Step 4: Add workflow selector coverage**

Render the workflow combobox with enough workflows to represent a long list and include separate SharePoint matches in a name and in a description. Assert that both results remain after searching while unrelated workflows disappear.

- [ ] **Step 5: Run consumer tests and confirm GREEN**

Run:

```bash
./test.sh client unit src/components/ui/combobox.test.tsx src/components/forms/WorkflowSelector.test.tsx
```

Expected: both test files pass.

- [ ] **Step 6: Commit consumer metadata coverage**

```bash
git add client/src/components/ui/combobox.tsx client/src/components/ui/combobox.test.tsx client/src/components/forms/WorkflowSelector.test.tsx
git commit -m "test: cover searchable dropdown metadata"
```

### Task 4: Verify and prepare the seeded preview

**Files:**
- Verify: `client/src/components/ui/command.tsx`
- Verify: `client/src/components/ui/combobox.tsx`
- Verify: `client/src/components/forms/WorkflowSelector.tsx`

- [ ] **Step 1: Run targeted component verification**

```bash
./test.sh client unit src/components/ui/command.test.tsx src/components/ui/combobox.test.tsx src/components/forms/WorkflowSelector.test.tsx src/pages/ExecutionHistory.test.tsx
```

Expected: all selected tests pass.

- [ ] **Step 2: Run client type checking and linting**

```bash
(cd client && npm run tsc && npm run lint)
```

Expected: both commands exit successfully.

- [ ] **Step 3: Boot the worktree debug stack**

```bash
./debug.sh up
./debug.sh status
```

Expected: status reports `UP` and prints an `Open:` URL plus the seeded development login.

- [ ] **Step 4: Install and authenticate the matching CLI in isolated scratch space**

Create `/tmp/bifrost-cli-searchable-dropdown`, install the CLI tarball served by the debug API into its virtual environment, and log in using the URL and credentials reported by `./debug.sh status`.

- [ ] **Step 5: Seed a long, diagnostic workflow list**

Use the matched CLI's workflow creation/register commands, as exposed by `bifrost workflows --help`, to add enough global and organization-scoped workflows to overflow the History picker. Include workflows named `SharePoint Site Audit` and `Archive Document Library`, with the latter's description mentioning SharePoint, plus unrelated alphabetically distributed entries.

- [ ] **Step 6: Manually verify the History workflow filter**

Open the History page as the seeded platform administrator. Confirm the picker exposes a scrollbar; scrolling deep and then searching `sharepoint` returns to the top; both name and description matches appear; clearing search restores the complete list; and keyboard Home, arrows, and Enter stay inside the dropdown.

- [ ] **Step 7: Commit the verified candidate**

```bash
git status --short
git add client/src/components/ui/command.tsx client/src/components/ui/command.test.tsx client/src/components/ui/combobox.tsx client/src/components/ui/combobox.test.tsx client/src/components/forms/WorkflowSelector.test.tsx docs/superpowers/specs/2026-09-15-searchable-dropdown-behavior-design.md docs/superpowers/plans/2026-09-15-searchable-dropdown-behavior.md
git commit -m "fix: stabilize searchable dropdown filtering"
```

Expected: the worktree is clean after the commit.
