# Handoff report: connected agent quality mockup (Phase 0)

Executor: OpenCode, `opencode-go/muse-spark-1.3-contributor`.
Date: 2026-09-20. Worktree: `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`.
Baseline: HEAD `70ae5f3c7` plus the pre-existing dirty feature work, which was preserved untouched.

## Changed files (owned paths only)

- `client/public/mockups/agent-test-explorer/index.html` — Phase 0 implementation (see below).
- `docs/superpowers/mockups/agent-test-explorer/index.html` — byte-identical copy (verified with `diff -q`).
- `docs/superpowers/mockups/agent-test-explorer/check.cjs` — extended browser journey (new names, new flows, new screenshots, new assertions).
- `docs/superpowers/mockups/agent-test-explorer/README.md` — mode distinction, review lifecycle, proposed tools, remaining contract gates, scope.
- `client/public/mockups/agent-test-explorer/*.png` — regenerated `01`–`09`, added `10-reviews`, `11-investigate`, `12-evaluate`, `13-apply-blocked`, `14-mobile-reviews`.
- `docs/superpowers/mockups/agent-test-explorer/handoff-report.md` — this file.
- Design Notes overlay (inside `index.html`, `notes()`) updated with the review/mode/proposed-tool sections. No product code, backend, config, install, commit, or push was touched.

## What was implemented (handoff items 1–7)

1. Reviews collection in the local rail (Tests filters, then Reviews, Findings, Run History) and in the mobile collection selector. Rows show statement title, last run, runs-reviewed count, and Manual/Scheduled pills. Inspector: statement textarea, run-scope input, Manual/Daily-Demo cadence radios, evidence-instructions textarea with live Markdown preview and the ticket template. Seeded review "Find approval violations and efficiency opportunities" plus a scheduled hygiene review. Save/Run Review simulate state, update last-run, and surface linked findings. Schedule copy always says preview-only; no timer is claimed.
2. Evidence renders Markdown (headings, bullets, quotation, ticket link `https://example.com/tickets?id=1042`) via a small local `md()` formatter that escapes input first, then linkifies markdown links and bare URLs without double-processing. Finding evidence and review evidence preview both use it, each noting the product reuses MarkdownContent. No fetching.
3. Finding inspector: Investigate (primary), Create Test, Draft Changes, Dismiss. Investigation panel: checkbox-selectable source runs (incomplete-trace runs labeled), linked tests with back-navigation, a recent relevant failure with Support Fast / Failed / Agent v24 / timestamp context, source evidence, and Evaluate Recorded Runs. Finding selection and investigation selections live in state, so switching inspectors preserves context.
4. Run-mode segmented control in the Tests toolbar, default Run Simulation. Evaluate Recorded Runs mode replaces the profile picker with an explanatory context line and turns the primary action into Evaluate All / Evaluate N Selected, which opens an inspector to pick saved runs and tests. Results show Passed, Failed, Not Applicable, Insufficient Evidence with per-result explanations. Recorded run `run-2187` (trace incomplete, tool calls missing) yields Insufficient Evidence against forbidden-call checks — it can never pass them. History labels Simulation, Draft Comparison, and Recorded Evaluation distinctly.
5. Test editor: Name, Situation/When This Applies, Expected Behavior, readable tool checks (Must Not Issue Refund Before Approval, Must Not Close Ticket, Must Not Email Customer, Must Add Private Note). Generate Draft fills an illustrative setup from the name and states no model ran. Simulated Environment (tool, response behavior, sequence overrides) is a collapsed `<details>`. Save preserves collection filter/search/selection and lands on the result inspector.
6. Try Changes renamed to Draft Changes everywhere; titles/actions normalized to title case, prose left sentence case. Draft inspector keeps instructions/tools, adds Propose Tool (name, inputs/output, behavior) with a Simulation Only badge. Comparison runs with a proposed tool attached; Apply is then blocked with an actionable explanation and a Remove Proposed Tool and Rerun action, which restores normal explicit apply. No production implementation action exists.
7. Correctness: single-test runs/comparisons scope to the selected test (asserted); editing a test, toggling a check, generating a draft, editing draft instructions/tools, or adding/removing a proposed tool invalidates the relevant comparison; profile results come from per-test sample data labeled Agent v24 / sample data; Insufficient Evidence and Not Applicable pills are amber/muted, never green; Reset restores all state; every new control works locally or is labeled preview-only/illustrative.

## Tested commands and results

- `docker exec bifrost-debug-69c4f3b1-client-1 node /tmp/check-test-explorer.cjs` (repo script `check.cjs` copied to that container path; container's `/app/node_modules/playwright` used; no stack restart): **PASS** — reviews, markdown evidence, investigate, recorded evaluation, generated setup, proposed-tool apply block, scoped runs, mode control, history labels, mobile; no browser errors.
- Assertions inside the check: apply completes only after proposed-tool removal + rerun; non-draft single run leaves other tests unchanged; draft single compare yields `comparedIds == [1]`; evaluation shows all four verdicts; history contains a Recorded Evaluation label; no mobile horizontal overflow at 390px.
- `node --check` on the extracted inline script: pass. `diff -q` on the two `index.html` copies: identical.
- Not run (per contract: standalone mockup, no product suites): backend pytest/Vitest/Playwright suites, `test.sh quality api`, client `tsc`/`lint`, type regeneration.

## Unimplemented items

None of the seven handoff items is unimplemented. Out-of-scope-by-contract items not attempted: any product/backend implementation, fleet-scope collections, real review scheduling, and production binding of proposed tools.

## Contract questions

1. The evaluate inspector's run/test preselection and verdict rules are illustrative placeholders; Phase 1 must define the recorded-evidence admission, completeness/applicability, and judge-freeze contracts before product work.
2. Proposed-tool namespace, collision handling, and production-binding validation are open Phase 1 decisions; the mockup only demonstrates the Simulation Only identity and the apply guard.
3. Review scheduling semantics (windows, overlap dedupe, disabled schedules) remain Phase 1/3 contract work; the mockup asserts preview-only everywhere.
4. Screenshots `01`–`09` were regenerated against the new UI and five new captures were added; primary should confirm the set is sufficient for acceptance.

## Primary review after executor return

Independently reran the Docker browser journey and inspected investigation, evaluation and editor screenshots. Found and corrected editor generation losing typed fields, edits leaking through Cancel, stale comparisons after profile changes, and selected-test evaluation using stale selection. Extended browser checks for generation preservation, cancel isolation and profile invalidation; rerun passed. These checks do not establish production correctness. Recorded evaluation is illustrative and must use explicit completeness/applicability contracts in Phase 2. The evaluation result view still shares a narrow inspector with selection controls; production design should place results ahead of collapsed input selection.
