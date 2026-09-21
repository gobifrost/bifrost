# Handoff: connected agent quality mockup

Executor: OpenCode, `opencode-go/muse-spark-1.3-contributor` (locally available; explicitly selected by the user).
Owner/reviewer: originating agent. Worktree: `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`, baseline HEAD `70ae5f3c7` plus pre-existing dirty work. All product/backend edits belong to the ongoing feature: preserve them, do not reset anything.

Read `2026-09-20-unified-agent-quality-workbench.md` (same directory), especially product model and Phase 0. Implement only Phase 0, following these exact design decisions. Do not redesign the backend or product UI.

## Ownership

Only `client/public/mockups/agent-test-explorer/` and `docs/superpowers/mockups/agent-test-explorer/`. The two index.html copies must stay identical. Existing HTML is the baseline; preserve fonts, palette, Files-like collection/inspector layout, mobile behavior, and standalone operation. No external libraries, scripts, API/model calls or fetching evidence. No commits, pushes, installs, credentials/configuration changes or unrelated cleanup. Report contract gaps rather than expanding scope.

## Exact changes

1. Keep Tests default. Add Reviews in local rail (and mobile collection selector), before Findings. Reviews collection rows show statement title, last reviewed/count, manual or scheduled. Selecting opens inspector: statement textarea, run scope, cadence choice (manual/daily demo), evidence instructions. Seed a review asking to find approval violations and efficiency opportunities. Save and Run Review simulate state and lead to linked Findings. Schedules are clearly preview-only; never claim a real timer is active.
2. Evidence should visually demonstrate Markdown: headings, bullets, quotation and a ticket link `https://example.com/tickets?id=1042`. Render controlled samples safely (no raw user HTML injection); escape entered text. Explain the product will reuse MarkdownContent. Review evidence instructions show the ticket template. No real evidence fetch.
3. Finding inspector: Investigate primary; Create Test, Draft Changes, Dismiss available. Investigate inspector includes source run rows selectable by checkbox, linked existing tests, a recent relevant failure with version/profile context, and Evaluate Recorded Runs. Preserve context when switching inspector. Create Test remains available; duplication is not required.
4. Add explicit run-mode control in Tests toolbar, default Run Simulation. Alternate Evaluate Recorded Runs opens inspector to select illustrative saved runs and tests. Run All remains convenient and label/context must make mode clear. Evaluate selected runs shows Passed, Failed, Not Applicable, Insufficient Evidence results with explanations. No agent rerun. At least one missing-trace example must not pass a forbidden-call assertion. History labels recorded evaluation vs simulation distinctly.
5. Test editor: name, Situation/When This Applies, Expected Behavior, readable tool checks (Must Not Close Ticket, Must Not Email Customer, Must Add Private Note). Generate Draft fills an illustrative setup from these inputs. Collapse Simulated Environment by default; advanced controls show mocked responses and sequence overrides there. Generation is clearly simulated, with no claim a model ran. Save draft/test and inspect behavior without losing collection state.
6. Rename Try changes to Draft Changes and normalize action casing across this prototype (titles/actions title case, prose normal). No separate Changes tab. Draft inspector edits instructions and attached tools as today; add Propose Tool with name, inputs/output description, behavior. Show Simulation Only badge on proposed tools. Simulations can compare the candidate with a proposed tool; Apply is blocked while one is unresolved, with an actionable explanation. Removing it and rerunning can enable normal explicit apply. Do not invent a production implementation action.
7. Preserve test result correctness in prototype state: comparing one test cannot mark all passed; editing draft/test invalidates relevant comparison; profile/result context truthful for sample state; incomplete outcomes never green. Add a visible reset. All new controls either work locally or clearly identify an illustrative unavailable capability.
8. Update Design Notes and README with mode distinction, review lifecycle, proposed tools, remaining contract gates, and scope. Extend check.cjs for these flows. Use existing container `bifrost-debug-69c4f3b1-client-1` with Node/Playwright/Chromium already available. Browser opens `file:///app/public/mockups/agent-test-explorer/index.html`. Copy script to `/tmp/...` in container; module is `/app/node_modules/playwright`. No stack restart. Capture desktop/mobile states in allowed directory; avoid toast overlays in screenshots. Do not run product suites for a standalone mockup.

## Done means

Runnable standalone mockup showing review→finding→investigate→evaluate existing tests, Create Test→generated setup, Draft Changes→proposed tool→simulation→apply blocked, normal comparison/apply, run history and responsive inspector. No browser errors or horizontal mobile overflow. Same HTML in both paths. Primary will independently inspect code, browser journeys/screenshots and intent before acceptance.

Write `docs/superpowers/mockups/agent-test-explorer/handoff-report.md` listing changed files, tested commands/results, unimplemented items and contract questions. Stop after this packet. Do not begin product implementation.

## Executor switch

User selected Muse Spark 1.3 Contributor. Previous DeepSeek session `ses_f40872e42ffe0BN995EK3IUZhK` was stopped before starting Muse. Preserve and inspect any partial prototype changes. Same ownership and acceptance criteria; no concurrent writer.

Muse execution launched via `/tmp/launch-workbench-muse.py`; log `/tmp/workbench-mockup-muse.jsonl`; unified exec handle `42693`. Session ID pending first event. Implementation and primary acceptance remain pending.

## Completion monitoring

Muse session: `ses_f408590ecffeLgmMDztpA1DA5g`. An independent user-systemd watcher `bifrost-workbench-muse-notify.service` tracks executor PID/start identity and sends one ntfy message on return (or attention after two hours). Watcher: `/tmp/bifrost-workbench-watch/watch.py`; sanitized send result: `/tmp/bifrost-workbench-watch/result.json`. This survives the chat turn but does not automatically run primary review. Notification explicitly says review pending; a report's existence does not establish acceptance. Use the notify skill for the separate reviewed-ready notice. Do not start a second writer while Muse is active.

## Operating correction

User clarified that executor-completion notifications should be consumed by the owner, not relayed through the user. The mockup-only packet was an intermediate phase, not the authorized task's stopping point. The notification watcher has completed and will not be used for raw executor completion again. Primary review corrected prototype defects and reran browser checks. Broader execution continues: Muse is auditing contracts in session `ses_f4077eefcffe093ztb0yqItxkQ`, log `/tmp/workbench-contracts-muse.jsonl`, exec handle `70242`; only two contract/handoff documents are writable. Primary will review and issue the next code packet without waiting for the user to report completion.
