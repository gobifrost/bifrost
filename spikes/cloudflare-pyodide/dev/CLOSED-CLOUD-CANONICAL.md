# Canonical candidate deployed closed

On 2026-11-05, cf CLI deployed the exact guarded candidate that passed local
canonical engine/SDK acceptance. This advances artifact deployment, **not deployed
workflow acceptance**. No Python child was invoked or temporary secret created.

* Worker: bifrost-pyodide-spike-ddf316c1 in the existing isolated test account.
* Active version: `074e726f-f924-42f6-9498-6609f4c9cf7d`.
* Deployment: `bcd9106f-2f70-402c-93f3-ca640bf3c449`, 100%.
* Readback: workers.dev disabled, previews disabled, expiry0, secret list empty.
* JS bundle:15,299,685bytes, SHA256
  `9fd388c49d189320d290573c7d77118f92e1ce81e977cb76afffb1bb2a11f13a`.
* CLI reported parent startup2ms, upload14,941.10KiB/gzip4,309.97KiB. Parent
  startup is not Python cold start and does not revise the earlier benchmarks.

`cf-canonical-build.py` validates the completed local acceptance and every staged
source hash, then runs `cf deploy --dry-run` in a fresh CT128 packet: network none,
1CPU/2GiB/128PIDs,120-second bound, empty task HOME and read-only cached packages.
No dependency install or account access occurs in the builder. The build uses
cached cf1.0.0-beta.26/Wrangler4.156.0. The local cf1.0.0-beta.22 uploader consumes
the emitted v0 format with existing authorized account access.

Only `.cloudflare/output` was transferred. Archive SHA256 matched on CT128 and
Windows before extraction/upload. No env/profile/credential files were copied.
`cloud-canonical-build-v1.json` and `closed-canonical-deployment.json` preserve
the build and sanitized readback evidence.

Actual upload from the owned temporary prebuilt directory:

```sh
cf deploy --prebuilt --provision=false --message "Experimental canonical Pyodide d9fa315; closed pending matched-origin acceptance"
cf workers get bifrost-pyodide-spike-ddf316c1
cf workers deployments get bcd9106f-2f70-402c-93f3-ca640bf3c449 --worker bifrost-pyodide-spike-ddf316c1
cf workers secrets list --worker bifrost-pyodide-spike-ddf316c1
```

Resource auto-provisioning was disabled. Worker metadata before apply confirmed
the exact existing target and its retired version. After apply, active deployment
and closure were independently read back. Production resources were untouched.

## Remaining live boundary

The gateway Worker exists but its origin is the old dev2037ac0 API. The matched
canonical test API1d33150 has no currently qualified cloud ingress. Read-only
inspection found only the owned Compose overlay dirty in dev2037ac0, and no
requirements.lock/pyproject differences between those source pins. No checkout,
schema, API, gateway or tunnel upgrade was applied during this increment.

Do not run the older `run_cloud_qualification.py` unchanged: it binds the earlier
API source and does not exercise canonical dispatch. Next prepare a matched,
authenticated temporary origin and cloud canonical controller with bounded
request/cost/expiry accounting and guaranteed closure. A renewed invocation
window must use its own profile and temporary secret, not the retired allowance.
Keep arbitrary/untrusted admission disabled. Parent deployment, Python execution,
canonical reconciliation and physical/resource isolation remain separate gates.
