# Canonical failure and printed-output persistence

The retained failure scenario now passes against the repaired engine closure and current-upstream isolated API. This is the actual claim -> local workerd/Pyodide SDK -> canonical consumer path. It is not the production signed control transport, deployed canonical Cloudflare dispatch or physical termination acceptance.

The workflow reads configuration, prints its run credential, emits a structured warning with it, and raises ValueError before the table write. The real upstream engine returns Failed/ValueError with redacted error/output. The normal consumer commits Failed and persists logs. Independent PostgreSQL reads assert the redacted stdout and structured message each occur exactly once; all11 persisted messages are checked against the handed-down credentials before evidence is emitted.

The parent then loses the ACK after canonical completion. A replacement owner session reconciles the retained result normally: the run stays Failed, log row IDs do not change and physical launch count stays1. No proof document exists and no document-upsert call occurred. Access and refresh credentials both return401 after terminal completion. The fixture worker/claim, workspace modules and test rows are cleaned through the existing test teardown.

## Source, commands and evidence

API/consumer source1d33150eb376b71f8a81b427920eda1b0447f829 includes upstream main025f6c019. Runtime engine is the shared stream-capture revision f49d4ba99; prepared artifact SHA is bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0. The fixed failure fixture retains its stdout assertion; the new test adds a direct PostgreSQL message assertion rather than inferring content from row count. Exact packet hashes are in results/canonical-failure-repaired/v4.json.

```sh
# Assigned CT128, exclusive staged packet and existing bounded test project:
python3 ct128-canonical-failure.py
# Actual inner fixture command, after cf CLI local provider readiness:
python /qualification/canonical-local.py --failure-probe
# Offline current and historical evidence:
node backend/verify-canonical-failure-repaired.mjs
node backend/verify-workflow-failure.mjs --commit d352dc663a0dfe40d38892a25b9b78725f0041ee
```

The cf CLI local provider uses the previously verified source-built Python bundle/cache, without new packages or credentials. Its package manifest declares the already-installed cf1.0.0-beta.26/Wrangler4.156.0 pins. Provider cap1CPU/2GiB/no swap/128PIDs, test runner768MiB, bounded supporting services; guest allocation remains2CPU/8GiB. The supervisor has a360s wall bound. Exact elapsed time and cleanup are in the receipt; no isolate CPU/peak-memory measurement is inferred from process limits or OOM status.

Two startup failures are preserved: v1 lacked the declared Wrangler dependency, so cf CLI declined to select a dev server; v2 lacked the /spike mount required by the pinned binary wrapper and stopped before execution. No credentialed fixture launched in either. Bounded changes used existing cached dependencies/mounts, with no installation, access workaround or unchanged retry. V3 passed terminal/error/replay checks and11-row persistence. V4 added direct persisted-message/redaction checks and passed as well. The older failed stdout receipts remain immutable and still verify as failed against d352dc663; this separate result does not overwrite their flags or hashes.

All owned test containers stopped; dev services, volumes and unrelated locks remained. Docker stopping the local provider is cleanup, not an isolate physical-stop primitive. The fixture supplies a trusted parent LocalPool transport while reusing real upstream claims, run credentials, consumer and PostgreSQL fencing. It neither fabricates a Linux PID nor implements a general provider protocol. Its legacy default public-URL metadata is not a new public-URL conformance assertion; the deployed explicit-URL proof remains separate. Untrusted workflow admission stays disabled pending the negotiated runtime/admission boundary and independently verified provider physical termination.
