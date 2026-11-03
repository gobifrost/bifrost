# Refuse direct HTTPS when only the BiFrost API is supplied

The private adapter advertised platform-fetch-https as a supported declaration,
but its actual parent TestApi capability admits only the fixed virtual BiFrost
origin and SDK/table/refresh routes. A workflow could therefore bind successfully
and fail only when it attempted an external integration call. That contradicted
pre-effect rejection of declared unsupported workloads.

The private supported vocabulary now contains json, async-workflow,
pinned-workspace and bifrost-http-sdk. Direct HTTPS, alone or mixed with SDK
requirements, fails before storing the plan, native Redis bookkeeping, journal
reservation, invocation collection or result callback. Existing SDK declarations
are still accepted. No automatic Linux fallback is introduced.

This is a missing network adapter/capability, not evidence that Pyodide cannot
perform HTTPS and not a native-wheel/OS build failure. The low-level runtime
and historical network probes retain their own scope. A PyEmscripten compilation
cannot add an omitted parent egress route; no new dependency was compiled or
reimplemented for this correction. Custom CA/mTLS/proxy contracts remain separate.

## Evidence

The new native adapter regression fails against the previous source: no ValueError
is raised and the external-HTTPS plan is stored. That first declaration produces
two assertion failures; the later mixed subcase is not reached in that red run.
After removing the false capability, all 31 adapter cases pass, including both
external declarations and the positive SDK subset. Ruff and Pyright pass in
bounded network-disabled CT128 containers (0.5 CPU/1 GiB/no additional swap/64 PIDs).
The fixed-origin route suite also checks two ordinary external HTTPS destinations
are refused; it makes no network call. These are admission/routing tests, not a
new actual SDK or external integration run.

The preceding staged engine/SDK receipt remains an actual local proof at its own
adapter source pin; its declarations use only the four still-supported features.
No runtime bytecode, SDK transport or canonical platform source changes here.
The old refusal-ACK verifier reads the two changed adapter files from their tested
ea9a8eae0 snapshot; it does not relabel the old receipt as a test of the new guard.
Raw fail-first/pass/quality output is in ../results/https-capability/.
The original red console text is retained in before-raw.json; before.txt trims
only trailing console spaces so the contribution passes whitespace checks.

## Required next implementation, not completed scope

Direct external HTTP orchestration remains part of the spike objective. This
refusal is not a substitute for implementing it. The next adapter should supply
a trusted, explicit approved-origin policy, preserve canonical deadline/revocation,
keep BiFrost runtime authorization out of third-party requests, handle redirects
and uncertain in-flight writes honestly, and prove actual requests in a Python
Worker. It should reuse the existing parent capability boundary, not create a
new durable queue or orchestration service. Until it is implemented and qualified,
workflows requiring direct third-party HTTPS stay on Linux.

No Worker deployment/invocation, new domain permission, production effect,
credential, grant, spending, runtime/security setting or guest expansion occurred.
Public/untrusted admission remains disabled. The broader goal is still incomplete.
