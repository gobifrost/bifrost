# Challenge-driven cold-module refresh

The real isolated API refuses a validly signed but expired run access token with
`401` and `Bearer error="invalid_token", error_description="refresh_required"`.
Without an adapter, upstream's synchronous cold-module refresh fails with
`can't start new thread`, producing `WorkflowLoadError` before the workflow
document write (independent readback 404). The actual API trace contains only
the denied module GET. This is an unsupported host-thread operation, not a
missing PyEmscripten wheel. Compiling an extension cannot provide that host
contract, so no wheel build or thread emulation was attempted.

## Bounded physical bridge adapter

`pyodide_refresh.py` replaces only the SDK's private synchronous helper-thread
bridge with `pyodide.ffi.run_sync` around the **actual upstream async refresh**.
Upstream retains its coordinator, credential adoption/storage, redaction, API
authorization, challenge detection and bounded replay. No asyncio/threading
global is patched, no token issuer/decoder is cloned, and Linux code is untouched.
The fresh-child bootstrap installs this adapter after marking execution-child
mode and before importing the worker. Its versioned package includes the adapter
and exact digest; runtime fingerprint drift still fails before upstream imports.

The adapted actual Worker trace begins:

1. GET pinned workspace module → 401 refresh_required.
2. POST real `/auth/refresh` → 200 replacement.
3. GET the same pinned module → 200.
4. Actual upstream engine/SDK reads config and writes the workflow document.

Independent readback returns 70. Canonical claim, single physical launch despite
duplicate dispatch, lost-ACK result reconciliation, log/result redaction and
fixture cleanup pass. Access use and refresh redemption both return 401 after
terminal consumption. No mocked API or SDK operation establishes this result.

`results/canonical-refresh-challenge.json` retains the unadapted failure;
`results/canonical-refresh-challenge-adapted.json` retains success and exact
source provenance. `results/refresh-runtime-manifest.json` pins 479 modules,
payload `344afbb5a62e39b2799a52789bbaecaf17d281e66583becb88818c25154b2eb4`.
The lock/native-wheel/runtime identity remains the locally qualified one in
RUNTIME-PROVENANCE.md. Native memory overhead and cloud compatibility are not
measured by this experiment.

## Fixture authority and commands

Only the isolated fixture issuer's clock is patched while calling upstream
`issue_access_token` to mint an expired access token. The API's clock, database
authority, original parent credential, owner lease and fresh run-refresh token
remain unchanged. Parent setup/cleanup uses its genuine fresh credential; the
child receives the expired token. The preflight checks the real API challenge
before invocation. No auth dependency, signing format or enforcement is mocked.

With the isolated API healthy and the authorized-local Wrangler supervisor
attached to that stack, from the spike directory:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-challenge-adapted challenge
node backend/verify-refresh-challenge.mjs
python -m unittest discover -s backend -p 'test_*.py' -v
```

The retained observers use the existing local Docker containers. Run refuses an
existing receipt before effects; verify is offline. Six companion unit tests
cover guard/bridge delegation and failure propagation. The actual Worker result,
not those mocks, proves SDK compatibility. Focused Ruff and 32 Node tests pass.
No broad API/client suite or type-check rerun is claimed for this companion change.

The prior direct-refresh result remains verified against immutable source:

```sh
node backend/verify-canonical-refresh.mjs --commit 982921ceeb781508375518a68a04bd473e72098f
```

## Limits

This uses a private upstream bridge hook and Pyodide's native synchronous-await
facility; upstream changes require requalification. It does not prove concurrent
sync/async refresh adoption, refresh interruption/cancellation, arbitrary threads,
new credentials/grants, hostile code isolation, CPU physical termination or
deployed canonical execution. No new cloud call or shared placement edit occurred.
Own supervisor/network/API state is restored after qualification.
