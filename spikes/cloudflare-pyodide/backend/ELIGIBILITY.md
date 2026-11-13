# Cloudflare eligibility contract for normal worker workloads

Derived from measured runs on spike `12049f958`. This is the rule set that decides whether a
"normal" worker duty goes to the Cloudflare backend or stays on Linux. Every clause cites the
measurement that produced it; nothing here is aspirational.

## Verdict summary

| Duty | Status | Basis |
|---|---|---|
| Short I/O workflows (SDK/HTTP/JSON/tables) | ✅ Eligible | 5/5 benchmark workloads green, full gates |
| Multi-minute wait/poll workflows | ✅ Eligible with a subrequest estimate | 9×40 s poll held 332.5 s, Success |
| Data providers | ✅ Eligible | Miss 280 ms; cache hit 99 ms without executing user code |
| Agent runs | ✅ Eligible (single-step proven) | `agent.run` completed, 1 iteration, $0.0000344 |
| Long tail of PyPI packages | ⚠️ Curated allowlist only | Bundle ships 11 distributions; +13 % for requests/msal |
| Persistent/supervised services | ❌ Linux | Runtime rejects them by design |
| Native-extension dependencies | ❌ Linux | No pip; wasm wheels only |
| Arbitrary CPU-heavy loops | ❌ Linux | Provider throws, does not kill; operator cost |

## Eligibility inputs (all declaration-driven)

An attempt is Cloudflare-eligible when every input below is satisfied **before** dispatch.
These map 1:1 to the enforcing gates already live in the executor.

1. **Modules** (`requirements.modules`) — every declared workspace module must resolve in the
   staged bundle manifest, else the capability gate rejects with 422 before any effect.
2. **Third-party packages** — every imported distribution must be in the curated allowlist
   (`backend/DEPENDENCY-CENSUS.md`). Today: httpx, pydantic(+core), python-dotenv, PyYAML,
   redis, requests, urllib3, charset-normalizer, msal, PyJWT, certifi.
3. **Egress** (`egress.hosts`) — exact HTTPS hosts the workflow may reach. Unlisted destinations
   are rejected with zero calls. Anything requiring an unlisted host needs an explicit allowlist
   entry, not a silent fallback.
4. **Subrequests** — the dispatcher must estimate request count (SDK calls + third-party HTTP +
   module resolution) and require it to fit the configured allowance. **This is the real bound,
   not wall clock**: a 5.35-minute poll workflow succeeded; the tenth poll at `subRequests: 12`
   would not.
5. **CPU** — a bounded budget the operator sets per workload class. CPU burn is an operator
   cost, not a platform safety gate; no kill primitive is assumed.

## What the dispatcher should never do

- Never route on wall-clock duration alone; duration is largely free, subrequests are not.
- Never let a missing declaration silently fall back to Linux. Rejection or an explicit policy
  decision, never a silent backend switch.
- Never assume the provider will stop runaway code. It throws; the code may catch and continue.
- Never report provider CPU/memory. They are `null`; capacity planning cannot use them.

## Known runtime traps to reject or warn at admission

- `time.process_time()` raises in Pyodide (`RuntimeError: processor time ... cannot be represented`).
  Workflows measuring their own CPU this way must run on Linux.
- msal `Authority` performs eager OIDC discovery; real token acquisition needs an allowlisted
  egress host and, for RSA/EC signing, `cryptography` (not bundled).
- Packaging: workflow files must match the fixture/dispatch naming contract; the capability gate
  rejects unknown workspace shapes rather than running them.

## Residual gaps this contract does not close

- Routing implementation itself (who computes the subrequest estimate and picks the backend)
  is not built; this defines its inputs.
- Production deadline enforcement is a dispatcher concern; the qualification fixture admits
  ≤30 s declarations and does not enforce `workflow_deadline`.
- Provider CPU/memory accounting remains unavailable, so per-tenant cost attribution stays
  estimate-based.
