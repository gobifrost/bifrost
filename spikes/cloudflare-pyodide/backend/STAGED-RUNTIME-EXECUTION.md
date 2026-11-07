# Fresh staged artifact executes real engine/SDK workflows locally

The exact output of stage-qualified-runtime now passes actual local cf CLI /
workerd Pyodide execution, beyond the preceding Node assembly check. Two tenant
attempts use genuine run credentials and upstream workspace/config/table APIs.
One intentionally fails after effects; its successful peer remains live and
finishes. Each physical invocation launches once.

Observed canonical checks: API-derived source pins and declared package binding,
peer 403, one failed/one successful terminal, access 401 after each terminal,
stale-owner refusal, duplicate-dispatch reuse, lost-ACK reconnect/reconciliation,
unchanged replay, awaited SDK close and final accounting counts [2,2,1,0].
The private adapter retires after canonical ACK and owned fixture data is removed.
This remains a fixture-supplied control connection, not public signed Pyodide
startup or deployed canonical transport acceptance.

## Explicit input profile, no substituted closure

The existing controller adds only an explicit --staged-runtime mode. It verifies
the staged payload 61d818ae..., both exact JavaScript loader files and SDK/engine
provenance before run creation or platform effects. Its historical default still
requires the original bc4eae48... closure; the separate SDK-update mode keeps its
own exact pin and selection check. Modes are not silently substituted or retried.
The new profile requires the real SDK-close response evidence for both attempts.

Before the actual run, a bounded source-only probe accepts the real staged input
then independently alters the payload, loader, cleanup adapter and provenance.
All four alterations are refused before runtime creation; restored input passes.
No cryptographic pin or SDK/API call is mocked by that probe.

The old SDK-refresh verifier now reads the controller from its unchanged tested
422480753 snapshot rather than pretending the new profile existed in old receipts.
That historical verifier still passes; original receipts are unchanged.

## Exact source, command and results

API source 1d33150eb376b71f8a81b427920eda1b0447f829; packaged engine
f49d4ba99cebd24aa497f1244833b940e640885f; SDK source
139596497d57b219ddcc3616aa66ec58e1b3cf3c. Staged payload SHA256
61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731,
unchanged from the qualified source/wheel artifact. The current loader/cleanup
pins are in the receipt's staged_runtime_provenance. Runtime bundle remains
Pyodide 314.0.6 / CPython 3.14.2 with the existing qualified native wheels.

Actual command in CT128:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/staged-runtime-v1/ct128-canonical-failure.py --staged-runtime --revision 1
node spikes/cloudflare-pyodide/backend/verify-staged-runtime-execution.mjs
```

The exclusive packet derives from the preserved sdk-refresh-v5 public fixture
files, with only the four fresh staged runtime files, controller and pin helper
replaced. Exact packet hashes, commands and the sanitized canonical results are
in ../results/staged-runtime-execution/v1.json. Private raw logs stay in the owned
guest run; they are not copied into the public contribution.

Existing CT128 2 CPU/8 GiB/no swap, unchanged bounded Compose/image/runtime pins,
provider 1 CPU/2 GiB/no additional swap/128 PIDs. The existing owned test project is
reconciled stopped before starting. Whole lifecycle takes 95.629 seconds including
stack readiness, execution and cleanup; this is not startup or per-workflow latency.
Ruff/Pyright for all three changed/new Python harness files pass before execution.
The 40-test baseline and broad platform suites were not repeated.

Owned provider and project containers are stopped; volumes are retained. No cloud
invocation/deployment, temporary guard, persistent credentials/grants, spending,
guest expansion, runtime/security setting or shared platform-source edit occurred.
Public/untrusted admission remains disabled. The new result qualifies this staged
artifact locally; deployed limits, CPU cancellation/isolation and public startup
remain independent blockers.
