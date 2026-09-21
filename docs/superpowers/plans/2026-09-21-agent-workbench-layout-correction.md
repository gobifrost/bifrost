# Agent Workbench Layout Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the approved Agent Workbench hierarchy, usable collection width, and collection-specific visual language without changing backend contracts.

**Architecture:** Extend the shared Workbench frame with grouped, icon-led navigation and adaptive inspector sizing. Extend the shared row primitive with an icon/signifier slot, then make each collection supply its own semantic identity and metadata. Keep fleet and agent scopes in the existing `AgentQualityWorkbench` orchestration and verify the complete rendered journey with the provider-free Playwright flow.

**Tech Stack:** React, TypeScript, Tailwind CSS, Lucide React, Vitest, Testing Library, Playwright.

---

### Task 1: Grouped Workspace Navigation and Adaptive Inspector

**Files:**
- Modify: `client/src/components/agents/workbench/AgentWorkbenchFrame.tsx`
- Test: `client/src/components/agents/workbench/AgentWorkbenchFrame.test.tsx`

- [ ] **Step 1: Write failing component tests**

Add assertions that the rail exposes `Act`, `Validate`, `Automate`, and `Evidence` groups, renders collection counts, and labels the icon-only inspector close action as `Close Inspector`.

- [ ] **Step 2: Verify the tests fail for the missing grouped navigation**

Run: `./test.sh client unit src/components/agents/workbench/AgentWorkbenchFrame.test.tsx`

Expected: FAIL because group labels and count text do not exist.

- [ ] **Step 3: Implement the minimal shared-frame API**

Add optional `group`, `icon`, and `count` fields to `WorkbenchCollection`. Render groups in stable input order, retain the mobile selector, reduce the rail width, and use `X` with an accessible label for inspector closing. Keep the collection dominant by limiting the attached inspector to approximately 36%, and replace the collection at intermediate widths.

- [ ] **Step 4: Verify the frame tests pass**

Run: `./test.sh client unit src/components/agents/workbench/AgentWorkbenchFrame.test.tsx`

Expected: all tests pass.

### Task 2: Collection-Specific Rows and Headers

**Files:**
- Modify: `client/src/components/agents/workbench/WorkbenchRow.tsx`
- Modify: `client/src/pages/agents/AgentQualityWorkbench.tsx`
- Test: `client/src/components/agents/workbench/WorkbenchRow.test.tsx`
- Test: `client/src/pages/agents/AgentQualityWorkbench.test.tsx`

- [ ] **Step 1: Write failing row and page tests**

Assert that a row renders an accessible semantic icon/signifier, that Reviews are described as automation that surfaces Findings, and that Findings, Tests, and Reviews expose distinct row metadata rather than the generic description.

- [ ] **Step 2: Verify the focused tests fail for the absent visual hierarchy**

Run: `./test.sh client unit src/components/agents/workbench/WorkbenchRow.test.tsx src/pages/agents/AgentQualityWorkbench.test.tsx`

Expected: FAIL on the new semantic signifiers and collection-specific copy.

- [ ] **Step 3: Implement collection presentation**

Use Lucide icons already shipped by the client. Define one collection descriptor map for label, group, icon, and description. Pass counts from loaded collections. Render Finding rows with kind/source/status, Test rows with latest verdict/version/collection, Review rows with automation status/version, and Run rows with lifecycle identity. Preserve source values exactly and do not invent Finding occurrence counts or accounting values.

- [ ] **Step 4: Verify the focused tests pass**

Run: `./test.sh client unit src/components/agents/workbench/WorkbenchRow.test.tsx src/pages/agents/AgentQualityWorkbench.test.tsx`

Expected: all tests pass.

### Task 3: Rendered Fidelity and Candidate Verification

**Files:**
- Modify if required by rendered evidence: `client/src/components/agents/workbench/AgentWorkbenchFrame.tsx`
- Modify if required by rendered evidence: `client/src/components/agents/workbench/WorkbenchRow.tsx`
- Modify if required by rendered evidence: `client/src/pages/agents/AgentQualityWorkbench.tsx`
- Verify: `client/e2e/agent-workbench.admin.spec.ts`

- [ ] **Step 1: Run static and focused verification**

Run: `./test.sh client unit src/components/agents/workbench/AgentWorkbenchFrame.test.tsx src/components/agents/workbench/WorkbenchCollectionToolbar.test.tsx src/components/agents/workbench/WorkbenchRow.test.tsx src/pages/agents/AgentQualityWorkbench.test.tsx src/pages/agents/GlobalAgentQualityPage.test.tsx`

Run: `(cd client && npm run tsc && npm run lint)`

Expected: focused tests, TypeScript, and lint pass without new warnings.

- [ ] **Step 2: Run the provider-free journey with screenshots**

Run: `./test.sh client e2e --screenshots e2e/agent-workbench.admin.spec.ts`

Expected: the journey passes without live provider calls and emits wide and narrow screenshots.

- [ ] **Step 3: Inspect one bounded screenshot round**

Review wide Findings, Tests, and Reviews plus the narrow pane state. Fix visible hierarchy, overflow, spacing, hover/selection, or inspector-width defects in one batch. Do not add unrequested features.

- [ ] **Step 4: Confirm the rendered correction once**

Rerun only the same Playwright spec after evidence-backed visual fixes. Inspect the final screenshots once and stop polishing.

- [ ] **Step 5: Run the design detector and commit the exact candidate**

Run: `node /home/jack/.codex/skills/impeccable/scripts/detect.mjs --json client/src/components/agents/workbench/AgentWorkbenchFrame.tsx client/src/components/agents/workbench/WorkbenchRow.tsx client/src/pages/agents/AgentQualityWorkbench.tsx`

Run: `git diff --check`

Expected: no unexplained detector findings or whitespace errors. Commit and push the complete candidate only after focused verification is green.
