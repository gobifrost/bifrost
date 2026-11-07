# Per-attempt HTTP invocation component

`http_invocation.py` extracts the physical HTTP operation from the fixed native
qualification harness. It freezes one authorized request, owns its HTTP session
through stream EOF, binds accepted/terminal frames to the execution identity,
and refuses a second collection/POST. Redirects, shared cookies and ambient
proxy configuration are disabled. The trusted caller still owns authorization,
dispatch deduplication, fencing, deadline reporting and canonical results.
This component neither issues credentials nor creates a scheduler or job queue.

CT128 qualification passed with the actual upstream engine and SDK: config read,
workspace module loading, one successful table upsert of 70, cross-tenant 403,
canonical Timeout and token 401 before late completion, actual Success after
40177 ms, lost terminal ACK and native journal replay. The canonical Timeout
remained unchanged; one launch occurred and the handle was eventually released.
The provider remained running/no OOM before owned-container cleanup. All owned
test containers were stopped afterwards; no cloud invocations occurred.

Four focused native unit cases separately cover immutable identity/credentials,
single-use POST, concurrent framing/session separation, redirect refusal and
request admission bounds. Mock framing tests are not parallel engine/SDK proof.
That fixed single-run transport stores singleton route state. The later
[shared-pool fixture](SHARED-NATIVE-POOL.md) instead uses per-attempt slots and
qualifies two real tenant runs through one local native pool. A reusable
deployment adapter and combined deployed lifecycle remain outstanding.

The guest receipt records image, source, workspace and runtime packet hashes:
[raw receipt](../results/http-component/v1.json). Same CT128 2 CPU/8 GiB allocation;
provider 1 CPU/2 GiB/128 PIDs. Supervisor 102.420 s includes setup, deliberate
40-second async work and cleanup; it is not startup, CPU or billing evidence.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/http-component-v1/ct128-canonical-failure.py --http-component
node spikes/cloudflare-pyodide/backend/verify-http-invocation.mjs
```

Normal awaited completion/EOF does not require isolate destruction. Abnormal
CPU cancellation and deployed isolation remain unqualified; admission remains
disabled. No new deployment, account action or security setting was introduced.
See [lifecycle assessment](LIFECYCLE.md) for official documentation and the
retained failed cancellation/tail experiments.
