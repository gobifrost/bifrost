# Browser Impersonation Spike

This local spike reuses `users.impersonate` and the existing `authorize_run_as` organization/protected-target checks. It does not add another permission. Changes live on `codex/impersonation-spike` in this isolated worktree; the debug environment stays running for review.

## Try It

Run `./debug.sh status` in this worktree for the current URL and generated administrator login.

1. Sign in as `dev@gobifrost.com`.
2. Open Users → Jane Demo → Profile, or `/users/4babcb49-822a-44a1-a2ff-9d4c1aeff4bf/profile`.
3. Click **Impersonate**, then open **Customer Workspace** (`/apps/customer-workspace`).
4. The V2 app reads the effective user through the ordinary SDK and supports synthetic organization notes through `useTable`/`tables.insert`.
5. Existing and newly opened regular tabs use the same impersonation session and display the banner.
6. Click **Exit** above the navbar to restore the original admin session across tabs.
7. Open **Auditing** to see the target user and “Impersonated by dev@gobifrost.com”.

The demo app source is `/tmp/bifrost-cli-impersonation-spike/demo-app`. All demo resources are synthetic and exist only in this isolated debug database.

## Session Design

The original spike used a `sessionStorage` override. That deliberately isolated the switch to one tab, while ordinary cookies and other tabs retained the operator identity. It proved the SDK path but left cookie-only resources and browser-wide behavior incomplete.

The revised implementation uses a shared `localStorage` override and an HttpOnly impersonation cookie. Ordinary browser bearer requests and cookie-only requests select the same effective identity. Explicit embed and durable runtime credentials retain their existing boundaries. Entry and exit reload the affected tabs, disposing cached permissions, app roots, and sockets. The target's home organization applies while preserving the operator's saved scope.

The target token expires after 30 minutes and cannot refresh into a normal target login. A Redis session record revalidates the operator's current impersonation permission, target availability, and original refresh-session JTI. The original login cookies remain available solely to recover the operator session on Exit. An expired impersonation marker continues to reject ordinary requests until explicit Exit; it must never silently resume administrator actions.

The WebSocket delivery and incoming-message paths revalidate the impersonation session before processing data. A revoked or expired session closes on its next message rather than continuing to receive through an already-open connection. Idle sockets do not require polling. The host renders the compact banner above both platform and app navigation, including V2 app loading/error states; deployed apps need no SDK upgrade to show it.

Principle: **Interactions, feedback, and system status**. The effective identity and Exit remain visible, including on narrow screens.

## Attribution

Audit rows retain the effective `user_id`, `impersonated_by_user_id`, and `impersonation_session_id`. Shared audit context, access-check collection/writing, live audit APIs, exports, archive snapshots, and audit UI preserve the fields. Operator UUIDs are not foreign keys so account deletion cannot erase provenance; archived rows retain display names as well.

Durable workflow executions, agent runs, and platform jobs must carry the same provenance through enqueue and runtime reconstruction. The fields are attribution only and never grant authority. Background work accepted during impersonation may finish after Exit while retaining its original operator attribution.

A temporary browser session cannot authorize an independent external MCP client. The OAuth callback requires Exit first; browser support tokens are not accepted as standalone MCP credentials.

## Practical Limits

This is a local demo candidate, with focused verification rather than a production rollout.

- The shared audit emitter retains its existing best-effort behavior. An audit-storage failure does not automatically fail the primary operation.
- This extends existing audit coverage; it does not instrument previously unaudited operations or add an operator field to every application log line. Execution logs can be correlated through their durable parent execution.
- Browser-wide means regular tabs sharing this origin and browser storage profile. Separate profiles, private sessions, and other devices have independent logins.
- Full backend, Vitest, Playwright, and merge-queue suites are not claimed as run.

## Verification

The original spike passed its focused backend authorization/audit/contract tests, 145 frontend tests across 16 files, targeted WebSocket transport tests, API and client lint/type checks, and the profile → published V1 app → reload → Exit browser journey. The V2 demo separately proved SDK identity, table insertion, and live updates. Those original checks asserted tab isolation and therefore do not prove the revised browser-wide behavior.

Revised verification results are recorded below after execution.

- `./test.sh tests/unit/routers/test_websocket_table_messages.py -v`: 10 passed, including revocation before delivery and WebSocket operator attribution.

```bash
./test.sh client unit src/contexts/AuthContext.test.tsx src/lib/auth-token.test.ts src/components/layout/ImpersonationBanner.test.tsx src/components/layout/PageShell.test.tsx src/components/users/ImpersonateUserButton.test.tsx src/pages/DevicePage.test.tsx src/lib/api-client.test.ts src/services/websocket.test.ts src/contexts/OrgScopeContext.test.tsx
```

Result: 61 passed across 9 files. An additional embed-logout regression found that clearing an embed could remove shared credentials/cookies. It now clears only the embed capability; `./test.sh client unit src/contexts/AuthContext.test.tsx src/lib/auth-token.test.ts` passed 12 tests after that correction.

```bash
./test.sh tests/unit/services/test_browser_impersonation.py tests/unit/core/test_auth_platform.py tests/unit/routers/test_websocket_table_messages.py tests/unit/services/test_mcp_auth.py tests/unit/services/execution/test_async_executor.py tests/unit/jobs/consumers/test_workflow_execution_lineage.py tests/unit/repositories/test_execution_create.py tests/unit/authorization/test_runtime.py tests/unit/core/test_request_actor.py tests/unit/services/test_agent_run_service.py tests/unit/services/test_platform_jobs.py tests/unit/jobs/test_platform_job_runner.py tests/unit/test_contract_version.py tests/e2e/api/test_browser_impersonation.py -v --durations=0
```

Result: 123 passed, three runtime actor assertions failed. Runtime audit attribution was reading the principal's copied fields rather than the verified durable authority; it now reads the authority directly. The browser auth E2E passed. Its original focused run also passed (one case); neither run launched a build/deploy/workflow job.

```bash
./test.sh tests/unit/core/test_request_actor.py tests/unit/services/test_run_user_cancellation.py -v --durations=0
./test.sh tests/unit/test_access_checks_collector.py tests/e2e/api/test_browser_impersonation.py -v --durations=0
```

Results: 24 passed and 17 passed respectively, with no failures/errors/skips. The latter includes durable runtime access-check provenance and refusal to turn an impersonation session into an independent CLI login. The browser E2E took 8.45 seconds in fixture setup and 0.17 seconds in ordinary HTTP calls; it enqueued no jobs.

Final frontend checks:

```bash
./test.sh client unit src/contexts/AuthContext.test.tsx src/lib/auth-token.test.ts src/components/layout/ImpersonationBanner.test.tsx src/components/layout/PageShell.test.tsx src/components/users/ImpersonateUserButton.test.tsx src/pages/DevicePage.test.tsx src/lib/api-client.test.ts src/services/websocket.test.ts src/contexts/OrgScopeContext.test.tsx
(cd client && npm run tsc)
(cd client && ./node_modules/.bin/eslint src/contexts/AuthContext.tsx src/contexts/AuthContext.test.tsx src/lib/auth-token.ts src/lib/auth-token.test.ts src/pages/DevicePage.test.tsx e2e/impersonation.admin.spec.ts)
(cd client && OPENAPI_URL=https://bifrost-2c50930b-efis.eu1.netbird.services/openapi.json npm run generate:types)
```

Results: 62 tests across 9 files passed; final types and targeted lint passed. Full client lint passed before the final narrow corrections. OpenAPI types were regenerated from the running debug stack. The forward migration was applied to that demo database and its Alembic head is `20261019_durable_impersonation`.

### Final Browser-Wide Result

Exit now refreshes the operator once **before** clearing the shared override. Existing tabs therefore reload with the same fresh access token instead of racing to rotate an expired operator session. Both an Exit error and an operator-refresh error preserve the override/banner. Final focused AuthContext/storage verification passed 14 tests:

```bash
./test.sh client unit src/contexts/AuthContext.test.tsx src/lib/auth-token.test.ts
./test.sh quality api
./test.sh client e2e --screenshots e2e/impersonation.admin.spec.ts --project=platform-admin
```

API quality: 0 errors/warnings and all lint checks passed. Browser result: **2 passed** (setup plus one journey), 46.0 seconds total; journey 26.9 seconds. There is one publish job proving the published V1 host boundary. The test covers an existing app tab, a new tab, the initiating tab, reload, target identity, and restoration across all three tabs through the original refresh cookie even with an unusable saved operator access token.

The browser fixture now owns a separate login and revokes it in cleanup; refresh rotation cannot invalidate the shared setup login used by other tests. Two fixture errors were corrected from evidence: an organization app was initially requested from Global scope (404), and the test initially assumed the debug administrator email instead of reading the actual test operator. No retries, increased timeouts, or skips were added.

The final live V2 browser check also passed:

- Existing tab and new tab both switch; cookie-only `/api/auth/me` identifies Jane.
- SDK table insertion and live update succeed; the new note survives reload.
- Wide/mobile layouts and light/dark themes display the banner; mobile has no horizontal overflow.
- Exit restores all tabs and cookie-only identity to the operator.
- The audit response contained **93 events** for that session, each retaining the original operator identity.

Screenshots and executable browser checks are in `/tmp/bifrost-cli-impersonation-spike/`; the final run log is `/tmp/bifrost-impersonation-browser-wide-demo-verified.log`. The app's optional absent logo returns its existing 404/no-logo state. The catalog's role metadata request returns 403 for the normal target user, enforcing its existing permission boundary; the app identity, read, write, and live-update paths succeeded.

Final TypeScript checking and targeted client lint passed. Generated API types and `git diff --check` passed. The public debug readiness endpoint returned 200. Full suites and merge-queue CI remain unrun; this is a local demo, not a deployment to a shared environment.

### Merge Review

The UI gate now has explicit coverage for a denied impersonation permission and protected targets. Direct unauthorized HTTP starts also return 403. Reviewing the existing demo audit failures found 78 misleading `users.impersonate` observer failures: internal operator revalidation was being judged against the target. Revalidation remains enforced, while its internal notes are excluded from the target collector. Historical audit rows are preserved. The original three `roles.read` denials and wrong-password login were genuine refusals.

The frozen spike security review covered all 72 source inventory items and found two credential-lifetime escape paths (MCP OAuth callback and passkey/MFA enrollment) plus unnecessary support bearer exposure in WebSocket URLs. The callback now rejects support claims regardless of cookie name; support sessions cannot enroll permanent target credentials; ordinary support sockets authenticate with the HttpOnly cookie. The scan's findings and final semantic draft are saved, but report finalization failed and no completed security report is claimed.

Additional focused verification:

```bash
./test.sh tests/unit/test_access_checks_collector.py tests/unit/services/test_browser_impersonation.py tests/unit/services/test_impersonation.py tests/unit/services/test_access_check_writer.py tests/unit/services/test_mcp_auth.py tests/unit/services/mcp_server/test_auth_code_binding.py tests/e2e/api/test_browser_impersonation.py -v --durations=0
./test.sh tests/unit/routers/test_browser_impersonation_credentials.py tests/unit/services/test_passkey_service.py tests/unit/test_user_mfa_reset.py tests/e2e/api/test_browser_impersonation.py -v --durations=0
./test.sh tests/unit/routers/test_browser_impersonation_credentials.py tests/e2e/api/test_browser_impersonation.py -v --durations=0
```

Results: 107 passed in the first run. The second had 13 passes and one endpoint-test failure: cookie-only writes were correctly stopped by CSRF before reaching the new guard. Matching the browser's explicit bearer transport fixed the test fixture; the final run passed all four cases, including five credential-endpoint refusals. No retries or weakened CSRF checks were added.

The final focused frontend run passed 68 tests across the same nine files listed above. A fresh live V2 demo passed SDK identity, note creation/live updates, reload, cross-tab entry/Exit, and cookie identity restoration after removing the WebSocket query bearer. Its audit response retained operator/session attribution, and a direct database check found no new false `users.impersonate` failures. Genuine `roles.read` denials remain visible. Log: `/tmp/bifrost-impersonation-merge-live-demo.log`.

Final API quality (`./test.sh quality api`) passed with zero errors/warnings and clean Ruff. The shell fix makes the header's bottom border part of its 64px height, moves the banner above the sidebar/header row, and constrains the sidebar to the remaining row height. Live geometry at 1440×1000: both normal dividers at y=64; both impersonated dividers at y=109 below a 45px banner; sidebar bottom y=1000 in both modes. All 26 focused Header/PageShell/Sidebar tests and targeted layout lint passed. Screenshots: `/tmp/bifrost-cli-impersonation-spike/shell-normal.png` and `shell-impersonated.png`.

PR review: CodeQL's two `StorageEvent` warnings (1692/1693) were dismissed as false positives with the installed DOM constructor signature and passing cross-tab tests as evidence. Its empty-except warning (1694) prompted removal of redundant exception handling: revocation already validates the signed marker's session UUID before the audit path. `./test.sh tests/e2e/api/test_browser_impersonation.py -v --durations=0` passed both cases after that cleanup. Final client `npm run tsc` and full `npm run lint` passed.

The full PR backend unit run found nine integration-accounting gaps (9,535 other tests passed): the normal scheduler payload expectation lacked the new nullable attribution field; new browser-auth routes/resolvers/token mint/Redis reads needed authorization inventory entries; generated operation inventory and skill API references were stale. All 3,942 client tests passed in CI. These were fixed without changing the runtime permission model or weakening assertions. Scheduler coverage now also proves persisted operator/session IDs reach the publish payload.

```bash
./test.sh tests/unit/jobs/schedulers/test_deferred_execution_promoter.py -v --durations=0
./test.sh tests/unit/test_access_list.py tests/unit/services/test_operation_inventory.py tests/unit/test_skill_appendix_fresh.py tests/unit/jobs/schedulers/test_deferred_execution_promoter.py -v --durations=0
./test.sh tests/unit/test_access_list.py::TestEveryElevatedCheckHasAScope::test_every_elevated_check_site_names_its_replacement tests/unit/test_access_list.py::test_personal_and_own_agent_writes_are_all_on_the_reviewed_allowlist tests/unit/test_access_list.py::test_allowlist_has_no_unused_entries -v --durations=0
python3 scripts/check_skill_mirrors.py
```

Results: scheduler 17 passed; combined inventory/generated/scheduler run 100 passed with two newly exposed registry omissions. Registering the original-human resolver's copied actor facts and the ownership-checked personal Exit route fixed those omissions; the final three affected assertions passed. Mirror validation passed. The new auth endpoints are classified as authentication transport, rather than CLI/MCP entity mutations; every new Redis liveness/ownership read is authority-bearing in the inventory.

The next complete backend unit run passed 9,545 tests and exposed one remaining snapshot assumption in `test_permissions.py`: `users.impersonate` was still listed among privileged permissions without a registered operation. Removing it from that exception set tightens the inventory now that the browser start endpoint names the permission. `./test.sh tests/unit/test_permissions.py -v --durations=0` passed all 175 cases. The runtime privileged-permission set is unchanged.

The first merge-queue candidate passed backend/client units, lint/types, both image builds, browser smoke, and E2E shards 2/3 and 3/3. Shard 1 exposed a test ownership defect: the preceding revoke-all-sessions test invalidated the session-scoped administrator refresh token, which the browser impersonation test reused. The server correctly returned 403 rather than minting a support session from a revoked login. An ordered local reproduction produced the same failure (one failed, two passed).

Both browser API tests now create a fresh login from a copy of their user fixture and own a private cookie jar. Cleanup exits impersonation and revokes only that login's refresh JTI. The bearer-only precheck still starts with no cookies, and all authorization, credential-enrollment, marker revocation, and expired-marker recovery assertions remain. Runtime authentication was not changed.

```bash
./test.sh tests/e2e/api/test_auth.py::TestLogoutAndRevocation::test_revoke_all_sessions tests/e2e/api/test_browser_impersonation.py -v --durations=0
```

The exposing order passed all three tests (JUnit: zero failures/errors); focused Ruff passed in the Docker test image. No retries, skips, or timeout changes were added. Broader suites are left to the new merge-queue candidate; the test-only correction does not change the API schema or application behavior.
