# Bifrost Test Authoring Rules — Expanded

Companion to `SKILL.md`. Read when you need to know *how* to write a test, not *when*.

## Component tests (vitest + @testing-library/react)

**Location:** sibling of the component. `client/src/components/foo/Foo.tsx` → `client/src/components/foo/Foo.test.tsx`.

**What to cover:**
- Validation feedback (errors appear/disappear correctly in response to input)
- Conditional rendering (feature flags, loading/error/empty states)
- Event handlers (click → mutation called with right arguments)
- State transitions (dialog phases, toggle state)

**What to mock:**
- Hooks and external modules at module level with `vi.mock()`.
- Do not render the whole app. Don't pull `<Router>`, `<QueryClient>`, etc., unless the component explicitly requires them.
- Network calls are mocked via the hook wrappers, not MSW.

**Patterns:**
- Selectors: `userEvent.setup()` + `screen.getByRole()` / `getByLabel()` / `within()`. No `data-testid`.
- Helpers: define `makeThing()` and `renderFoo()` helpers at the top of the test file to reduce repetition.

**Reference implementations (copy these when starting a new test):**
- `client/src/components/applications/AppReplacePathDialog.test.tsx`
- `client/src/components/workflows/WorkflowSidebar.test.tsx`

**Exempt from requiring a sibling test:**
- Pure presentational wrappers: `<Card>`, `<PageHeader>`, static icon components.
- Re-exports.
- Trivial styled-div components with no branching behavior.

If in doubt whether a component is "trivial" — err on writing the test. If the component has a `useState`, a `useEffect`, or any conditional JSX, it has behavior worth asserting.

## Feature happy-path tests (Playwright)

**Location:** `client/e2e/<feature>.<audience>.spec.ts`, where audience is `admin`, `user`, or `unauth`.

**What to cover:**
- The primary user journey end-to-end: navigate, interact, verify the outcome appeared.
- Exactly one path per feature. Don't branch in a single spec.

**What NOT to cover here:**
- Validation error paths, permission-denied paths, "what if the form is empty" — those go in vitest.
- Every field permutation. Pick the representative happy path and stop.

**Selector conventions:**
- `page.getByRole()`, `page.getByLabel()`, `page.getByPlaceholder()`.
- No `data-testid`. (The existing suite does not use it; keep that consistent.)

**Wait strategy:**
- Condition-based: `waitForURL`, `getByRole(...).waitFor()`, `Promise.race([...])` when the outcome could be one of several states.
- **Never** `page.waitForTimeout()`. That's the signature of a flaky test-in-waiting.

## Backend unit tests

**Location:** `api/tests/unit/`.

**What to cover:** pure logic — anything that can run without a database, queue, or HTTP server.

**Patterns:** mock dependencies with `unittest.mock` or pytest fixtures. Don't use `./test.sh stack up` — unit tests run inside `test-runner` against an ephemeral config.

## Backend e2e tests

**Location:** `api/tests/e2e/`.

**What to cover:** anything that hits the real API, real DB, real queue, or real S3. Round-trip behavior.

**Isolation:** each `./test.sh` invocation starts from a template DB clone. HTTP
writes persist between test functions in that invocation; a `db_session`
fixture rolls back only its own uncommitted work. Use unique names and explicit
cleanup where tests share state. The autouse S3 and Redis fixtures clean selected
paths/keys per test (see `api/tests/conftest.py`), not the whole workspace.

**Performance:** use the shared fixtures in `tests/e2e/fixtures/`. Count queued jobs and full builds as test setup, even when they hide inside a fixture.

### Expensive backend E2E tests

A full deploy, install, publish, build, sync, or worker execution pays for more
than an HTTP request: dispatch, another process, storage writes, and a terminal
state wait. Add one only when the cross-process boundary itself is the contract
under test. Keep one representative successful journey through that boundary;
exercise validation, permission, mapping, and persistence edge cases through
the endpoint or direct service/database layer that can detect the regression.
For a refusal that occurs before enqueueing, assert the HTTP refusal without
running a successful job first unless the prior job creates the state being
refused. A second job is justified when the transition between jobs is the
behavior, such as upgrade or rollback.

Before adding an expensive case:

1. Search for an existing test that already crosses the same boundary. Name
   the new behavior it does not cover. Extend that journey when its state can
   be reused safely; keep independent tests when their mutations would make
   order or cleanup ambiguous.
2. Count the real jobs/builds in the test and its fixtures. For each, state
   which observable contract requires it. Move setup-only entity creation to
   a direct fixture when the API creation path is covered elsewhere.
3. Run the relevant cases together with `./test.sh ... -v --durations=0`.
   Inspect setup, call, and teardown separately. For a case taking at least
   2 seconds, identify whether ordinary requests, a queued job, or fixture
   setup dominates. Record the old and new summed case time when replacing
   coverage; distinguish that sum from suite and shard wall time. To locate
   hotspots in a full JUnit result, run
   `python3 api/scripts/report_test_costs.py /tmp/bifrost-<project>/test-results.xml`
   on the host; `./test.sh stack status` prints the project name.
4. In the PR description, name the retained full-path test and the lower-level
   tests covering edge cases. Explain any extra full job whose value is not
   evident from the assertion. Do not add a fixed test-to-source map or a
   blanket duration gate: timing and ownership change, but the unique boundary
   and measured cost can be reviewed.

Batch related focused tests in one `./test.sh` invocation. Each separate local
invocation resets the stack; that reset is command overhead, not a test's JUnit
case time. Preserve test isolation and explicit cleanup when sharing fixtures.

## Running tests

All via `./test.sh`. Never `pytest` or `npx vitest` / `npx playwright` directly — the script manages the Dockerized test stack and state reset.

```bash
./test.sh                          # Backend unit tests (fast default)
./test.sh unit                     # Same
./test.sh e2e                      # Backend e2e tests
./test.sh all                      # Unit + e2e
./test.sh tests/unit/test_foo.py::test_bar -v   # Single test

./test.sh client unit              # Vitest on host
./test.sh client e2e               # Playwright in containers
./test.sh client e2e --screenshots # Capture screenshots for UX review
./test.sh client e2e e2e/auth.unauth.spec.ts    # Single spec
```

## Debugging failing tests

- Logs: `/tmp/bifrost-<project>/*.log` per service, per worktree.
- JUnit XML on the host: `/tmp/bifrost-<project>/test-results.xml`.
- For an intermittent E2E failure, do not assume the test is harmless or that state pollution is the only cause. Prior Bifrost flakes have exposed both leaked state and real product races. Capture the exposing order/load, run the test alone, then recreate the exposing condition and classify the cause from evidence.
- Do not add retries, raise timeouts, skip the test, or rerun until green. After fixing a concrete cause, repetition is useful only to validate that fix under the previously failing condition.
- If the E2E test is trying to prove many edge cases or implementation details, simplify it to the one stable integration or user contract and move the remaining assertions to unit/component tests. Delete obsolete or duplicate coverage when another named test preserves its only useful signal.
