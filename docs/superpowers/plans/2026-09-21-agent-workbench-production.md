# Agent Workbench Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the disconnected Agent Quality pages and legacy tuning discussion with the accepted Agent Workbench collection-and-inspector experience backed by the existing production APIs.

**Architecture:** `AgentQualityWorkbench` becomes the single route-level component for fleet and agent scope, with small focused Workbench components for the frame, navigation, toolbar, rows, and inspector. Existing `/agents/quality` and `/agents/:id/quality` URLs remain compatible, but every visible label uses Workbench terminology. Run triage remains a lightweight verdict/note workflow and links negative verdicts to an explicit durable Finding instead of the retired improvement conversation.

**Tech Stack:** React, TypeScript, React Router, TanStack Query, Radix-backed Bifrost components, Vitest/Testing Library, Playwright, Tailwind/Bifrost design tokens.

---

## File structure

- Modify `client/src/pages/agents/AgentQualityWorkbench.tsx`: shared fleet/agent route state, queries, mutations, selection, and inspector orchestration.
- Modify `client/src/pages/agents/GlobalAgentQualityPage.tsx`: compatibility wrapper only; no second page implementation.
- Create `client/src/components/agents/workbench/AgentWorkbenchFrame.tsx`: contained workspace, breadcrumb/header, navigation rail, mobile collection selector, collection pane, and attached inspector pane.
- Create `client/src/components/agents/workbench/AgentWorkbenchFrame.test.tsx`: navigation, selected-row, inspector, and mobile return behavior.
- Create `client/src/components/agents/workbench/WorkbenchCollectionToolbar.tsx`: search, agent filter, contextual controls, and trailing primary action.
- Create `client/src/components/agents/workbench/WorkbenchCollectionToolbar.test.tsx`: filter/search/action behavior and accessible labels.
- Create `client/src/components/agents/workbench/WorkbenchRow.tsx`: full-row interactive treatment with nested-control isolation and persistent selected state.
- Create `client/src/components/agents/workbench/WorkbenchRow.test.tsx`: activation, keyboard semantics, and nested-control behavior.
- Modify `client/src/pages/agents/AgentQualityWorkbench.test.tsx`: shared scope, terminology, collection flows, inspector state, mutation recovery, and unknown usage display.
- Modify `client/src/pages/agents/GlobalAgentQualityPage.test.tsx`: assert the wrapper uses the shared workspace and fleet defaults to Findings.
- Modify `client/src/pages/agents/FleetHeader.tsx`, `client/src/pages/agents/AgentDetailPage.tsx`, and their tests: `Agent Workbench` and `Open Workbench` navigation.
- Create `client/src/components/agents/RunFindingAction.tsx`: query an existing run-sourced Finding or create a prefilled one.
- Create `client/src/components/agents/RunFindingAction.test.tsx`: `Create Finding`, successful linkage, `Open Finding`, and retryable failure.
- Modify `client/src/components/agents/RunReviewSheet.tsx`, `client/src/components/agents/AgentRunSheet.tsx`, `client/src/pages/agents/AgentRunDetailPage.tsx`, and their tests: remove active tuning discussion and show the Finding action for negative verdicts.
- Delete `client/src/components/agents/FlagConversation.tsx` and `client/src/components/agents/FlagConversation.test.tsx` after all active imports are removed.
- Modify `client/src/components/agents/AgentRunsTab.tsx` and `client/src/pages/agents/AgentReviewPage.tsx`: update Workbench links and Title Case labels.
- Modify `client/src/App.tsx` and `client/src/App.test.tsx`: route both scopes through the shared component while preserving redirects.
- Create `client/e2e/agent-workbench.admin.spec.ts`: one seeded, provider-free happy path through Findings, investigation, and Test creation.

### Task 1: Lock Workbench terminology and route ownership

**Files:**
- Modify: `client/src/pages/agents/AgentQualityWorkbench.test.tsx`
- Modify: `client/src/pages/agents/GlobalAgentQualityPage.test.tsx`
- Modify: `client/src/pages/agents/FleetHeader.test.tsx`
- Modify: `client/src/pages/agents/AgentDetailPage.test.tsx`
- Modify: `client/src/App.test.tsx`
- Modify: `client/src/pages/agents/QualityHeader.tsx`
- Modify: `client/src/pages/agents/FleetHeader.tsx`
- Modify: `client/src/pages/agents/AgentDetailPage.tsx`
- Modify: `client/src/pages/agents/GlobalAgentQualityPage.tsx`
- Modify: `client/src/App.tsx`

- [ ] **Step 1: Write failing terminology and shared-route tests**

Assert these exact visible contracts:

```tsx
expect(screen.getByRole("heading", { name: "Workbench" })).toBeVisible();
expect(screen.queryByText(/quality workbench/i)).not.toBeInTheDocument();
expect(screen.getByRole("link", { name: "Open Workbench" })).toBeVisible();
expect(screen.getByRole("link", { name: "Agent Workbench" })).toBeVisible();
```

In the global test, assert that `/agents/quality` renders the same
`data-agent-workbench` root and defaults to the `Findings` navigation item.
Retain the App route-order assertion so the literal global route stays before
`agents/:id`.

- [ ] **Step 2: Run the tests and confirm the expected red state**

Run:

```bash
./test.sh client unit src/pages/agents/AgentQualityWorkbench.test.tsx src/pages/agents/GlobalAgentQualityPage.test.tsx src/pages/agents/FleetHeader.test.tsx src/pages/agents/AgentDetailPage.test.tsx src/App.test.tsx
```

Expected: failures name the old `Quality`, `Quality workbench`, or separate
global-page markup. Do not continue if the new assertions pass accidentally.

- [ ] **Step 3: Implement the minimal naming and route ownership change**

`GlobalAgentQualityPage` becomes a compatibility wrapper:

```tsx
import { AgentQualityWorkbench } from "./AgentQualityWorkbench";

export function GlobalAgentQualityPage() {
	return <AgentQualityWorkbench scope="fleet" />;
}
```

Add a discriminated public prop:

```tsx
export type AgentWorkbenchScope = "agent" | "fleet";

export function AgentQualityWorkbench({
	scope = "agent",
}: {
	scope?: AgentWorkbenchScope;
}) {
	const { id: routeAgentId } = useParams<{ id: string }>();
	const isFleet = scope === "fleet";
	const agentId = isFleet ? undefined : routeAgentId;
	return <AgentWorkbenchContent scope={scope} agentId={agentId} />;
}
```

Change visible labels to `Agent Workbench`, `Workbench`, and `Open Workbench`.
Keep compatibility URLs and internal query keys unchanged in this task.

- [ ] **Step 4: Run the focused tests and make them green**

Run the command from Step 2. Expected: all selected tests pass with no warning
introduced by the naming change.

- [ ] **Step 5: Commit**

```bash
git add client/src/App.tsx client/src/App.test.tsx client/src/pages/agents client/src/pages/agents/FleetHeader.tsx
git commit -m "Rename Agent Quality surface to Workbench"
```

### Task 2: Build the shared contained workspace shell

**Files:**
- Create: `client/src/components/agents/workbench/AgentWorkbenchFrame.tsx`
- Create: `client/src/components/agents/workbench/AgentWorkbenchFrame.test.tsx`
- Create: `client/src/components/agents/workbench/WorkbenchCollectionToolbar.tsx`
- Create: `client/src/components/agents/workbench/WorkbenchCollectionToolbar.test.tsx`
- Create: `client/src/components/agents/workbench/WorkbenchRow.tsx`
- Create: `client/src/components/agents/workbench/WorkbenchRow.test.tsx`
- Modify: `client/src/pages/agents/AgentQualityWorkbench.tsx`

- [ ] **Step 1: Write failing frame and row behavior tests**

Cover one observable contract per test:

```tsx
it("keeps collection navigation and the inspector in one workspace", async () => {
	const { user } = renderFrame();
	await user.click(screen.getByRole("tab", { name: "Findings" }));
	expect(screen.getByRole("region", { name: "Findings collection" })).toBeVisible();
	expect(screen.getByRole("complementary", { name: "Finding details" })).toBeVisible();
});

it("activates a row from the whole row without activating nested controls", async () => {
	const onSelect = vi.fn();
	const { user } = renderRow({ onSelect });
	await user.click(screen.getByRole("button", { name: "Row action" }));
	expect(onSelect).not.toHaveBeenCalled();
	await user.click(screen.getByRole("option", { name: /refund issued/i }));
	expect(onSelect).toHaveBeenCalledOnce();
});
```

Also assert that selected rows carry `aria-selected="true"` and the shared
`tree-row-selected` class, and that the mobile close action returns to the
collection without resetting the selected ID.

- [ ] **Step 2: Run the new component tests and confirm red**

```bash
./test.sh client unit src/components/agents/workbench/AgentWorkbenchFrame.test.tsx src/components/agents/workbench/WorkbenchCollectionToolbar.test.tsx src/components/agents/workbench/WorkbenchRow.test.tsx
```

Expected: module-not-found failures for the three new components.

- [ ] **Step 3: Implement the focused primitives**

The frame API keeps routing/data out of presentation:

```tsx
export type WorkbenchCollection = "tests" | "reviews" | "findings" | "runs";

export function AgentWorkbenchFrame({
	title,
	collection,
	onCollectionChange,
	collectionPane,
	inspectorPane,
	inspectorOpen,
	onCloseInspector,
}: AgentWorkbenchFrameProps) {
	return (
		<section data-agent-workbench className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--bf-radius-feature)] border bg-card">
			<WorkspaceHeader>
				<h1 className="flex min-w-0 items-center px-4 font-display text-base font-semibold">{title}</h1>
			</WorkspaceHeader>
			<div className="flex min-h-0 flex-1">
				<WorkbenchNavigation value={collection} onValueChange={onCollectionChange} />
				<div className={cn("min-w-0 flex-1", inspectorOpen && "max-md:hidden")}>{collectionPane}</div>
				{inspectorOpen ? <aside aria-label={`${singular(collection)} details`}>{inspectorPane}</aside> : null}
			</div>
		</section>
	);
}
```

Use vertical Radix tabs or the existing navigation-selection classes for the
rail, a Select using the same values on narrow screens, `WorkspaceHeader` for
the 48px band, and bounded independent scrolling inside the collection and
inspector. `WorkbenchRow` uses a semantic button or option target, visible
focus, hover background, and persistent `tree-row-selected` selection.

- [ ] **Step 4: Run the component tests and make them green**

Run the command from Step 2. Expected: all new component tests pass.

- [ ] **Step 5: Integrate the frame into `AgentQualityWorkbench`**

Replace the button group, search card, two-column card grid, and detached
Simulation setup card with `AgentWorkbenchFrame`,
`WorkbenchCollectionToolbar`, and `WorkbenchRow`. Preserve existing query and
mutation functions. Keep search within the toolbar for its collection.

- [ ] **Step 6: Run existing and new Workbench tests**

```bash
./test.sh client unit src/components/agents/workbench src/pages/agents/AgentQualityWorkbench.test.tsx
```

Expected: all selected tests pass. Update assertions only where the accepted
interaction changed; retain mutation payload assertions.

- [ ] **Step 7: Commit**

```bash
git add client/src/components/agents/workbench client/src/pages/agents/AgentQualityWorkbench.tsx client/src/pages/agents/AgentQualityWorkbench.test.tsx
git commit -m "Build shared Agent Workbench frame"
```

### Task 3: Unify fleet and agent collections in the shared component

**Files:**
- Modify: `client/src/pages/agents/AgentQualityWorkbench.tsx`
- Modify: `client/src/pages/agents/AgentQualityWorkbench.test.tsx`
- Modify: `client/src/pages/agents/GlobalAgentQualityPage.test.tsx`
- Modify: `client/src/pages/agents/GlobalAgentQualityPage.tsx`

- [ ] **Step 1: Write failing scope and inspector tests**

Add tests that prove:

- fleet scope defaults to Findings and calls `searchFindings` without an agent;
- agent scope defaults to Tests and calls `agentTests` plus
  `latestAgentTests` for the route agent;
- selecting a fleet Finding opens the attached inspector without leaving
  `/agents/quality`;
- choosing an agent filters fleet Tests and Reviews inside the same workspace;
- selecting a row sets a stable URL `selected` parameter and applies the
  selected-row state;
- an empty collection explains its purpose and next action;
- missing monetary cost renders `Unknown`, never `$0.00`.

- [ ] **Step 2: Run the two route-level test files and confirm red**

```bash
./test.sh client unit src/pages/agents/AgentQualityWorkbench.test.tsx src/pages/agents/GlobalAgentQualityPage.test.tsx
```

Expected: fleet scope lacks shared queries/inspector and the existing global
row navigation leaves the route.

- [ ] **Step 3: Implement effective scope and query selection**

Use one effective agent identity:

```tsx
const routeAgentId = useParams<{ id: string }>().id;
const fleetAgentId = params.get("agent") ?? "";
const effectiveAgentId = scope === "agent" ? routeAgentId ?? "" : fleetAgentId;
const defaultCollection: WorkbenchCollection = scope === "fleet" ? "findings" : "tests";
```

Use `searchFindings` for fleet Findings and `findings` for agent Findings.
Enable Tests and Reviews only when `effectiveAgentId` exists. Show the agent
filter in fleet scope and include agent identity in fleet rows. Do not invent
fleet endpoints for Tests or Reviews; the chosen filter selects their real
agent-scoped contracts.

Selection updates URL state without route navigation:

```tsx
updateParams({
	collection,
	selected: `${collection}:${id}`,
});
```

Render existing result, Finding, Review, and run inspectors inside the frame's
attached inspector pane. Move Test creation into the inspector opened by
`Add Test` or `Create Test`; do not leave a permanent form under the list.

- [ ] **Step 4: Run the route-level tests and make them green**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Run the service contract test**

```bash
./test.sh client unit src/services/agentPlatform.test.ts
```

Expected: pass; the shared UI did not fork or bypass typed service methods.

- [ ] **Step 6: Commit**

```bash
git add client/src/pages/agents/AgentQualityWorkbench.tsx client/src/pages/agents/AgentQualityWorkbench.test.tsx client/src/pages/agents/GlobalAgentQualityPage.tsx client/src/pages/agents/GlobalAgentQualityPage.test.tsx
git commit -m "Unify fleet and agent Workbench collections"
```

### Task 4: Retire the tuning conversation and link negative run reviews to Findings

**Files:**
- Create: `client/src/components/agents/RunFindingAction.tsx`
- Create: `client/src/components/agents/RunFindingAction.test.tsx`
- Modify: `client/src/components/agents/RunReviewSheet.tsx`
- Modify: `client/src/components/agents/RunReviewSheet.test.tsx`
- Modify: `client/src/components/agents/AgentRunSheet.tsx`
- Modify: `client/src/components/agents/AgentRunSheet.test.tsx`
- Modify: `client/src/pages/agents/AgentRunDetailPage.tsx`
- Modify: `client/src/pages/agents/AgentRunDetailPage.test.tsx`
- Modify: `client/src/components/agents/AgentRunsTab.tsx`
- Modify: `client/src/pages/agents/AgentReviewPage.tsx`
- Delete: `client/src/components/agents/FlagConversation.tsx`
- Delete: `client/src/components/agents/FlagConversation.test.tsx`

- [ ] **Step 1: Write failing run-to-Finding tests**

Test the new action independently:

```tsx
it("creates a prefilled Finding for a negatively reviewed run", async () => {
	const { user } = renderAction({ verdict: "down", note: "Skipped approval" });
	await user.click(screen.getByRole("button", { name: "Create Finding" }));
	expect(mockCreateFinding).toHaveBeenCalledWith({
		agent_id: "agent-1",
		description: "Skipped approval",
		expected_behavior: null,
		source_kind: "run",
		finding_kind: "problem",
		source_run_id: "run-1",
		source_sequence: null,
		external_ref: null,
	});
});
```

Add a second test where the Findings query already contains the run ID and the
control is an `Open Finding` link. Add an error/retry test that retains the
prefilled note and does not submit twice.

Update run sheet/detail tests to assert `Discussion` and
`Improvement Conversation` are absent, while a negative verdict shows
`Create Finding`.

- [ ] **Step 2: Run the focused tests and confirm red**

```bash
./test.sh client unit src/components/agents/RunFindingAction.test.tsx src/components/agents/RunReviewSheet.test.tsx src/components/agents/AgentRunSheet.test.tsx src/pages/agents/AgentRunDetailPage.test.tsx
```

Expected: missing action component plus existing Discussion/conversation
assertions fail.

- [ ] **Step 3: Implement `RunFindingAction`**

Use typed `agentPlatform.findings` and `agentPlatform.createFinding`. Match an
existing Finding by `source_kind === "run"` and `source_run_id === run.id`.
Invalidate both agent and global Finding query keys on success, then render:

```tsx
<Button asChild variant="outline">
	<Link to={`/agents/${agentId}/quality?collection=findings&selected=findings:${finding.id}`}>
		Open Finding
	</Link>
</Button>
```

When no Finding exists, render a Title Case `Create Finding` button. Use the
trimmed verdict note as the description, falling back to a readable run-based
description. Surface retryable inline feedback on mutation failure.

- [ ] **Step 4: Remove active tuning discussion wiring**

Make `RunReviewSheet` a single review/activity surface. Remove the Discussion
tab, `FlagConversation` import, conversation props, chat props, and `tune`
default. Remove corresponding hooks/state from `AgentRunSheet` and
`AgentRunDetailPage`. Insert `RunFindingAction` after the review control when
the current verdict is `down`.

Remove `FlagConversation.tsx` and its test only after `rg` confirms no active
client import remains. Keep backend APIs and generated types untouched; this
task retires the production UI, not historical storage.

- [ ] **Step 5: Run the focused tests and make them green**

Run the command from Step 2. Expected: all selected tests pass and no test
expects the retired conversation.

- [ ] **Step 6: Check for dead client code and stale copy**

```bash
rg -n "FlagConversation|Improvement conversation|Improvement Conversation|Discussion|quality workbench|Open workbench|Create finding" client/src --glob '*.tsx' --glob '*.ts'
```

Expected: no active UI occurrence of retired conversation copy, lower-cased
action labels, or visible `quality workbench`. Generated schema names may still
contain historical conversation contracts.

- [ ] **Step 7: Commit**

```bash
git add client/src/components/agents client/src/pages/agents
git commit -m "Route negative run reviews through Findings"
```

### Task 5: Add the provider-free browser journey and rendered fidelity pass

**Files:**
- Create: `client/e2e/agent-workbench.admin.spec.ts`
- Modify as evidence requires: Workbench components and their sibling tests

- [ ] **Step 1: Write the failing Playwright happy path**

Seed rows directly through existing API/database fixture helpers. Do not invoke
designer, synthetic, review, or live model providers. The test must:

1. open Agents and choose `Agent Workbench`;
2. confirm fleet Findings is the default;
3. select a Finding and see its attached inspector;
4. choose `Create Test` and verify the prefilled Situation/Expected Behavior;
5. save the Test through the real API;
6. return to Tests with collection state preserved;
7. capture wide and narrow screenshots when `--screenshots` is enabled.

Use semantic roles and labels only. Do not use `waitForTimeout`, retry loops,
or paid provider execution.

- [ ] **Step 2: Run the browser test and confirm the expected red state**

```bash
./test.sh client e2e e2e/agent-workbench.admin.spec.ts --screenshots
```

Expected: failure at the first missing or incorrect Workbench interaction, not
fixture setup or authentication.

- [ ] **Step 3: Fix only evidence-backed UI defects**

Compare the production screenshots to:

- `client/public/mockups/agent-test-explorer/01-tests.png`
- `client/public/mockups/agent-test-explorer/06-finding.png`
- `client/public/mockups/agent-test-explorer/10-reviews.png`
- `client/public/mockups/agent-test-explorer/08-mobile-tests.png`

Check workspace containment, rail proportions, toolbar ownership, row density,
hover/focus/selected states, inspector attachment, Title Case copy, theme
tokens, and mobile pane replacement. Repair each mismatch in the responsible
component and add/strengthen the lowest-level regression test.

- [ ] **Step 4: Rerun the browser test once after the hypothesized fixes**

```bash
./test.sh client e2e e2e/agent-workbench.admin.spec.ts --screenshots
```

Expected: one passing happy path with screenshot artifacts. This is validation
after a fix, not rerun-until-green.

- [ ] **Step 5: Run the mechanical design detector once**

```bash
node /home/jack/.codex/skills/impeccable/scripts/detect.mjs --json client/src/pages/agents/AgentQualityWorkbench.tsx client/src/components/agents/workbench client/src/components/agents/RunFindingAction.tsx
```

Inspect every reported item. Fix confirmed defects; record false positives with
specific evidence in the final fidelity ledger.

- [ ] **Step 6: Commit**

```bash
git add client/e2e/agent-workbench.admin.spec.ts client/src
git commit -m "Verify Agent Workbench user journey"
```

### Task 6: Final focused verification and handoff

**Files:**
- Modify only if a selected check exposes a clear regression

- [ ] **Step 1: Run the complete focused client unit set**

```bash
./test.sh client unit src/pages/agents/AgentQualityWorkbench.test.tsx src/pages/agents/GlobalAgentQualityPage.test.tsx src/components/agents/workbench src/components/agents/RunFindingAction.test.tsx src/components/agents/RunReviewSheet.test.tsx src/components/agents/AgentRunSheet.test.tsx src/components/agents/AgentRunsTab.test.tsx src/pages/agents/AgentRunDetailPage.test.tsx src/pages/agents/AgentReviewPage.test.tsx src/pages/agents/FleetHeader.test.tsx src/pages/agents/AgentDetailPage.test.tsx src/services/agentPlatform.test.ts src/App.test.tsx
```

Expected: zero failed tests, zero skipped tests, and no new console warnings.

- [ ] **Step 2: Run TypeScript and lint checks**

```bash
(cd client && npm run tsc)
(cd client && npm run lint)
```

Expected: both exit zero. Existing warnings must be identified; new warnings
from changed files are defects.

- [ ] **Step 3: Rerun the single browser happy path on the exact candidate**

```bash
./test.sh client e2e e2e/agent-workbench.admin.spec.ts --screenshots
```

Expected: pass without retries and without live provider calls.

- [ ] **Step 4: Inspect final screenshots with `view_image`**

Inspect wide and narrow production screenshots side-by-side with the accepted
prototype. Write a fidelity ledger covering at least copy, workspace anatomy,
navigation, toolbar, row states, inspector, typography, density, theme, and
responsive behavior. Continue editing for any fixable mismatch.

- [ ] **Step 5: Verify repository state and commit the exact candidate**

```bash
git diff --check
git status --short
git log -5 --oneline
```

Commit only the intended Workbench changes. Do not reset, delete stack data, or
touch unrelated user work.

- [ ] **Step 6: Push and report bounded verification**

Push `feature/durable-agent-platform-backend`. Report exact test counts and
commands, broader suites not run, fixed defects with file/line evidence,
remaining backend/product gaps, and the unchanged debug URL/login. Do not
declare the entire durable Agent Platform feature complete.
