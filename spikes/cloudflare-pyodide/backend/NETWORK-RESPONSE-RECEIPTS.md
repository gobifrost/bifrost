# Network-response receipt review

Scope: the network-data-written-to-file annotations on PR1161. These scripts
were inspected, not invoked. Cloud authorization remains retired. No alert was
dismissed and no scanner exclusion or security control was changed.

| Exact source | Destination and content | Disposition |
|---|---|---|
| cloud.mjs | Fixed results/cloud-imports.json and cloud-memory.json; bounded Worker JSON/text | Intended diagnostic data; reject known temporary auth echoes before persistence |
| cloud-security.mjs | Fixed results/security-cloud.json; bounded probe responses | Same guard, with SPIKE_AUTH supplied |
| backend/cloud-isolation.mjs | Finite locally selected results filename; bounded response/error records | Same guard; response cannot choose filename; budget ledger/control unchanged |
| backend/transport.test.mjs | Fixed current-runtime-sync-transport.json; bounded synchronous transport diagnostic | Inert JSON, no credentials supplied; serialized size bounded |
| backend/invalid-credential.test.mjs | Fixed current-runtime-invalid-credential.json; bounded rejection response | Real ordering defect: formerly wrote before checking known credential echo. Now reject before write |
| backend/fresh-cleanup-readback.mjs | Fixed fresh-cleanup-readback.json; HTTP status and verified empty secret-binding list | Inert JSON; removed rewriting historical config captures during normalization |
| backend/external-cancel.test.mjs | Fixed external-cpu-cancel-local.json; callback events and bounded response text | Inert JSON; serialized size bounded; no change to cancellation claims |
| sdk-proof.mjs | Fixed results/sdk-readback.json; bounded independent REST readback | Reject the actual in-memory API token before persistence |
| termination.mjs | Fixed results/termination.json; bounded interrupted-request outcome | Inert JSON, serialized size bounded; not a physical isolate-stop proof |

The annotated data flows exist: recording responses is the purpose of these
receipts. They do not interpolate a network value into a pathname, shell,
dynamic import or executable artifact. JSON strings containing JavaScript or
path traversal text remain data. This source disposition does not establish
that CodeQL has accepted the changes.

Concrete fixes use diagnosticJson in probe-io.mjs before each annotated response
write. It rejects a known secret in raw or JSON-escaped form with a constant
error message, and caps the UTF-8 serialized receipt at 1 MiB including escaping
and formatting. Existing bounded response readers still cap incoming bytes.
The helper preserves successful JSON values; it does not silently redact or
substitute workflow results. Unknown credentials, transformed/encoded secrets,
other benchmark writers and arbitrary log sinks are **not** thereby qualified.
Fixed-path overwrite/symlink safety is a separate boundary; these legacy scripts
were not run and their historical receipts were not overwritten in this work.

Verification: `node --test spikes/cloudflare-pyodide/probe-io.test.mjs` passes
eight offline tests, including nested/escaped token echoes, serialized expansion,
UTF-8 boundary, invalid guard configuration and a real temporary-file regression
showing an echoed credential is rejected before any receipt file is created.
The test uses synthetic Response objects, not an SDK/cloud compatibility claim.
All changed JavaScript modules pass node --check; git diff --check passes.

Next acceptance requires current-head CodeQL and, where authorized, runtime
validation. These source tests do not renew any cloud invocation budget.
