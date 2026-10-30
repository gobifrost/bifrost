# Authentication Consolidation Verification

Candidate: `codex/auth-consolidation`, based on `a1edc19fd`.
Status: implementation and corrected consumer-harness verification passed.
The latest browser evidence uses base `8daa923ef`; the chat-oracle simplification
was verified as its explicitly recorded working delta.
This is local delivery evidence,
not deployment approval or a performance result.

All Docker/client workloads acquire `/tmp/bifrost-capacity-host.lock` and retain
it until owned stacks stop. Test project: `bifrost-test-f784321a`. No benchmark
children or other chats' stacks were stopped.

## Focused Backend Coverage

The first expanded selection passed 893 of 898 tests. The five failures were
fixed and all affected files rerun in the 174-test correction selection below.
JUnit: `/tmp/bifrost-auth-first-focused-failures.xml` and
`/tmp/bifrost-auth-focused-unit-r2.xml`. Causes and durable dispositions are in
[the independent review record](2026-10-30-authentication-review.md).

```bash
./test.sh tests/unit/authorization tests/unit/shared/test_human_sessions.py tests/unit/shared/test_auth_cookies.py tests/unit/core/test_auth_platform.py tests/unit/services/test_mcp_auth.py tests/unit/services/mcp_server/test_auth_provider_claims.py tests/unit/services/mcp_server/test_auth_code_binding.py tests/unit/services/mcp_server/test_bridge_refresh.py tests/unit/services/mcp_server/test_authorization.py tests/unit/routers/test_oauth_sso.py tests/unit/routers/test_auth_run_refresh.py tests/unit/test_mcp_oauth_external_claim.py tests/unit/test_integrations_external.py tests/unit/test_user_mfa_reset.py tests/unit/test_e2e_fixture_refresh.py tests/unit/test_solution_dev_proxy.py tests/unit/test_access_list.py -q --durations=10
./test.sh tests/unit/authorization/test_validator.py tests/unit/test_e2e_fixture_refresh.py tests/unit/test_access_list.py tests/unit/services/test_browser_impersonation.py tests/unit/routers/test_browser_impersonation_credentials.py tests/unit/core/test_request_context_middleware_access_checks.py tests/unit/test_dto_flags.py tests/unit/test_contract_version.py tests/unit/test_device_auth_decision.py -q --durations=10
```

The late consumer selection passed all 60 tests, including WebSocket signed-role
consumption, CLI migration guidance and SDK packaging/contract provenance.
JUnit: `/tmp/bifrost-auth-final-consumer-unit.xml`.

```bash
./test.sh tests/unit/test_cli_app_migrate.py tests/unit/test_migrate_v2.py tests/unit/routers/test_websocket_human_claims.py tests/unit/routers/test_websocket_table_messages.py tests/unit/test_websocket_table_resolution.py tests/unit/test_sdk_package_fingerprint.py tests/unit/test_sdk_package.py tests/unit/test_application_sdk_status.py tests/unit/test_solution_sdk_aggregates.py -q
```

The final live selection passed all 72 tests. It covers PostgreSQL single-use
rotation, predecessor logout, concurrent revoke/rotate, cancellation, password
and MFA reset, normal login/profile/SSO, human WebSocket invalidation on input and
outgoing delivery, support-session boundaries, device approval and retained
runtime refresh. JUnit: `/tmp/bifrost-auth-focused-e2e.xml`.

```bash
./test.sh tests/e2e/platform/test_human_sessions.py tests/e2e/api/test_auth.py tests/e2e/api/test_profile.py tests/e2e/api/test_oauth_sso.py tests/e2e/api/test_websocket.py tests/e2e/api/test_browser_impersonation.py tests/e2e/test_device_auth.py tests/e2e/platform/test_runtime_token_claims.py -v --durations=10
./test.sh quality api
```

API quality passed with zero pyright errors/warnings and clean ruff. The late
worker/SDK selection passed 100 of 101 tests, including all SDK-status cases.
The only failure was the workflow-list expectation documented in the review
record. Its corrected class then passed all six tests. Both remaining live
consumer cases passed (workspace requester binding and external attribution).
JUnit: `/tmp/bifrost-auth-review-correction-r5-failure.xml`,
`/tmp/bifrost-auth-worker-list-correction.xml` and
`/tmp/bifrost-auth-final-extra-e2e.xml`.

```bash
./test.sh tests/unit/services/test_worker_sdk_http.py tests/unit/test_application_sdk_status.py tests/unit/test_solution_sdk_aggregates.py -q --durations=10
./test.sh tests/unit/services/test_worker_sdk_http.py::TestSocketWorkflowsAndExecutions -q --durations=10
./test.sh tests/e2e/platform/test_workspace_bundle_import.py::test_workspace_bundle_preview_enforces_auth_requester_expiry_and_active_dedupe tests/e2e/api/test_access_check_attribution.py::test_external_provider_claim_and_agent_access_checks -v --durations=10
./test.sh quality api
```

The slowest changed live-consumer case spent 6.48 seconds in fixture setup and
0.98 seconds in its call. External attribution spent 3.28 seconds in ordinary
HTTP/authentication requests and observation of the existing audit flush. These
fixture migrations add no deploy/install/build/sync or worker job. In the
72-test selection, the slowest call was the existing password change (1.61
seconds); the larger times were shared fixture setup.

## Focused Browser and SDK Coverage

The final browser source candidate `9016e1767` passed all 142 tests across the
following 11 files, including normal logout after an epoch challenge. Client tsc
passed. Lint reported zero errors and the existing console warning in
`client/e2e/support/seed-review-pack.ts:197`.

```bash
./test.sh client unit src/lib/api-client.test.ts src/lib/api-client.auth-redirect.test.ts src/lib/app-sdk/transport.test.ts src/lib/app-sdk/tables.test.ts src/lib/app-sdk/provider.test.tsx src/lib/app-sdk/ws-client.test.ts src/lib/app-sdk/sdk-contract.test.ts src/services/websocket.test.ts src/contexts/AuthContext.test.tsx src/pages/MCPCallback.test.tsx src/lib/auth-token.test.ts
(cd client && npm run tsc && npm run lint)
./test.sh client e2e e2e/auth.unauth.spec.ts --project=unauthenticated --grep 'should complete full login flow with MFA'
```

The initial production-client MFA run reached `/auth/me` HTTP 200 but used the
APIResponse-only `toBeOK` matcher on a browser Response. The assertion now checks
`status() === 200`. The final run passed: setup 17.4 seconds, the MFA happy path
2.2 seconds, two tests passed in 20.7 seconds. API quality passed again with zero
pyright errors/warnings and clean ruff. Final log:
`/tmp/bifrost-auth-last-checks.log`; isolated stack teardown completed before
releasing the host lock.

## Limits

Full backend, full Vitest, full Playwright, pre-pr and CodeQL suites were
not run locally. PR CI subsequently ran the full unit selections and exposed
the two stale consumers described below. Focused tests cover changed behavior and identified consumers; the
protected merge queue remains the complete-suite gate. This work makes no
capacity, CPU or memory improvement claim. The sibling benchmark owns those
measurements. Runtime legacy-bridge removal remains deferred to PR1c in a later
release; this change does not establish its rollout prerequisite.


## Full CI Findings and Focused Corrections

PR preflight on `290a3f367` passed 10,975 backend tests and 4,014 client tests but
failed one consumer in each suite: the Solution CLI guide's removed useUser
warning and a useTable fixture's generic-401 renewal assumption. The E2E
aggregate failed because preflight blocked the shards. CodeQL added five
intent/annotation comments, addressed without changing authentication behavior.
The independent review record retains every cause and correction.

The corrected source `6ea657ae2` passed 77 hook/transport tests, 122 selected
backend tests, seven PostgreSQL session cases and API/client type/lint checks.
JUnit: `/tmp/bifrost-auth-ci-correction-unit.xml` and
`/tmp/bifrost-auth-ci-correction-e2e.xml`; log:
`/tmp/bifrost-auth-ci-corrections.log`. The scoped commands were:

```bash
./test.sh client unit src/lib/app-sdk/use-table.test.tsx src/lib/app-sdk/tables.test.ts src/lib/app-sdk/transport.test.ts
(cd client && npm run tsc && ./node_modules/.bin/eslint src/lib/app-sdk/use-table.test.tsx)
./test.sh tests/unit/test_cli_solution_migrate_app.py tests/unit/test_cli_app_migrate.py tests/unit/test_migrate_v2.py tests/unit/authorization/test_human_tokens.py tests/unit/authorization/test_one_minter.py tests/unit/shared/test_human_sessions.py tests/unit/shared/test_auth_cookies.py tests/unit/test_access_list.py -q --durations=10
./test.sh tests/e2e/platform/test_human_sessions.py -v --durations=0
./test.sh quality api
```

The access catalog generator ran under the host lock and produced no change.
The live cases completed in 1.02 seconds; rotation/revocation and cancellation
assertions remained intact. The stack was removed before releasing the lock.
Full corrected CI remains required before requeueing; no unchanged CI retry was
requested.

## Protected Merge-Queue Result

PR candidate `1a198c8eb` passed full CI: 10,976 backend units, 4,015 client tests,
Product Updates, lint/types and both CodeQL checks. The subsequent exact merge
candidate `ce0329121c8e4d5dd47fe2be462cfcc223c821c8`, run `41097807451`, failed
live consumer tests and browser smoke (14 passed, 3 failed). Backend shard 1
passed 752 with 21 failures; shard 2 passed 740 with 4 failures; shard 3 exposed
static MCP/CLI credentials and three obsolete SDK-contract assertions. Failure
summaries are retained at `/tmp/bifrost-auth-queue-shard-<job-id>-summary.log`
for jobs `124208005800`, `124208005667` and `124208005569`. Browser trace and
artifacts are retained at `/tmp/bifrost-auth-browser-smoke-ci/`.

The independent review record documents the causal fixes being implemented.
Fresh focused verification and final review are required before requeueing.

## Live Consumer Harness Verification

Candidate `24e771634` passed 37 helper/agent-settings tests, client tsc/lint,
two fixture protocol units, all 99 selected live consumers and API quality
(pyright 0 errors/warnings; ruff clean). The live selection completed in 44.50 seconds.
JUnit: `/tmp/bifrost-auth-queue-fixture-unit.xml` and
`/tmp/bifrost-auth-queue-consumer-e2e.xml`; full log:
`/tmp/bifrost-auth-queue-consumer-checks-r2.log`.

```bash
./test.sh client unit src/test/human-auth.test.ts src/components/agents/AgentSettingsTab.test.tsx
(cd client && npm run tsc && npm run lint)
./test.sh tests/unit/test_e2e_fixture_refresh.py -v --durations=0
./test.sh tests/e2e/api/test_websocket_read_visibility.py tests/e2e/platform/test_subscriptions.py tests/e2e/platform/test_cli_apps.py tests/e2e/platform/test_cli_claims.py tests/e2e/platform/test_cli_roles.py tests/e2e/platform/test_cli_workflows.py tests/e2e/platform/test_cli_agents.py tests/e2e/platform/test_cli_orgs.py tests/e2e/platform/test_cli_solution_files.py tests/e2e/api-integration/test_mcp_endpoints.py tests/e2e/api-integration/test_mcp_gateway.py tests/e2e/api-integration/test_mcp_protocol.py tests/e2e/api-integration/test_mcp_refresh_after_register.py tests/e2e/platform/test_application_sdk_update.py -v --durations=0
./test.sh quality api
./test.sh client e2e e2e/agent-create-acceptance.user.spec.ts e2e/forms.user.spec.ts e2e/forms-public.unauth.spec.ts --grep @smoke
```

That browser reproduction passed setup, agent creation and the public form;
member-form setup exposed the API-native refresh-path correction documented
in the review record. Its trace/results are retained. Browser r3 is queued
for the corrected helper and the focused/smoke selections; the unchanged 99
backend cases will not be repeated. No production or performance result is
implied.


## Final Browser Consumer Verification

The corrected native API refresh path passed all six helper protocol tests,
client tsc and lint. The focused reproduction passed all four cases (setup
plus the three previously failing journeys) in 40.1 seconds. The complete
critical smoke passed 17 cases in 110.7 seconds. Both runs report source
`8daa923ef024c9764efe5747faf20b920b5d194d`, `sourceDirty: false`, and zero skips,
retries, unexpected results or run-level errors.

The additional payload-observation selection passed all six cases in 44.7
seconds. Routed OAuth/settings mocks preserve their request-body observations;
no broad mock rewrite was needed. The passive chat request-body assertion was
replaced by the existing persisted context-usage model-profile check. Exact
outbound chat payload coverage remains in `services/chatRuns.test.ts`. This run
records `sourceDirty: true` for that single chat-test delta against the base
above; it is focused working-tree evidence, not an immutable release claim.

```bash
./test.sh client unit src/test/human-auth.test.ts
(cd client && npm run tsc && npm run lint)
./test.sh client e2e e2e/agent-create-acceptance.user.spec.ts e2e/forms.user.spec.ts e2e/forms-public.unauth.spec.ts --grep @smoke
./test.sh client smoke
./test.sh client e2e e2e/ai-model-settings.admin.spec.ts e2e/per-mapping-oauth.admin.spec.ts e2e/chat-persistence.admin.spec.ts
```

Evidence: `/tmp/bifrost-auth-browser-consumer-r3.log`,
`/tmp/bifrost-auth-queue-failed-browser-results.json`,
`/tmp/bifrost-auth-queue-browser-smoke-results.json`, and
`/tmp/bifrost-auth-queue-request-body-results.json`. The owned stack was removed
before the host lock was released. No unchanged failing test was retried.

The slowest backend selection setup took 9.47 seconds to create shared user
fixtures; its WebSocket call took less than a second. The existing CLI file
restore call took 3.90 seconds and retains its unique encrypted-backup/CLI
install contract. No test adds an install, deploy, publish, build, sync or worker
job. Full backend E2E and full Playwright were not run locally; the protected
queue remains the complete-suite gate. Full client CI on `8daa923ef` passed
4,021 tests across 606 files, both CodeQL analyses, Product Updates and lint/type
checks; the actual full backend-unit job is still being monitored.

## Second Queue Consumer Correction (Verification Pending)

Final source 85ae0cc9d passed actual CI: 10,976 backend units, 4,021 client
tests across 606 files, both CodeQL analyses, lint/types and Product Updates.
Its merge candidate e54c1b6d952dbe8d5f8b0739a2a0f0482f95c104 passed both image
builds, full units/client tests, lint/types, 870 shard-3 E2E tests (21 existing
skips) and the 17-test critical browser smoke. The other shards exposed 11 raw
subscription REST callers using stale human credentials. The source merge is
still pending; the queue failure is not waived.

The correction is limited to three test modules. Current credentials are
obtained for raw table/file subscription HTTP and WebSocket consumers and for
execution-log raw clients. No production source changed. The direct-client
inventory and cause are recorded in the review document.

Verified under the host flock:

```bash
./test.sh tests/e2e/platform/test_solution_storage_full_e2e.py tests/e2e/test_execution_logs_list_endpoint.py -v --durations=0
```

Result: 10 passed in 18.66s; JUnit `/tmp/bifrost-auth-raw-logs.xml`, log
`/tmp/bifrost-auth-raw-logs-check.log`. Its owned stack was stopped.

The initial subscription sequence passed 13 tests but allowed fresh actor setup
to hide the stale admin. A second attempt exposed four stale WebSocket actors
but combined duplicate module selections, so neither result is claimed as the
required queue reproduction. Logs are retained at
`/tmp/bifrost-auth-subscription-repro.log` and
`/tmp/bifrost-auth-subscription-repro-r2.log`, with the latter JUnit at
`/tmp/bifrost-auth-subscription-before-r2.xml`. The refined sequence initializes
actors in separate existing modules before the role cleanup. Its expected
11-failure baseline, corrected selection and API quality remain pending.

## Final Causal Proof and Source Merge

The final local sequence used distinct modules to initialize all actor fixtures
before role cleanup. It reproduces the full-suite credential state, rather than
letting a late user-creation request refresh the shared admin.

```bash
./test.sh tests/e2e/api/test_websocket_read_visibility.py::TestAgentRunChannelVisibility::test_owner_can_subscribe_others_denied tests/e2e/api/test_agent_run_visibility.py::test_platform_admin_sees_both_runs tests/e2e/platform/test_solution_storage_full_e2e.py tests/e2e/platform/test_subscriptions.py tests/e2e/platform/test_file_websocket_subscriptions.py -v --durations=0
./test.sh quality api
```

Under the shared host flock, the first command against the unchanged 85ae test
modules produced the 11 original REST `Not authenticated` failures, 4 passes and
zero errors in 28.89s. With correction 6fc98159a restored, the same 15 cases passed
in 28.82s. API quality: zero Pyright errors/warnings, Ruff clean. JUnit evidence:
`/tmp/bifrost-auth-subscription-before.xml` and
`/tmp/bifrost-auth-subscription-after.xml`; full log:
`/tmp/bifrost-auth-subscription-repro-r3.log`. The script exited zero, removed its
owned stack, released the lock and restored a clean worktree. Independent final
Astra review approved queue readiness with no P1/P2 findings.

PR #1149 then merged as `05d1fbabdb36f6bf6e00fc80c0b991728fdbcbd2` at
2026-10-30T19:39:47Z. Protected queue [CI run 41115436575](https://github.com/gobifrost/bifrost/actions/runs/41115436575)
passed all required checks on that exact candidate:

- Backend units: 10,976 passed, 65 intentionally deselected slow tests.
- Client units: 4,021 passed across 606 files.
- Backend E2E: 2,404 passed, 40 existing skips; shards 809/0, 799/5 and 796/35
  passed/skipped. Source-file-size balancing accounts for the changed shard mix.
- Critical browser smoke: 17 passed, no retries.
- Lint/types, both candidate image builds and Product Updates: passed.

Both language analyses passed on source 6fc98159a, and all five prior inline
CodeQL review threads were resolved. Full backend E2E ran in the protected queue;
it was not repeated locally. Full Playwright was not run locally; the exact
critical smoke and targeted probes above are the browser evidence. The required
post-source Product Updates activation is tracked separately below. No release
publication, production deployment or new performance measurement occurred.
