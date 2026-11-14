# Preserve possibly launched work after a malformed ACK

The actual upstream ThinRemoteProcessPool previously discarded a workflow or
service route when its launch reply lacked a valid PID. The launcher could
already have started that route; a later result was then ignored. The adapter
already preserves routes after ConnectionError/TimeoutError because repeating
uncertain work can duplicate effects. Malformed success replies now take that
same uncertainty path.

The change is limited to `_remote_pid_from_ack`: it still accepts only a positive
integer PID and rejects booleans. Invalid/non-object replies raise ConnectionError
instead of RuntimeError/AttributeError. The existing dispatch handlers retain
the route and its context with remote_pid=None until inventory or a terminal
event resolves it. No fabricated PID, opaque-handle acceptance, new handler,
retry, alternate provider, scheduling policy, runtime setting or schema was added.
Explicit launcher errors retain their existing rejection/cleanup behavior.

The API's existing fenced terminal callback remains authoritative. Retaining a
route does not certify the process exists, authorize another launch, acknowledge
a result, or prove physical termination. This closes one uncertainty defect in
the proposed physical boundary; it does not implement an isolate backend.

## Executed experiments

| Hypothesis/change | Command inside the assigned guest | Observation | Next |
| --- | --- | --- | --- |
| New expected ACK-retention tests against unchanged source | `timeout 650 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/ack-uncertainty-v1/ct128-ack-uncertainty.py` | 17 cases, 13 expected failures, no errors/skips; supervisor 97.463 s | Narrow ACK error classification |
| Correction plus existing pool, credential and journal contracts | Same command with `--after` | 77 passed, no failures/errors/skips; supervisor 98.657 s | Static checks/review |
| Ruff and Pyright on changed shared source and two test modules | `timeout 260 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/ack-uncertainty-v1/ct128-ack-quality.py` | Both exited 0; Pyright 0 errors/warnings | Normal CI and maintainer review |

The service test was simplified before the after run: its initial version also
assumed a second direct route_service call reused its route ID. Current upstream
generates a new route ID on every such call; canonical claim ownership decides
whether to dispatch. That unrelated assumption was removed, rather than adding
a second deduplication policy to satisfy it. The baseline failed earlier at ACK
validation, so it still reproduces the actual defect. The two receipts preserve
their distinct exact test hashes; the after verifier checks current source.

Regressions cover missing PID/opaque unit-only field, bool, zero, negative,
string, null and list ACKs; late result delivery exactly once; service callback
replay; existing positive Linux PID/lost ACK behavior; explicit rejection cleanup;
and continued rejection of unavailable provider memory. Transport/Redis doubles
isolate the malformed ACK cases: these do not prove a real launcher returned a
malformed ACK or that a physical child ran. The broader selected journal tests
exercise actual PostgreSQL/Redis canonical contracts; no workerd/cloud child ran.

Base guest source 1d33150eb376b71f8a81b427920eda1b0447f829, plus the three exact
overlays in results/ack-uncertainty/{before,after}.json. Current shared remote_pool
was unchanged from that base before this fix. Existing exclusive checkout
/opt/bifrost-tests/pyodide-ddf316c1/upstream-025f6c0 and project
bifrost-test-5dc8d94e were reused only after verifying they were stopped and had
only their known bounded Compose overlay. No unrelated dirty work was replaced.
The helper now preserves the known test/source overlays; it must not be blindly
rerun against that modified checkout or its exclusive receipt paths.

CT128 stayed 2 CPU / 8 GiB, no swap. Existing Compose bounds stayed unchanged:
test-runner 768 MiB, API 1 GiB, worker/PostgreSQL 512 MiB, other fixtures bounded.
All owned test services stopped after both runs; volumes and live dev stack were
preserved. No laptop Docker, cloud invocation, deployment, new access, spending,
security setting or production effect. The two harness helpers passed bounded
guest Ruff. The updated PR1161 Product Updates disposition remains a draft author
proposal and passed the canonical validator in the cached network-disabled image.
Broader suites were not rerun locally; normal CI remains required.

## Ownership and remaining gate

Before editing, the known Containers worktree was read-only checked for dirty
remote source; none was present. Existing Eddie request
b1d7db8d-f5e6-40e3-ae13-f532c90496fc reported no concrete overlapping remote_pool
edit/plan through its retained Containers run107/messages12080. Its reply was
acknowledged under the existing caller/run. This is limited conflict evidence,
not ownership release, maintainer acceptance or runtime authorization. The
Containers branch/resources and protocol/launcher/host/placement files were not
changed or steered.

Historical REMOTE-HANDLE-BOUNDARY.md and its five-case receipt retain their old
source pin and describe the pre-fix behavior. The validator does not rewrite old
evidence. Current check:

```sh
node spikes/cloudflare-pyodide/backend/verify-ack-uncertainty.mjs
```

The actual deployed CPU quota continuation counterexample remains unresolved.
Pyodide cannot advertise general workflow.execute support. The next boundary is
coordinated, versioned physical-handle negotiation and reconciliation, with
canonical pre-claim eligibility and a genuine provider stop guarantee. This fix
does not silently extend the positive-PID wire contract to isolates.
