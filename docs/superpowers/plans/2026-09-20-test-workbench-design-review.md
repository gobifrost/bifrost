> Superseded for navigation, authoring flow, wording and scope by `2026-09-20-unified-agent-quality-workbench.md` and the accepted agent-test-explorer prototype. Retained as historical review evidence; do not implement the former Evidence / Tests / Changes tabs or suite-first flow from this document.

# Cohesive test workbench — design correction

Owner: primary design reviewer. Implementation: OpenCode. Status: implemented and reviewed by primary for exercised states; user visual review pending. See `2026-09-20-test-workbench-verification.md` for exact checks and limits.

## Intent and scope
An operator should move from evidence to a repeatable test, compare a proposed agent change across profiles, understand failures, and explicitly apply a reviewed change. The whole experience must read as one Bifrost workbench. This replaces the old tuning presentation, not backend contracts. Keep Evidence / Tests / Changes & Results navigation and existing deep links. Fleetwide tabs remain deferred.

## References inspected
- `client/src/pages/Integrations.tsx`: ListPageHeader, ListToolbar, SearchBox, one clear create action, shared collection styling.
- `client/src/pages/ExecutionHistory.tsx`: DataTable scan hierarchy and selection-to-detail.
- `client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx`: shared Sheet, contextual title, close and action placement.
- `client/src/pages/agents/AgentDetailPage.tsx`: agent breadcrumb, 1400px workspace, responsive wrapping.
- `client/src/components/layout/PageWorkspace.tsx`: shared page sizing and scroll ownership.
- Existing PromptDiffViewer, ModelProfileSelector, MultiCombobox, UI tables, dialogs, tabs and design-tokens are mandatory references rather than new visual primitives.

## Required corrections

### 1. One shell, correct task
Principle: Consistency and design systems.
Replace the old TuneHeader presentation with a ListPageHeader-based `Test workbench`, agent backlink, description `Turn findings into tests, compare agent changes, and review results.` Remove production statistics and obsolete dry-run copy from this workspace; they already belong to the agent overview. Keep a compact named suite / proposed change / profiles context line where relevant. Tabs and header must remain visible while the content region scrolls on desktop; mobile uses normal document flow. Use existing typography/tokens, no decorative hero or dashboard cards.

### 2. Collections first, editing on demand
Principle: Content and information architecture.
Evidence: findings get the shared search/toolbar pattern and a compact list; record-finding form opens on demand. Keep flagged-run review/proposal generation as a clearly labeled secondary section, not a permanently open competing editor. Preserve discussion, history and run verdict behavior.
Tests: suite selection and New suite in a compact toolbar. The selected suite opens with its name, draft/published status, test count and publish action. Put test cases immediately below, with Search tests, Add test and secondary Generate tests. Suite settings and generation are opened on demand; they must not push the test collection below several full forms. Published tests remain inspectable via readable scenario/tool responses/expectations. Use a bounded detail/editor region (shared Sheet or explicit inline detail) rather than nesting cards for every field. Keep draft acceptance explicit and visible in review, not hidden inside raw JSON.
Changes & Results: start with a compact proposed-change summary and Edit/Create action. Large candidate editor opens only when requested. Keep model profile selection and Run tests together; show saved results immediately below, without scrolling through the candidate form. Applying remains a distinct explicit review using PromptDiffViewer and required reason, with stale protection intact.

### 3. Familiar test results
Principle: Visual hierarchy.
A saved run leads with status and completed / passed / failed counts and selected profile names. Present each profile/live/proposed execution as a compact row using shared table/list conventions; select one to inspect tests. Do not create card-inside-card-inside-card containment. Test detail leads with failed expectations and Expected / Actual, then passed checks. Show comparison counts only when comparison data exists (no meaningless em-dash regressions on a live-only run). Consolidate supporting usage, tool-call/output differences and raw evidence under one labeled detail area with clear subsection labels; raw payloads are tertiary, not the default reading surface. Keep links to real run evidence. Never imply the baseline/live side uses real tool side effects: both test sides use mocked tools.

### 4. Plain, consistent authoring language
Principle: Forms, error prevention, and recovery.
Use `Add test`, `Save test`, `Generate tests`, `Expected behavior`, `Tool responses`, `Proposed changes`, `Live agent`, `Review and apply`. Explain immutable versions near Publish only; avoid frozen/synthetic/assertion jargon in primary controls. Expectation rows must summarize parameters: `Must call lookup`, `Must not call send_email`, `Completes successfully`, `Output customer.id must equal 42`, `Uses at most 500 tokens`; custom labels may remain titles with an accurate summary below. Put raw type/JSON in Advanced. Use actual units and typed values; preserve unsupported definitions losslessly. Retain all recently added invalid-edit guards and error recovery. Reuse this readable expectation rendering in saved-case inspection rather than defaulting to JSON.

### 5. Every connected surface
Principle: Layout, spacing, and responsive behavior.
Review all evaluation components (SuiteEditor, CaseEditor, TestDesigner, FindingsPanel, CandidateEditor/Promotion, MatrixPanel, ExecutionResults, PlatformEvidence), workbench, TuneHeader, proposal editor, run Advanced durable evidence, evaluation deep-link component, and entry-point labels on agent detail/review plus Testing model setting. Apply the same naming, section rhythm (existing space-4/6), control sizing and responsive behavior. Do not redesign existing unrelated pages. Keep narrow screens usable, keyboard focus visible, all errors/action outcomes clear, and no horizontal page overflow. Drawers/dialogs keep header/footer outside long-content scrollers and restore focus to their trigger.

## Acceptance evidence
Capture whole visible workbench pages at desktop and 390px mobile: Evidence populated/empty, Tests populated with selected suite, test authoring and saved test inspection, Changes & Results before execution and populated failure, apply review. Include a running state from deterministic browser fixtures if practical; do not fake runtime success. Capture page-level framing plus focused detail as needed, not only cropped panels. Review screenshots against reference patterns, not just test success. Focused component tests must cover changed interactions, preserved invalid edits, search/selection, acceptance and stale apply. Run forced TypeScript check, lint, and relevant browser journeys. No blind reruns, timeout inflation or removed coverage to get green.

Intent check: these changes must make it easy to find a test, understand its expectations, run it across profiles, and decide whether a proposed change helps. Visual decoration alone does not satisfy this review.
