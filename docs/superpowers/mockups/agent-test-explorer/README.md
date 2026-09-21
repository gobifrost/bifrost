# Agent test explorer — design proposal

Status: proposed for visual review, not approved for product implementation.

## Direction

Use one collection-and-inspector component, following FilesExplorer. The default content is the agent's tests, named as should/should-not behaviors. Each row exposes the latest outcome, duration, cost and run time. Search, run mode, selection, profiles and Run All belong to the collection toolbar. No suite setup is required before a user can define or run a test.

The inspector handles results, test editing, investigation, review definition, Draft Changes and explicit apply review. Draft comparison adds live/draft columns to the existing list. Reviews, Findings and Run History are collections inside the same component. They are not steps in a wizard. On narrow screens the inspector replaces the collection, preserving selection on return.

This addresses Content and information architecture (tests are primary), Consistency and design systems (Files' collection/inspector model and Bifrost tokens), and User intent and task success (review a run, inspect a failure, evaluate evidence, change the agent, compare, review).

## Connected states in the interactive mockup

- Tests: searchable should/should-not list; run-mode control defaulting to Run Simulation with Evaluate Recorded Runs as the alternate; individual, selected, or all-test execution; profile picker (simulation only).
- Result: expected versus observed behavior, situation, readable tool checks, profile outcomes, trace and scenario.
- Test editor: name, Situation/When This Applies, Expected Behavior, readable tool checks, Generate Draft (illustrative, no model runs), and a collapsed Simulated Environment section holding mocked responses and sequence overrides.
- Draft Changes: instruction additions, attached tools, and Propose Tool (Simulation Only badge). Comparing a candidate with a proposed tool works; Apply stays blocked until the proposed tool is removed and the comparison rerun.
- Apply: explicit change review and reason; blocked state explains the unresolved proposed tool; no automatic update after a passing run and no production implementation action.
- Reviews: plain-English statements with run scope, cadence (Manual or Scheduled/Daily Demo, preview-only), and evidence instructions with a ticket-link template. Save and Run Review simulate state and lead to linked findings.
- Findings: searchable problems and opportunities; Investigate (primary), Create Test, Draft Changes, Dismiss. Investigation shows selectable source runs, linked tests, a recent relevant failure with version/profile context, and Evaluate Recorded Runs.
- Evaluate Recorded Runs: inspector to select saved runs and tests; results show Passed, Failed, Not Applicable and Insufficient Evidence with explanations. The agent is never rerun. An incomplete trace cannot pass a forbidden-call check.
- History: saved runs labeled Simulation, Draft Comparison, or Recorded Evaluation.

An opportunity is not inherently a test failure. It becomes a test only when the user decides what behavior to expect. Creating that test does not dismiss its source finding. A test pass never dismisses a finding.

Evidence samples render Markdown (headings, bullets, quotation, ticket links) through a small local formatter with user text escaped. The product reuses the MarkdownContent component; the mockup notes this where evidence renders.

## Alternatives

A notebook makes each scenario easy to edit but makes scanning results across many tests harder. A setup wizard puts suites, candidates and profile configuration ahead of the user's task. The list/inspector direction supports both fast scanning and focused editing without changing screens.

## Backend questions before implementation

Existing immutable suite versions may be internal execution snapshots for an agent's default test collection. Existing named suites must not be silently flattened or deleted; migration and advanced grouping need a separate reviewed decision. The mockup removes the prerequisite, not the backend data.

Latest per-test outcomes across runs and profiles need an explicit read contract. A comparison must preserve the precise test version, agent version and profile configuration, including results invalidated by edits. Local agent edits should freeze into a candidate when comparison starts. Explicit apply must retain authorization, stale-agent protection and history. Synthetic-tool isolation and shared PlatformJob progress remain unchanged.

Review scheduling, recorded-evidence admission with completeness/applicability semantics, and proposed-tool namespace/binding validation need reviewed additive contracts (Phase 1 of the unified workbench plan). The mockup demonstrates the interaction only.

The component should accept agent scope so it can also serve the previously discussed Agents-level Testing collection with an agent filter. This mockup demonstrates the single-agent scope; fleet scope and automatic review scheduling are not yet mocked.

## Preview and validation

Open `index.html` directly, or `/mockups/agent-test-explorer/index.html` on the existing debug client. Both copies are identical and self-contained, including local fonts. Sample data and simulated runs are explicitly labelled. No API, model, tool or agent changes occur; Reset restores browser-memory state.

Browser check: `docker exec bifrost-debug-69c4f3b1-client-1 node /tmp/check-test-explorer.cjs` (the script is retained as `check.cjs`; copy it to that path first). Checked reviews list/inspector/run, markdown evidence with ticket link, finding investigation, recorded evaluation with all four verdicts, test editing with generated draft, draft comparison, proposed-tool apply block and removal-rerun recovery, finding-to-test, history mode labels, run-mode control, single-test comparison scoping, and mobile (tests, result, reviews) without script errors or horizontal overflow. Desktop and mobile screenshots are next to the served HTML. This is a mockup-only change; repository backend, Vitest and full Playwright gates were not rerun.
