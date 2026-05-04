# Progress Demo — Org Gate Spot Check

Purpose: verify the table-access org gates are working end-to-end via a real
app + real users + real WebSocket. Run before merging PR #178.

## Setup

The dev stack must be running for this worktree (`./debug.sh`). The seed
script creates two non-superuser accounts in two orgs:

```bash
./scripts/seed-spot-check-users.sh
```

After it runs:

- Org `Provider` (`00000000-0000-0000-0000-000000000002`) — pre-existing.
- Org `Beta`     (`00000000-0000-0000-0000-000000000003`) — created by the script.
- User `alice@gobifrost.com` / `password` in Provider, non-superuser.
- User `bob@gobifrost.com`   / `password` in Beta,     non-superuser.
- The Progress Demo app's `organization_id` is set to `NULL` (global) by the
  seed script so cross-org users can navigate to it. Without this, the
  *application*-level org gate would prevent Bob from ever reaching the page,
  and the table-level gate (the thing this spot-check exercises) would never
  fire.

## What the spot check exercises

The dev stack has **two** tables both named `progress_demo`:

| table_id (prefix) | organization_id | rows produced by `run_progress_demo` |
|---|---|---|
| `dcbf8c77-…` | Provider | 5 |
| `6c5b64fc-…` | (global, no org) | — |

The org gate in `_resolve_table_id` (api/src/routers/websocket.py + the REST
`get_table_or_404` helper) cascades like so for non-superusers querying
`progress_demo` by name:

- Alice (Provider) → matches the Provider-scoped table → sees rows after the
  workflow runs.
- Bob (Beta) → no Beta table named `progress_demo`, so falls through to the
  **global** one → sees an empty result set.

The Progress Demo app's `useTable("progress_demo")` therefore resolves to a
*different physical table* per user. Alice's "Run workflow" populates the
Provider table; Bob's request to run that same workflow ID **404s** because
the workflow itself is org-scoped to Provider.

## Procedure

1. Get the dev URL: `./debug.sh status` (e.g.
   `http://bifrost-debug-…netbird.cloud` or `http://localhost:<port>` in port
   mode).
2. In a fresh browser session, open `<debug-url>/apps/progress-demo`.
3. Log in as `alice@gobifrost.com` / `password`.
4. **Expected for Alice:** the identity panel reads
   - Signed in as: `alice@gobifrost.com`
   - Org: `Provider`
   - Rows visible: `0` (or higher, if previous runs left rows)
5. Click **Run workflow**. Within ~5 seconds, 5 rows stream in via WebSocket.
6. **Verify for Alice:**
   - Rows visible: `5`
   - `Resolved table_id` starts with `dcbf8c77-…` (the Provider-scoped table)
   - 5 progress bars visible
7. Open a private/incognito window. Navigate to
   `<debug-url>/apps/progress-demo` and log in as `bob@gobifrost.com` /
   `password`.
8. **Expected for Bob (before clicking anything):**
   - Signed in as: `bob@gobifrost.com`
   - Org: `Beta`
   - Rows visible: `0`
   - No rows — no "Resolved table_id" line is shown because `rows` is empty.
9. Click **Run workflow** as Bob.
10. **Expected for Bob (after clicking):** a red banner appears reading
    `Workflow refused: …` (the message will mention 404 / Not Found — the
    workflow itself is org-scoped to Provider and Bob can't reach it). Rows
    remain at 0.
11. Open the browser DevTools → Network tab as Bob. The
    `POST /api/tables/progress_demo/documents/query` request should succeed
    with `200` but return `{"table_id": "6c5b64fc-…", "documents": [], …}` —
    i.e. the **global** table, not Provider's. **No Provider rows leak to Bob.**
12. The `POST /api/workflows/<id>/execute` request must return `404`.
13. Check the WebSocket frames for Bob: any subscription to the `progress_demo`
    table is for the global table_id (`6c5b64fc-…`), not Provider's. No
    Provider row payloads should be delivered to Bob's session even if
    Alice runs the workflow concurrently.
14. **Do NOT** log in as `dev@gobifrost.com` (superuser) for this check —
    superuser bypasses the org gate by design and would mask a regression.

## Pass criteria

- Alice's identity panel shows `Provider` and `Resolved table_id` prefix
  `dcbf8c77-…` after running the workflow; 5 rows render.
- Bob's identity panel shows `Beta` and remains at 0 rows; clicking
  **Run workflow** produces a `Workflow refused` banner.
- Bob's network tab: `documents/query` returns `200` with the **global**
  `table_id` and zero documents (no Provider row data); `workflows/<id>/execute`
  returns `404`.
- No console errors that crash the page.

If any of those fail — particularly if Bob's `documents/query` returns Provider
rows, or if his `workflows/<id>/execute` succeeds — the org gate is leaking.
**Do not merge PR #178.**

## Cleanup

The seed users + Beta org persist in the dev stack DB until you tear down with
`./debug.sh down`. They're harmless to leave around; re-running the seed
script just refreshes their hashes idempotently.
