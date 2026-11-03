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

Next: qualify a synthetic gateway on the existing isolated guest with actual
Pyodide HTTP transport and redirect controls. Keep production/public admission
disabled. A deployed test requires its applicable bounded authority; historical
cloud receipts do not qualify this new policy.
