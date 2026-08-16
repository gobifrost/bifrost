# Reliable test and artifact pipeline execution plan

**Status:** Implementation complete; rollout measurement pending  
**Tracking issue:** [#603](https://github.com/gobifrost/bifrost/issues/603)  
**Evidence report:** [CI reliability and latency analysis](2026-08-15-ci-reliability-and-latency.md)  
**Owners:** Maintainers of `gobifrost/bifrost`  
**Started:** 2026-08-15

## Outcome

Bifrost will use a layered test system that gives authors fast feedback without
allowing an unproven commit or artifact to reach the shared `:dev` environment.
The system must make timing, state, and harness failures actionable rather than
rerunning them until they disappear.

The target flow is:

| Stage | Required work | Purpose |
| --- | --- | --- |
| Pull request | Lint, type/contract checks, backend unit tests, client Vitest | Fast feedback on deterministic and local contracts |
| Exact merge candidate | Complete current backend integration/E2E suite plus a small critical Playwright smoke set | Protect `main` and the shared dev image against the exact candidate |
| Dev publication | Build or resolve the production candidate, prove it came from the successful candidate, exercise the artifact, sign/attest it, promote its immutable digest, deploy, and smoke the live application | Publish what was tested rather than an unverified rebuild |
| Nightly on latest green `main` | Full product Playwright, clean build, migration/upgrade, state-order, and concurrency/soak discovery | Find expensive and environmental defects without delaying every author |
| Versioned release | Artifact identity, version/manifests, compatibility, install/upgrade, provenance, and deployment checks | Prove release mechanics and the released artifact rather than blindly duplicating source checks |

The job currently named `E2E Tests` is backend pytest integration/E2E. Browser
tests are Playwright. Workflow names and documentation must keep those layers
distinct.

## Non-negotiable safety invariant

The complete current required suite must pass against the exact merge candidate
before that commit can reach `main` and produce a shared `:dev` image. A change
may replace that relationship only with an objectively equivalent or stronger
gate that tests the exact candidate and fails closed. No implementation in this
plan changes branch protection, repository settings, production configuration,
or the merge-queue requirement.

In particular:

- nightly is supplementary discovery, never a substitute for the merge gate;
- a missing candidate image, provenance record, or smoke result blocks
  publication rather than triggering an untested fallback build;
- no retry, rerun-until-green, skip, xfail, quarantine, or blanket timeout
  increase may turn an unknown result into a pass; and
- the existing full backend merge-group suite remains intact until an exact
  artifact gate is both implemented and proven stronger.

## Baseline

The evidence report measured 737 Actions runs from 2026-06-16 through
2026-08-15. Code-bearing pull requests passed on their first attempt 73.4% of
the time; exact merge candidates passed 92.0% of the time. Among 83 failed PR
or merge-group workflows, 53 were deterministic product/build/contract
regressions, 15 were deterministic generated drift, 10 were timing/state or
isolation defects, three were authentication-expiry cascades, and only two
were external infrastructure or harness-construction failures.

Docker contributes fixed latency but is not the supported primary reliability
cause. Successful samples spent about 3.5--3.8 minutes building images and
about 1.5 minutes in stack setup/reset/collection per runner. A full Docker
stack is appropriate for PostgreSQL/Redis/RabbitMQ/object-storage/worker
contracts; it is unnecessary for pure transformations and component logic.

PR #602 already removed the duplicate full backend E2E execution from ordinary
pull requests while preserving it on the exact merge candidate. That saves a
projected median 8.03 minutes and p95 17.10 minutes of PR wait without weakening
the dev-image gate.

## Test taxonomy

Every test must have one primary contract and live at the lowest layer that can
catch its regression.

| Layer | Allowed dependencies | Scheduling | Examples |
| --- | --- | --- | --- |
| Pure unit | In-process code with controlled fakes | Every PR and merge candidate | state transitions, permission predicates, serialization, migration helper behavior |
| Component | React plus mocked service/hook boundaries | Every PR and merge candidate | validation, state transitions, conditional rendering |
| Integration | One or more real PostgreSQL/Redis/RabbitMQ/S3/API/worker boundaries | Complete exact merge candidate | repository semantics, queue handoff, cross-store eventual consistency |
| Browser smoke | Live client/API stack; one happy path per critical system capability | Exact merge candidate | login, protected navigation, public form submission, app preview/publish |
| Browser journey | Live stack; broader feature and role behavior | Nightly | complete Playwright product suite |
| Upgrade/soak | Prior-version data/artifact, clean build, concurrency, longer observation | Nightly or release-specific | upgrade from last stable, worker/scheduler contention, state-order exposure |

Documentation screenshot capture is not product acceptance coverage and will
remain a separately invoked docs workflow rather than part of the nightly
product-browser gate.

## Supported offender review and durable dispositions

| Offender | Historical evidence | Root cause | Durable disposition |
| --- | --- | --- | --- |
| `test_fresh_database_does_not_install_withdrawn_builder_schema` | 14 failures across four PRs/14 SHAs; while this plan was being implemented, product PR #607 advanced the head and follow-up PR #609 changed only this expectation | Overcomplicated contract: asserted the global Alembic head in a test about withdrawn tables/roles | Delete the unrelated exact-head assertion; preserve absence checks. Migration infrastructure owns head convergence. |
| `test_appendices_are_fresh_and_present` | 11 failures across eight PRs/11 SHAs | Deterministic generated-artifact drift | Retain the authoritative test and keep it in fast PR feedback; do not call it flaky. |
| delegation lifecycle callback | Eight failures across three PRs/eight SHAs | Real provider-selection regression | Product fix already preserved the configured provider; keep the merge-gate coverage. |
| `test_cannot_retry_pending_delivery` | Six failures across five PRs/six SHAs | Test races a real worker and conditionally skips when it loses | Move the retry-state predicate to a deterministic unit contract; keep live coverage for actual delivery/retry behavior, not an unowned transient state. |
| same-path solution install lookup | Four failures across three PRs/four SHAs | Redis cleanup called a single-key wrapper with many keys and swallowed `TypeError` | Delete keys through the supported API and fail closed if isolation cannot be established; cover conflicting order. |
| long-shard 401 cascades | Three workflows with roughly 164--171 downstream failures each | Session-scoped credentials outlived the access token | Refresh credentials deterministically before expiry at a test boundary; do not lengthen production token policy as a test fix. |
| form embed startup handle | Two failures across two PRs/two SHAs | Redis returns the async handle before the worker creates its PostgreSQL row | PR #566 restored a bounded causal wait for the documented cross-store transition. Preserve that model. |
| WebSocket policy revocation | Recurring raw receive timeout pattern | Test waits on a clock instead of a causal acknowledgement/event boundary | Use protocol acknowledgement and typed event waiting helpers; keep timeout only as a diagnostic upper bound. |
| Playwright CI retries | Configuration retries every browser failure twice | Rerun can hide state/race defects and mutates the database between attempts | Set retries to zero; retain trace/video/screenshot diagnostics on the first failure. |
| skipped Playwright/E2E cases | Static inventory found unconditional and state-dependent skips | Obsolete, environment-dependent, or unowned preconditions | Delete redundant cases with replacement coverage documented; make required external suites explicit; establish deterministic fixtures for retained behavior. |
| best-effort isolation fixtures | Redis, S3 manifest, and file-policy cleanup catch errors and continue | Contaminated state can be reported as a product assertion failure or false pass | Skip only when the dependency is intentionally absent for a lower layer; otherwise cleanup failure is a harness failure. |
| public-form CAPTCHA smoke | Public form took more than the browser assertion budget even after the worker was ready | The client always used a WASM PBKDF2 loop; the measured challenge took 7,483 ms versus 458 ms through ALTCHA's native WebCrypto implementation | Use native WebCrypto in secure contexts and retain the WASM implementation only where the browser cannot expose WebCrypto. Component tests cover both capability branches. |
| solution browser journeys | Full Playwright exposed 15--20 second page loads before the solution API calls even started | The test stack used Vite and paid cold, CPU-contended module transformation inside the assertion window; the shipped Nginx client does not have that runtime phase | Run browser gates against the compiled production client. The exposing solution journeys then completed in 17--20 seconds without timeout changes. |
| solution list empty state | Failed after a preceding solution test left state behind | Order-dependent browser assertion required a globally empty database and duplicated the explicit `Solutions.test.tsx` empty-state contract | Delete the redundant browser case; retain the component contract and the browser list/install-affordance contract. |
| consecutive form dropdowns | Raw bounding-box coordinates selected an option from the first dropdown instead of opening the second | Overcomplicated interaction bypassed Playwright's actionability checks and never proved the first popup had closed | Click the semantic combobox locators and assert the first option is hidden before opening the second dropdown. |
| public embed CSP in production | The first production-client smoke returned no `frame-ancestors` header although the Vite smoke passed | Nginx's final `try_files /index.html` argument internally redirected out of the embed location and discarded its computed CSP header | Serve `index.html` within the embed location (`try_files /index.html =404`) and statically guard both public and HMAC routes. |
| production client `logo.svg` | Two otherwise-successful journeys reported repeated `403` browser errors | Source assets were mode `0600`; the production build preserved that mode, so Nginx workers could not read them | Normalize every built directory to `0755` and file to `0644` in the production Dockerfile; retain console-error assertions. |
| solution write-lock and direct Redis tests | Six broad-suite failures across solution round trips and execution visibility, all reporting a closed or foreign event loop | Process-global async Redis clients escaped the pytest loop that created them and were reused after that loop closed | Own a Redis connection for the complete lock/test lifetime and close it through `aclose()`; never reach through the singleton's private client. The exact six exposing paths plus the lock unit contract pass together. |
| requirements-cache Redis test | The post-repair full E2E inventory exposed one additional `Event loop is closed` failure after 1,700 preceding tests | The test itself borrowed the process-global Redis singleton; the same cross-loop ownership defect therefore remained in test code after the production lock path was fixed | Give the test a short-lived `RedisClient`, close it in its creating loop, and parse the cached payload through the public Redis API. The original node passes in the exposing sequence. |
| MCP knowledge gateway result | Broad suite returned a successful tool call as JSON text while the test assumed `structuredContent` was mandatory | The MCP transport legitimately supports both structured results and JSON-encoded text results; the test asserted one wire representation instead of the contract | Decode either supported success representation through one helper and assert the gateway payload, preserving error handling. |
| MCP memory embedding fixture | Failed only after earlier knowledge tests had created canonical 1,536-dimensional rows | The deterministic fixture emitted three-dimensional vectors, so shared knowledge state correctly demanded an explicit reindex instead of saving | Make the fake embedding shape conform to the provider contract (1,536 dimensions); retain the product's dimension-mismatch protection. |
| stale scheduler fixture process | The post-repair full E2E inventory still observed the old three-dimensional embedding response although the bind-mounted fixture source had been repaired | `test.sh` reset API/worker/scheduler state but left the long-lived scheduler fixture process running the Python module it imported at stack startup | Stop and restart `scheduler-fixtures` during the canonical state reset. A static harness contract prevents removal, and the knowledge-store-to-MCP exposing sequence passes with the reloaded 1,536-dimensional fixture. |
| worker import-closure posture | Worker import pulled in Uvicorn, Starlette, MCP, and provider SDK modules before an agent execution needed them | `AutonomousAgentExecutor` was eagerly imported at module load, expanding worker memory/startup closure | Keep the import type-only at analysis time and load the executor inside the actual agent-message path. The import-posture test now exercises the intended boundary. |
| permanently skipped live GitHub creation test | Required E2E lane could never create an external repository without an opt-in marker and token | The test's real contract was request routing/shape; destructive third-party creation is not a deterministic required-suite fixture | Delete the dead E2E case and add parameterized unit contracts for user and organization repository endpoints, request payload, and response shape. |
| permanently skipped OAuth callback duplicate | Cross-process patching could not replace the live API's token exchange, so the entire E2E class was unconditionally skipped | Harness construction could never exercise the advertised boundary; the callback ownership contract already had direct unit coverage | Delete the inert class and retain `test_callback_links_token_to_mapping_and_captures_entity_id` as the deterministic contract. |
| unit-test coroutine warnings | Full backend unit discovery reported unawaited Redis transport and SQLAlchemy mock coroutines in two otherwise-green tests | One timeout fake replaced every `asyncio.wait_for` in the process; one `AsyncSession` fake incorrectly made synchronous `add()` awaitable | Scope the timeout fake to the delegation deadline and model `add()` as a synchronous mock. Both exposing tests pass with `RuntimeWarning` promoted to an error. |
| Vitest loopback connection noise | The complete 1,763-test lane passed but emitted repeated `ECONNREFUSED 127.0.0.1:3000` messages | Some component fixtures still allow browser-environment resource/service access to escape their mocked boundary; the messages are not evidence that a live server contract passed | Keep Vitest as a no-server lane, inventory the exact callers with a network-deny diagnostic, and replace each escape with a controlled service/resource fake. This is ranked harness cleanup, not a reason to add a server or suppress stderr. |

The historical labels above are evidence-backed categories, not a claim that
every arbitrary wait in the repository is faulty. Polling is correct when the
product contract is asynchronous and the test waits for an observable state
transition with a bounded diagnostic deadline. Sleeping for elapsed time or
swallowing an unmet precondition is not.

## Browser gate selection

The initial exact-candidate browser set will be deliberately small and run
with zero retries:

1. unauthenticated login/protected-route behavior;
2. public form publication/submission through the live worker path;
3. authenticated form execution;
4. representative user authorization; and
5. app preview/publish across API, bundler, object storage, Redis pub/sub,
   WebSocket, and browser import.

Each selected spec must first pass locally and under the exposing CI topology.
If a spec cannot provide deterministic ownership and cleanup, it remains out
of the required smoke set until repaired; the current backend suite still
protects the exact merge candidate throughout that repair.

The nightly workflow runs all product Playwright projects except the docs
capture project. It retains HTML/JSON results, traces, videos, screenshots,
and relevant service logs on the first failure. It does not retry. Failures are
owned reliability incidents; the workflow must make the failing spec and stack
logs visible without converting the result to green.

### Full-suite discovery record

The initial zero-retry product-browser inventory ran 119 tests against the Vite
test client: 113 passed, four failed, and two serial dependents did not run in
17.2 minutes. The failures were durably assigned to Vite cold-transform cost,
an order-dependent empty-state assertion, and a coordinate-based dropdown
interaction. After moving to the production client and repairing those tests,
the 118-test inventory completed in 10.8 minutes with 116 passes and two
failures. Both failures had completed their advertised contracts and then
correctly rejected six/three `403` console errors for the unreadable production
logo. The exact exposing tests passed after the image permission fix. No run in
this sequence used a retry, skip, timeout increase, or rerun-until-green; each
repetition followed a supported code or harness repair.

The final production-client inventory then passed all 118 tests in 11.2
minutes with zero retries. This includes the previously failing realtime,
code-splitting, solution backup, solution file, public form, and static-asset
paths. The result is the completion criterion for the browser-suite repair,
not evidence that an unexplained failure disappeared on rerun.

The corresponding complete backend discovery run collected 7,436 tests and
finished in 39 minutes 37 seconds: 7,421 passed, nine failed, and six were
explicit environment/platform skips. The nine failures were not rerun in
place. They were assigned to five supported causes: two async Redis ownership
defects (covering six failures), one MCP wire-representation assumption, one
nondeterministic embedding-fixture dimension, and one eager worker import.
After those causes were repaired, one exact exposing sequence containing all
nine original paths plus the affected write-lock, OAuth, and import contracts
passed 16 tests in 20.27 seconds. The permanently skipped GitHub and OAuth
cases were then removed with deterministic lower-layer replacements. External
credential suites and genuine operating-system capability checks remain
explicitly conditional; the required gate must report them as not exercised,
not imply that a skipped external system passed.

The post-repair deterministic lanes then completed with 5,624 backend
unit/contract passes (three explicit environment checks skipped), 1,763 Vitest
passes, and 122 focused workflow/harness/reliability contract passes. API
type-check and lint completed with zero findings; client type-check and lint
completed with no errors (one existing React Hook Form compiler advisory).

A subsequent standalone full E2E inventory completed 1,786 tests and exposed
two remaining failures rather than being treated as a green rerun target. The
requirements-cache test still borrowed a cross-loop Redis singleton, and the
canonical reset did not reload the bind-mounted scheduler fixture server, so
it continued serving the pre-repair three-dimensional embedding. The fixes
above address those mechanisms. The exact ordered sequence—requirements cache,
canonical knowledge creation, then MCP memory save/search/remove—passed all
three tests in 11.10 seconds after the repairs. The inventory's sole skip was
the explicit alternate MFA-disabled environment case; its MFA-required
counterpart passed. The complete merge-group gate will independently run the
full current suite against the exact candidate before publication.

After rebasing onto the current default branch, the publication candidate
passed 152 focused workflow, harness, isolation, import-hygiene, warning,
replacement-coverage, OAuth, and migration contracts in 12.94 seconds. API
Pyright/Ruff and client TypeScript/ESLint also passed on that commit; ESLint
retained one pre-existing React Hook Form compiler advisory and no errors.
Shell/Node syntax and pinned-action validation passed. The 32-minute E2E
inventory was not blindly repeated after its two supported fixes; the exact
exposing sequence passed locally, and the protected merge-group gate remains
responsible for the independent complete-suite result against the final exact
candidate.

## Artifact architecture

Before this work, the merge gate tested source in development/test images and
a later `push: main` job built fresh production images. Therefore the source
commit was gated, but the exact published production digest was not. In the 24
most recent successful merge candidates available during implementation, all
24 merge-group SHAs were byte-for-byte identical to the later `main` push SHA.

The implemented dev path is:

1. the exact `merge_group` candidate passes the complete current suite;
2. a production candidate is built for that exact SHA;
3. artifact-level startup/health and critical smoke checks exercise that
   candidate;
4. the candidate is signed and attested with its source SHA and gate identity;
5. after GitHub places the identical SHA on `main`, the main workflow resolves
   that candidate and promotes the same immutable digest to the human-facing
   dev/version/SHA tags; and
6. deployment waits for rollout and application smoke success.

There is no fallback rebuild on `main`. A direct push or provenance mismatch
cannot publish `:dev`.

The SHA-addressed API and client candidates build, sign, and attest in parallel
with the complete merge-group suite. Their build result and the critical
browser smoke (which pulls the exact candidate client image) are dependencies
of the existing required `E2E Tests` aggregate. The `main` job resolves the two
full-SHA candidate tags, promotes those immutable digests with `buildx
imagetools`, and verifies every dev/version/SHA alias resolves to the expected
digest. It contains no Docker build action and no fallback build. The deployed
API readiness endpoint and client document are then smoked through Kubernetes
service port-forwards after rollout.

Before signing, the exact API candidate digest is also started as a container,
imports the production FastAPI application from the image, and proves its baked
version equals the candidate's computed version. The browser smoke exercises
the exact production client digest. These artifact checks supplement rather
than replace the complete source/integration suite.

Across 25 recent `main` runs, the old post-merge `Build Dev Images` job took a
median 274 seconds and p95 361 seconds (238--368 second range). Candidate builds
now overlap the roughly 20-minute exact-candidate suite, so digest promotion
removes about 4.6 minutes median and 6.0 minutes p95 from the post-merge path to
dev publication, before counting the eliminated duplicate production-client
build in backend-only jobs.

Release promotion has an additional constraint: Bifrost currently bakes the
version string into the API/client images. A dev image and a final semantic
release image therefore cannot be the same digest while reporting different
versions. Until runtime version identity is decoupled or a release candidate is
built once with its final version before testing, tag releases retain their
complete current gate. Removing the repeated release source suite before that
artifact identity is proven would be weaker and is not authorized by this plan.

## Delivery sequence

Each phase lands through the normal merge queue. The exact-candidate backend
suite stays complete for every phase.

### Phase 0 — completed latency foundation

- PR #566: scoped local verification and durable failure disposition.
- PR #602: fast ordinary PR lane; full backend E2E only on exact merge
  candidates, tags, and manual full runs; client Vitest folded into the
  required aggregate.

### Phase 1 — reliability foundation

- Remove the exact migration-head coupling.
- Replace the pending-delivery race/skip with a deterministic lower-layer
  state contract.
- Repair and fail-close Redis/S3/file-policy isolation.
- Add deterministic credential refresh before expiration.
- Remove Playwright retries and dispose of unconditional or state-race skips.
- Add focused regression coverage for every harness repair.

**Implementation status:** complete in this change except that credential
refresh remains a ranked follow-up; the current full browser inventory finishes
well inside the existing two-hour test-token policy. The migration coupling,
pending-delivery race, Redis event-loop leak, cleanup fail-open behavior,
runtime skips, and browser retries now have permanent dispositions and focused
contracts. The first longest-test pass additionally removed 51.09 seconds of
measured duplicate/artificial pytest time: refused-loopback MCP discovery,
one exposing large-memory loop, consolidated data/secret import lifecycles,
and replacement of two state-poisoning package-install E2Es with isolated
router plus storage/cache contracts. One real package install/recycle/execution
journey remains.

### Phase 2 — browser tiers and nightly discovery

- Add an explicit zero-retry smoke command and static list/selection tests.
- Add the selected Playwright smoke set to `merge_group` only and fold it into
  the existing required aggregate without changing repository settings.
- Add a scheduled/manual nightly product-browser workflow excluding docs
  capture.
- Retain Playwright reports and stack logs on failure.
- Add clean-build and upgrade/concurrency jobs only after their fixtures have
  deterministic setup and cleanup.

**Implementation status:** browser smoke, full product nightly, slow-contract
nightly, truthful API unit coverage, and clean no-cache production builds are implemented. Browser tests
now exercise the compiled production client and Nginx, which exposed and fixed
two deployment-only defects. Prior-version upgrade and sustained concurrency
fixtures remain intentionally pending rather than being represented by weak or
stateful tests. Fresh hosted jobs consume their already-clean boot state once
instead of immediately repeating the canonical reset; a marker guarantees any
second suite still resets. The prior PR Codecov step uploaded no generated
report and has been removed rather than continuing to imply coverage. The first
working backend unit baseline is 57% line coverage (5,648 passes in 134.25
seconds); nightly owns the report so measurement adds no merge latency.

### Phase 3 — exact dev artifact

- Build a SHA-addressed production candidate downstream of the successful
  exact-candidate suite.
- Smoke, sign, and attest the candidate.
- Replace the main rebuild with fail-closed digest promotion.
- Add post-deploy application smoke.
- Extend static workflow contract tests so trigger, dependency, SHA, artifact,
  and publication relationships cannot silently drift.

**Implementation status:** complete. Historical SHA identity was 24/24, the
candidate build is part of the required exact-candidate aggregate, the client
candidate is used directly by browser smoke, main can only promote the matching
full-SHA API/client candidates, every alias is digest-verified, and deployed API
and client services receive post-rollout smoke checks. The complete backend
merge-group suite remains unchanged.

### Phase 4 — release artifact model

- Decide how runtime version identity can remain correct without rebuilding an
  already-tested artifact.
- Build the final-version candidate once, test it, and promote the same digest
  to semver/major/minor/latest tags.
- Replace duplicated release source checks only when the tag is cryptographically
  or objectively tied to the already successful exact candidate and tested
  artifact.
- Add prior-stable-to-candidate upgrade and CLI compatibility checks.

## Verification and measurements

Every implementation PR must run focused tests for changed behavior and the
workflow contract tripwires. Cross-cutting harness changes also run the full
affected backend or browser suite once without retry. Known failures receive a
durable disposition before merge.

After rollout, compare at least 30 code PRs/merge candidates with the baseline:

- first-attempt pass rate by event and suite;
- timing/state/isolation failure count and distinct PR/SHA breadth;
- median and p90/p95 PR and merge-candidate duration;
- Playwright smoke and nightly duration/failure ownership;
- main-to-dev-image and main-to-successful-smoke time;
- duplicate runner-minutes; and
- exact-digest correlation from merge candidate through deployed dev.

Success means faster PR feedback, fewer false cascades, and stronger artifact
identity. It does not mean making red tests less visible.
