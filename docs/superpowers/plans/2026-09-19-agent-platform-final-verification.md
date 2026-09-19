# Durable Agent Platform final verification

Branch: `feature/durable-agent-platform-backend`  
Worktree: `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`  
Verification date: 2026-09-19

## Result

The durable runtime, Agent Debugger, and Evaluation Studio are implemented and
locally verified. The final Playwright gate passed with 169 expected tests and
zero skipped, flaky, or unexpected outcomes. Nothing was pushed, merged, or
deployed.

The browser repair preserves real native middle-click coverage. Chromium
correctly created and navigated a new target, but Playwright 1.62.1 could fail
to emit a `page` event while its adapter waited for a non-initial navigation.
`resource-navigation.admin.spec.ts` now observes the native target through the
public Chromium CDP-session API, checks its expected path, confirms the source
page did not navigate, and closes the target. It retains the existing five- and
ten-second bounds and the repository's zero-retry configuration.

## Delivered behavior

- Durable AgentRuns persist claims, checkpoints, fenced leases, restart
  recovery, timers, delegation, caller-owned outputs, and completion events.
- Agent Debugger shows the run tree, journal, snapshots, checkpoints, lease,
  wake state, and runtime usage while retaining links to the existing run
  detail.
- Evaluation Studio freezes cases and candidates, compares baseline and
  candidate evidence, supports reviewed historical inputs for Designer drafts,
  and requires a reviewed current-production diff before applying changes.
- Synthetic evaluation runs use the simulator and cannot dispatch real workflow
  tools. Testing uses its dedicated model assignment.
- Existing provider caching remains unchanged; persisted runtime cache metrics
  are visible without counting summarizer usage as evaluation runtime usage.

## Verification

| Command | Result | Evidence |
| --- | --- | --- |
| `./test.sh all` | 8,645 passed; no failures, errors, or skips | `/tmp/durable-backend-full-final5.xml` |
| `./test.sh tests/unit/jobs/platform/test_agent_evaluation_designer_notifications.py` | 7 passed | `/tmp/durable-notification-final.xml` |
| Focused terminal-completion suite | 73 passed; no failures, errors, or skips | `/tmp/durable-terminal-bridge-focused-verified.xml` |
| `./test.sh quality api` | Pyright: 0 errors and 0 warnings; Ruff clean | `/tmp/durable-terminal-bridge-quality-final.log` |
| `./test.sh client unit` | 519 files, 3,121 passed | `/tmp/durable-ui-unit-gate-final.log` |
| `./test.sh client e2e --screenshots e2e/agent-debugger.admin.spec.ts e2e/agent-evaluation-studio.admin.spec.ts` | 3 passed | `/tmp/durable-ui-bridge-visual-focused.log` |
| Focused native-tab exposing command | 1 passed in 14.8 seconds | `/tmp/durable-opencode-native-cdp.jsonl` |
| `./test.sh client e2e` | 169 passed; 0 skipped, flaky, or unexpected | `client/playwright-results/results.json` |
| `(cd client && npm run tsc)` | Passed | `/tmp/durable-ui-final-tsc.log` |
| `(cd client && npm run lint)` | 0 errors; 1 existing warning | `/tmp/durable-ui-final-lint.log` |

Focused terminal-completion suite:

```bash
./test.sh tests/unit/services/test_agent_run_evaluation_completion.py tests/unit/services/test_agent_run_consumer.py tests/unit/jobs/platform/test_agent_evaluation.py tests/unit/jobs/platform/test_agent_evaluation_completion_race.py tests/unit/test_import_hygiene.py
```

Focused native-tab exposing command:

```bash
docker compose -p bifrost-test-69c4f3b1 -f docker-compose.test.yml --profile client run --rm --no-deps playwright-runner node e2e/support/run-playwright.mjs e2e/resource-navigation.admin.spec.ts --no-deps
```

The backend full suite predates the two small UI-discovered completion fixes.
Those changes received their focused backend coverage, API quality, and the
final live browser coverage above. The full Vitest, TypeScript, and lint results
predate the native-tab test-only repair; the final full browser gate exercised
that repair. `./test.sh pre-pr`, merge-queue checks, registry publication,
signing, attestation, and deployment were not run.

Lint retains an existing `no-console` warning in
`e2e/support/seed-review-pack.ts:197`; it has no errors.

## Live evidence and limits

- Worker restart during a 45-second sleep resumed the same run on attempt 2 at
  checkpoint 3 with one logical tool call. Evidence:
  `/tmp/durable-live-resume-final.jsonl`.
- Two repeated long-prefix OpenRouter requests recorded 3,594 input tokens;
  cache reads increased from 0 to 3,456, or 96.16% reuse. Short synthetic
  cases recorded no cache reads. Evidence:
  `/tmp/durable-live-cache-result.json`.
- The Designer browser journey selected historical input, queued generation,
  received the post-materialization notification, reviewed the draft, and
  accepted frozen version 2. Evidence: `/tmp/durable-designer-browser.log`.

External effects are not claimed to be exactly once. Uncertain writes require
reconciliation or explicit recovery. Historical debugger fork and replay are
outside v1. The cache result is one measured example, not a fleet-wide savings
claim. OpenChamber was used only as a research reference; OpenCode was used
only as a local development executor.
