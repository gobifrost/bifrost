# Approved HTTPS policy candidate

Source-only experiment, prototype 0.1.0. It is not connected to any Worker,
provider binding or admission declaration. Direct HTTPS remains unsupported by
the private canonical adapter. No network requests or account changes occur in
the tests below.

Cloudflare documents intercepting outbound fetch/connect through a parent
globalOutbound gateway:
https://developers.cloudflare.com/dynamic-workers/usage/egress-control/
This candidate implements only the fetch policy function, not that binding or a
connect handler. A future gateway must refuse connect and route every relevant
HTTP request through the policy; no default outbound path may bypass it.

The trusted parent supplies exact HTTPS origins (empty by default), a fixed
1–100 request budget, forbidden run-credential strings and the existing immutable
attempt deadline. Inputs are snapshotted. Denied destinations/headers consume no
request budget; forwarded requests, including failures, do. The gate forces
manual redirects, preserving a redirect response for the caller. Follow-up
destinations must pass the same gate. Known cleartext credential values in any
header are denied; ordinary vendor authorization remains intact.

This is not arbitrary-code exfiltration prevention. Encoded credentials, bodies,
query strings and responses are not inspected. Origin approval does not prove
DNS/address safety, and redirects through the actual Python/parent chain remain
unqualified. Tenant policy selection and secure vendor credential injection are
not implemented here. Never supply this candidate with workflow-chosen policy.

Expiry/close refuses future dispatch. A write dispatched before expiry can still
commit afterward; the original transport error is preserved and never retried.
Neither this policy nor the tests prove in-flight cancellation, CPU termination,
Cloudflare resource enforcement or deployed isolation.

## Reproduction

From repository root:

```sh
node --test spikes/cloudflare-pyodide/backend/approved-https-candidate.test.mjs spikes/cloudflare-pyodide/backend/attempt-egress.test.mjs spikes/cloudflare-pyodide/backend/test-api-route.test.mjs
```

2026-11-03: 15 tests pass, zero skipped. These are host Node policy tests using
real Request/Response objects with injected transport, not SDK/local-workerd/cloud
acceptance. No heavy laptop containers were started. Six new cases cover exact
origin/default denial and policy snapshots, invalid configuration, credential
headers, deadline/close isolation, lost-response uncertainty and redirects.

## Actual local Pyodide qualification

CT128 bifrost-pyodide-test-01, existing 2 CPU/8 GiB allocation; owned provider
limited to 1 CPU/2 GiB/128 PIDs, network disabled, cached dependency image
sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b.
The supervisor verifies the existing runtime bundle and wrapper hashes before
starting cf CLI 1.0.0-beta.26. Runtime reports Python 3.14.2 and
cpython-314-wasm32-emscripten. No new package, wheel build or runtime patch.

Packet: /opt/bifrost-tests/pyodide-ddf316c1/inbox/approved-https-v3.
Command: timeout -k 5 120 python3 <packet>/ct128-sdk-probe.py --approved-https --revision 3.
Exit 0, whole supervisor lifecycle 8.67748586088419 seconds (not startup alone).
Real pyfetch statuses: 403 destination, 403 credential, 200 write, 302 redirect,
429 exhausted. Independent Node loopback observer recorded exactly the write
and redirect requests; neither denied request reached it. Vendor Authorization
and body were preserved. Python explicitly uses manual redirect mode. Automatic
redirect mode, actual external TLS and canonical engine/SDK integration remain
unqualified by this test. Deadline/cancellation tests are still separate.

v1 failed readiness because the new packet omitted package.json. v2 supplied it
but omitted the Wrangler port config, starting on 8787 instead of 8799. Both
failed before Python and were stopped; receipts are preserved. v3 supplied both
existing pinned fixture files and passed. All three exact providers stopped;
volumes and the separate dev stack were preserved. Source hashes and observations
are in results/approved-https, checked by verify-approved-https.mjs.

Next: integrate the candidate into the private canonical engine/SDK fixture with
trusted policy selection and deadline/revocation controls. Keep production/public admission
disabled. A deployed test requires its applicable bounded authority; historical
cloud receipts do not qualify this new policy.
