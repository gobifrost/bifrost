# Refreshed deployed engine/SDK qualification

On2026-11-08UTC, the refreshed engine/worker/context overlay passed the actual
Cloudflare Python Worker canonical fixture. Source1796c6f, SDK client139596497,
native API1d33150. This closes the deployed-refresh gap after local SDK acceptance;
it is not whole-current-main or arbitrary-Python acceptance.

Three fresh child requests, no retry: inventory plus two tenant workflows. Real
workspace provenance/imports, config reads, table effects, peer denial,
log/error/result redaction and persisted logs10/1 passed. Expected Failed/Success
outcomes, SDK close and ended credentials401 passed. One launch each; duplicate
dispatch, stale-owner rejection, lost terminal ACK/native-owner reconnect,
canonical reconciliation, accounting `[2,2,1,0]` and fixture cleanup passed.
The original fixture's hardcoded cloud_invocations=0 is not used as the cloud
count; the relay independently records3, with response digests preserved.

Open version4a55fb13-eb84-49e1-b950-8aaf3fe4359d on the existing isolated
executor. Provider module readback:15,410,508bytes,
SHA25632fcf77c6c89c9330670166bad1092fba6b2e560ac487f2eff874795e501a870,
identical to the cf CLI prebuilt upload. CT128/Windows transfer archive hashes
matched4a915966f3d05bac5d99ee15d8263af20e7f8f4971a4cd58aea87cb6727f35d5.
Upload15,049.32KiB/gzip4,336.46KiB. CLI parent startup2ms is not Python cold start.
CPU, isolate peak memory and billed cost were not measured.

The cloud entrypoint composes the same fixed three-file overlay with the existing
guarded canonical transport. The base artifact pin is unchanged; the explicit
overlay is now qualified locally and deployed for the reviewed fixture. Before
upload, all accepted candidate digests and the cached image pin were independently
verified. The12-package runtime inventory remains Pyodide314.0.6/Python3.14.2,
cpython-314-wasm32-emscripten. No dependency substitution or new wheel build.

## Commands and bounds

`prepare-engine-context-cloud.py` builds from the completed local receipt and
fresh output under runs/engine-context-cloud-build-v1: network none,1CPU/2GiB,
128PIDs,120s bound, cached cf1.0.0-beta.26/Wrangler4.156.0. Account operations used
cf1.0.0-beta.22: `cf deploy --prebuilt --provision=false --secrets-file <private-file>`.
The same existing shared-native-pool.py fixture runs on CT128 with explicit cached
image,180s outer bound and the matched test API. Exact command is documented in
CLOUD-CANONICAL-ACCEPTANCE.md; this run's log is runs/engine-context-cloud-v1/qualification.private.log.
Only its physical transport forwards to the refreshed deployed candidate.

A fresh3-child/$0.10/30-minute window was used, without extending/reusing a retired
profile. Exactly3 requests were sent. No CPU/subrequest/security/observability
limit was expanded: existing1000ms/32-subrequest fixture settings retained.
The SDK-only tunnel required a random guard; unauthenticated probe403. Hardened
relay URLs have fixed authority and do not follow redirects. No general API exposure.

## Independent closure

Both Workers now have workers.dev/previews disabled, expiry0 and no temporary
secrets. Executor active version15873d67-b4ab-4be3-84c1-8e8a9118d3ad,
deployment9892f341-6ce8-44ea-bba2-6823868f2c22. Tunnel and owned SSH forwarding
stopped; temporary profiles removed from guest, host spool and Windows. Owned
relay/test containers stopped; volumes, uncertain locks and separate dev stack
preserved. Existing CT128 remains2CPU/8GiB. No production effects or persistent
credentials/grants. Sanitized assertions/build-source hashes, raw response hashes
and closure readback are committed; raw logs/tokens are not.

Ruff/syntax/receipt checks pass. Normal exact-head CI and maintainer review remain
separate. VerdictYELLOW for reviewed SDK/I/O workflows: physical CPU termination,
hostile isolation, abnormal parent interruption and arbitrary background-task
cancellation remain unqualified. No immediate mutable cross-isolate close or
lossless global callback tracing is claimed. Already accepted API effects remain
subject to canonical uncertainty/reconciliation. Arbitrary/untrusted admission
stays disabled; no production backend selector or upstream acceptance is implied.
