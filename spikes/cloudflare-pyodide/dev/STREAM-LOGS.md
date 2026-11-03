# Deployed upstream printed-log persistence

Worker version512022a3-6b85-4878-82f6-1fcb7848e97c executed the actual upstream engine stream-capture revision f49d4ba99 against the existing isolated CT128 dev API. The workflow used real run-scoped access/refresh credentials, loaded hash-pinned workspace code and a helper, read config42, upserted table document70, and printed its access token to stdout and stderr. Both printed values were redacted before delivery and in the returned result. No SDK operation was mocked.

After the fixture marked its own run terminal, the existing upstream recover_terminal_execution_logs operation drained three rows into PostgreSQL: the structured warning, INFO stdout and ERROR stderr. Independent ORM reads found each printed message exactly once, redacted. A repeat recovery flushed0; the Redis stream was empty. The fixture then verified ended access/refresh401 and removed only its own logs, audits, workspace and entities. This is real credentialed output persistence, but the fixture's direct terminal-state input is not canonical terminal reporting or an executor adapter.

The source engine fix is shared with Linux. Runtime staging verifies the original artifact SHA e1cca587e10cb755d49df0f2325c038a361a25f7031142a9e9e896a3c004bd39 and original engine SHA before replacing only the companion bootstrap and qualified upstream engine module. New artifact SHA is bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0; engine SHA6690efa6fc513a0f457c30cfed59da091bf3ae292d186bc72cd436c871759c21. All453 other modules/native-wheel artifacts are byte-identical. No missing-wheel compilation or replacement SDK was needed for this pure Python repair.

## Evidence and commands

```sh
node dev/stage_runtime.mjs <original-pinned-base-artifact>
node dev/verify-runtime-overlay.mjs <original-pinned-base-artifact> dev/runtime/generated-runtime.mjs
node --test dev/stage_runtime.test.mjs dev/executor/policy.test.mjs
# On CT128 with the staged fixed packet and existing cache:
python3 ct128-runtime-closure.py
# cf CLI deploy from dev/executor with a new bounded private profile:
cf deploy --secrets-file <private-guard-json>
# CT128, real API fixture:
python3 run_cloud_qualification.py --streams --private-directory cloud-private-stream-v1
node dev/verify-stream-cloud.mjs
```

The local cf CLI/actual-workerd closure check ran two fresh upstream-worker context controls with zero SDK calls. They pass the pinned Python3.14.2/Pyodide314.0.6/cpython-314-wasm32-emscripten guard. Network-disabled process cap1CPU/2GiB/no swap/256PIDs; elapsed16.313s includes CLI/build/startup, not workload latency. It stopped without OOM. The actual cloud control container used1CPU/768MiB/no swap/128PIDs/180s inside CT128's existing2CPU/8GiB allocation. Five Node routing/staging regressions and guest focused Ruff pass. Exact source-overlay hashes, runtime provenance and raw local/cloud receipts are in results/stream-cloud.

The five-request/$0.10/30-minute window used three reserved fixture POSTs plus one cleanup GET. Negative controls returned unsupported canonical-mode422 and invalid runtime credential401 before Python execution; config peer-tenant access returned403. The successful workflow's441ms is upstream workflow wall time, not provider CPU/startup or a cost invoice. Peak/CPU/billed cost remain unknown. Integration and fresh-state probes were deliberately not combined into this print fixture; their earlier receipts retain their original pins. Subrequest/CPU limits stayed unchanged.

Cleanup used cf CLI to deploy workersDev=false/expiry0 and delete DEV_AUTH. Final closed version5f6826d4-4fcb-4698-b1e8-1a4a1d3f574f, deployment200bfbcd-c0aa-49b8-8e4d-2b0355234ff4, has no secrets; the retired-guard GET returned404. The qualification container was removed; dev services and volumes remain. The retired profile/ledger must not be reused. No production resource, permanent credential or new queue was introduced.

Physical CPU termination and combined signed canonical cloud dispatch remain incomplete; arbitrary untrusted admission stays disabled. The historical canonical failure fixture is not rewritten or declared passed by this successful cloud stdout run. A new failure-path canonical run still needs its own engine/artifact pin and authorization/result evidence.
