# Acceptance journeys

The initial implementation ledger is `acceptance-ledger.json`. It deliberately
marks the inventory incomplete: its denominator is the listed journeys, not
every action in Bifrost. The platform backlog is in
`docs/design-modernization/ui-acceptance-plan.md` at the repository root.

Each bound journey identifies the exact spec, test title, project, viewport
variant and proof type. Browser tests must drive the action and verify its
specific outcome. Seed with real APIs and check setup responses; verify saved
state after reload. Keep intercepted UI tests distinct from real platform
persistence and provider-boundary simulations.

Run selected specs through the normal isolated test stack from the repository
root. Coordinate one stack command at a time:

The browser lane always builds the production client from this worktree, even
when `BIFROST_SKIP_BUILD=1` is used for backend images. The client image tag is
shared across worktrees; trusting an existing tag can otherwise test a different
UI while the report records the current source revision. Docker build cache
keeps unchanged-client builds cheap.

```sh
./test.sh client e2e --screenshots e2e/executions.admin.spec.ts --workers=1
node client/e2e/support/acceptance-report.mjs client/playwright-results/results.json
```

With no results argument, the report validates ledger references and shows
unrun/missing work. A supplied missing results file is an error. Report checks:

```sh
node --test client/e2e/support/acceptance-report.test.mjs
```

The result join uses exact file, title and project. A desktop pass cannot cover
an unrun mobile binding. Skipped, interrupted, expected-failure and retried
cases are not passing acceptance evidence. Run-level errors remain visible.
The standard runner records revision and dirty state in Playwright metadata;
dirty or unrecorded runs are iteration evidence, not clean-candidate sign-off.
The normal runner automatically writes `client/playwright-results/acceptance.md`
after each run. It requires the configured JSON reporter and rejects stale
results. Results describe that run only, with no merging of results from other
commits. HTML is in `client/playwright-results/html`; screenshots, videos and
retained failure traces are in `client/playwright-results/artifacts`. Both are
mounted out of the disposable runner container.

The editor journey uses the real Monaco editor and browser keyboard input.
Monaco and its language workers are compiled into the production client and
served by nginx. Editor tests exercise those same assets without a CDN cache
or request rewriting. Bifrost API requests are not intercepted.

When adding a journey, use a stable ID in its test title, add the exact bindings
to the ledger, then validate the report. Leave an unimplemented required
journey with `tests: []`; this means its required proof is not bound, not that
no related tests exist anywhere. Record proof types explicitly. Do not declare
the inventory complete until every route, settings tab and user action has
been reconciled against the action inventory.

The browser runner executes these fast harness checks before Playwright, so
the existing browser CI gates also validate reporting.

The required `@smoke` gate includes FORM-01 desktop, EXEC-01, EXEC-02,
APP-01 desktop, V1-01 and AGENT-01. Other new cases automatically join their
audience's nightly project. Only measured, reviewed critical journeys should
be added to `@smoke`; desktop tags do not silently include the mobile variants.
