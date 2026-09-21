# Test workbench design correction — review and verification

Status: UI correction implemented; primary code and visual review complete for the exercised states. Ready for user visual review. This records the UI correction, not a claim that the entire uncommitted backend replacement has received a new full-suite gate.

## Delivered
- Shared Bifrost ListPageHeader, agent backlink, persistent desktop tabs and a single content scroller; normal mobile document flow.
- Searchable findings and tests; flagged-run proposal work, generation and editing opened on demand.
- Readable scenario/tool-response/expectation inspection; parameter-aware expectation summaries shared with authoring.
- Compact proposed-change summary; profile selection and Run tests; failure-first Expected/Actual results with supporting diagnostics below.
- Existing PromptDiffViewer and explicit reviewed apply, preserving stale protection, prompt history and run verdicts.
- Consistent entry-point wording; run-review Discussion; durable evidence inside existing Activity / Advanced.

Principle: Consistency and design systems. References inspected: Integrations, ExecutionHistory / ExecutionDrawer, AgentDetailPage, shared PageWorkspace, ListPageHeader, ListToolbar, SearchBox and existing agent components. No new visual system or backend contracts were introduced by this correction.

## Review dispositions
Primary rejected the first pass because desktop header scrolling was not actually constrained, the proposed-change block remained oversized, wording remained inconsistent, and supporting details preceded failures. OpenCode corrected these and added a browser viewport assertion.

Primary identified that hash-only result evidence could disappear after consolidating disclosures. The visibility condition now includes the hash and has a regression test. Invalid expectation drafts remain guarded; unknown expectation definitions and typed values remain preserved.

Development failures were fixed: JSX grouping when relocating details; incomplete typed test fixtures; disclosure visibility assertions; asynchronous component assertions; and browser selectors tied to old headings or ambiguous repeated names. Existing timeout values were retained; no retries, skips or timeout inflation were added.

## Verification commands
Primary independently ran both client quality commands with successful process exit codes (not piped output):

```bash
(cd client && npm run tsc -- --force)
(cd client && npm run lint)
git diff --check
```

Lint: zero errors; existing warning in `client/e2e/support/seed-review-pack.ts:197` (`no-console`).

OpenCode ran the following focused component command: 18 files, **132 passed**.

```bash
./test.sh client unit src/components/agents/TuneHeader.test.tsx src/components/agents/evaluation/expectationSummaries.test.ts src/components/agents/evaluation/FindingsPanel.test.tsx src/components/agents/evaluation/CaseEditor.test.tsx src/components/agents/evaluation/SuiteWorkspace.test.tsx src/components/agents/evaluation/CandidateEditor.test.tsx src/components/agents/evaluation/ExecutionResults.test.tsx src/components/agents/evaluation/MatrixPanel.test.tsx src/components/agents/evaluation/TestDesigner.test.tsx src/components/agents/evaluation/SuiteEditor.test.tsx src/components/agents/evaluation/ChangesWorkspace.test.tsx src/components/agents/RunDurableEvidence.test.tsx src/components/agents/RunReviewSheet.test.tsx src/pages/agents/AgentTuneWorkbench.test.tsx src/pages/agents/EvaluationExecutionLink.test.tsx src/pages/agents/AgentDetailPage.test.tsx src/pages/agents/AgentReviewPage.test.tsx src/pages/agents/AgentRunDetailPage.test.tsx
```

Combined live browser command: **7 passed**, including authentication setup (six feature tests across five specs).

```bash
./test.sh client e2e e2e/agents-quality-workbench.admin.spec.ts e2e/agents-improvement-journey.admin.spec.ts e2e/agents-quality-promotion.admin.spec.ts e2e/agent-run-evidence.admin.spec.ts e2e/agents-review-verdict.admin.spec.ts
```

Broader backend, full Vitest, full Playwright and pre-pr suites were not rerun for this UI correction. Earlier baseline gates are recorded separately; they must not be represented as verification of the latest changes. No commit, push, PR, merge or deployment was performed.

## Primary visual review
Reviewed actual desktop and 390px mobile captures for populated and empty Evidence, selected suite Tests, scenario and expected-behavior authoring, pre-run Changes, populated failed results, apply diff/reason/actions, and run Advanced evidence. Header/tabs remain visible in the scrolled desktop result capture. Mobile results show actual failed results before apply rather than the previous post-apply state.

Primary also reviewed desktop/mobile Generate tests controls (without submitting generation), saved-test inspection, proposed-change prompt/settings, mobile pre-run setup, and the empty configured-tool-response section. The populated mock-response editor was reviewed in source and existing component tests, not a newly populated screenshot. Some desktop editor captures show the same viewport; mobile settings show delegate/system-tool selectors and advanced controls. Running state, large multi-profile/suite collections, keyboard-only and comprehensive accessibility evaluation are not proven by these screenshots. Component tests cover status logic; no accessibility conformance is claimed.

Executor: `opencode-go/muse-spark-1.3-contributor`, sessions `ses_f42cc4fecffeVz3pSJpEqP2Xqj` (implementation and primary follow-up) and `ses_f4290869bffetdGg1lL0aLu7nb` (capture-only). Primary retained design and final review ownership. All pre-existing backend edits were preserved.

Intent check: the workbench now groups finding review, repeatable tests, comparison and explicit apply around the operator's task, using existing Bifrost patterns. User visual sign-off remains distinct from implementation and test completion.

## Final capture pass
The capture-only update preserved product behavior and existing assertions. Its first run found an ambiguous new screenshot selector, corrected with an exact match. The final combined run passed all seven checks in 54.2 seconds. Primary independently linted the changed browser spec with `./node_modules/.bin/eslint e2e/agents-improvement-journey.admin.spec.ts` from `client/` (exit 0), parsed the final Playwright report, and ran `git diff --check`.

Representative artifacts (under `client/playwright-results/artifacts/agents-improvement-journey-1d24f-res-apply-preserves-verdict-platform-admin/`):
- `journey-evidence-desktop.png`, `journey-evidence-mobile.png`
- `journey-tests-desktop.png`, `journey-tests-mobile.png`
- `journey-changes-before-run-desktop.png`, `journey-changes-before-run-mobile.png`
- `journey-results-desktop.png`, `journey-results-mobile.png`
- `designer-disclosure-desktop.png`, `designer-disclosure-mobile.png`
- `saved-test-inspection-desktop.png`, `saved-test-inspection-mobile.png`
- `candidate-editor-settings-mobile.png`, `journey-apply-review-mobile.png`

No unresolved failing selected check remains. This is not user visual approval or completion of the broader release/merge gates.
