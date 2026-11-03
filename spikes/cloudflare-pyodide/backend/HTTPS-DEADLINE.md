# In-flight HTTPS write across canonical timeout

Prototype0.1.0, actual local workerd/Pyodide and disposable PostgreSQL/Redis/API.
This is not physical cancellation or deployed Cloudflare acceptance.

The fixed named-policy fixture now has a synthetic /hold destination. Its Node
server runs only on the owned Docker test network, without published ports;
the child gateway cannot access its /snapshot or /release control paths. The
destination records receipt of the request but holds its commit and response.
It records no arbitrary credential headers. Lifetime180seconds,32request cap,
1KiB request body cap, at most eight recorded effects.

The failing tenant's actual upstream workflow uses HTTPX to dispatch /hold
before the immutable30second deadline. The peer settles independently. The
trusted parent awaits canonical Timeout, checks revoked access401, unchanged
cutoff, live collection, busy count1 and PostgreSQL memory reservation, then
reads the independent destination: request received, commit false, release
false. Only afterward does the parent release the destination. It commits the
pre-deadline write and responds200. The same Python workflow asserts that a
new vendor write returns410 and a new SDK table write is refused. Its late
engine success is consumed without overwriting canonical Timeout/result/error/
completion timestamp/log IDs. The denied SDK document is independently absent.

## Observed result

One actual qualification, exit0,103.09527005115524seconds whole supervisor
lifecycle (not startup). Busy counts[2,1,1,0], one launch each, canonical
Timeout/Success. Original cutoff preserved, stale deadline owner rejected,
access revoked before late result, reservation retained until collection EOF
and canonical settlement. Both SDK close calls return; fixture/worker/collector
retirement completes. All owned test-project containers stop; volumes and the
separate dev stack remain untouched.

Independent event order: hold_received, then parent_release, then hold_committed.
The before-release snapshot (read after canonical Timeout) has four ordinary
vendor effects and no held commit. The after-release snapshot adds exactly one
/hold effect and no late-forbidden write. The vendor410 assertion is in the
hash-pinned workflow source; successful late execution proves that assertion
passed, rather than treating a missing server request alone as proof.

The early Wrangler POST200 lines describe dispatch streams, not completed
workflow bodies. The causal server snapshots and terminal receipt establish
the outcome; those log timings are not runtime duration measurements.

Same existing CT128 2CPU/8GiB allocation; provider1CPU/2GiB/128PIDs; cached
image33cd92be, API1d33150, enginef49d4ba, SDK139596497, bundle61d818ae,
cf CLI1.0.0-beta.26, Wrangler4.156.0 and existing cached workerd. No dependency
install, native build, cloud invocation, credentials/grants or resource expansion.
No new CPU, peak memory or billed-cost measurement is claimed.

```sh
# CT128, packet staged from the preceding named-policy fixture plus changed sources:
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/https-deadline-v1/ct128-canonical-failure.py --https-deadline --revision 1
# Repository:
node spikes/cloudflare-pyodide/backend/verify-https-deadline.mjs
```

Raw receipt, input hashes and generated local entry are in results/https-deadline.
Ruff/Pyright pass for the changed Python harness;12 focused Node policy/deadline/
generator cases pass. Historical named-policy and canonical-HTTPS verifiers pin
their changed generators/harnesses to75b872cf1/338eac91a respectively.

## Contract consequence

Refusing a new dispatch after deadline does not undo a previously authorized
HTTP request. Credential revocation likewise cannot retract a request accepted
by a third-party service. Preserve its uncertain effect until external outcome
reconciliation; never retry the write or fabricate rollback from Timeout.
This test proves one deterministic local accounting/containment case. It does
not qualify arbitrary I/O cancellation, automatic recovery after host loss,
provider CPU enforcement, memory isolation or public/untrusted admission.

Next: exercise a pre-deadline transport cancellation/revocation while the vendor
request is held, preserving the original failed receipt and uncertain capacity.
Deployed proof still requires its applicable bounded authority; no cloud test
is implied by this local success.
