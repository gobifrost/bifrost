# Current upstream execution and capacity contract

The branch merged upstream main025f6c019928d8be78bfe47f1cb62702b4ad2226 into merge commit1d33150eb376b71f8a81b427920eda1b0447f829. The merge was conflict-free. This brings the accepted external-capacity implementation from PR1172 and newer remote handler negotiation/recovery into the contribution; it does not patch the Containers owner's checkout or activate another provider.

## What upstream now provides

ExternalCapacityClient manages authenticated, idempotent physical worker resources under canonical PostgreSQL ownership. It reads /v1/capabilities, prepares a resource for an existing worker UUID, starts only the persisted resource UID and deletes that exact incarnation. Its source explicitly identifies the API as capacity provisioning, not job transport. There are no implicit retries/redirects after uncertain mutations. External policy checks fixed memory, maximum worker count and lifetime before admission.

The signed remote Hello now supports form=external and a resource_uid, while preserving legacy static/job wire identity. Current handler negotiation intersects advertised versions with supported workflow/agent/summary handlers. Existing route IDs, owner sessions, claims and terminal reporting remain the business execution contract. ThinRemoteProcessPool still requires a positive Linux PID in execution ACKs. External resource_uid is a worker-capacity incarnation, not an isolate execution handle, nor proof of per-run physical stop.

Therefore Pyodide cannot adopt this API by pretending an isolate is a Linux child, advertising a workflow handler before qualification, or returning an invented PID. The missing extension remains negotiated physical execution identity/inventory/stop semantics plus canonical pre-claim requirements and provider qualification. Provider physical-stop evidence is still absent. No new profile/handler was advertised, resource provisioned, capability granted or authoritative queue created by this reconciliation.

## Exact-source tests

On existing CT128, a fresh owned checkout upstream-025f6c0 and Compose project bifrost-test-5dc8d94e ran the merged source through repository ./test.sh:

```sh
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py \
  tests/unit/dev/test_thin_remote_pool.py \
  tests/unit/worker/remote/test_protocol.py \
  tests/unit/worker/remote/test_credential_protocol.py \
  tests/unit/services/worker_pool/test_external_capacity.py \
  tests/unit/services/worker_pool/test_external_policy.py \
  tests/unit/execution/test_result_transport_identity.py \
  tests/unit/engine/test_stream_capture.py \
  tests/unit/engine/test_engine_token_redaction.py -q
```

All129 cases passed in6.42s, with no skips/errors/failures. Supervisor elapsed60.465s includes stack lifecycle. Real PostgreSQL/Redis exercise claims, ownership, recovery and capacity admission; transport/provider effects in these unit fixtures use explicit doubles. This does not establish deployed external-provider or Pyodide dispatch acceptance. Ordinary CI remains required on the new merge head; the full local suite/client checks were not repeated for accepted upstream changes.

The first preflight injected mem_limit twice because the newer Compose scheduler already set it. Compose rejected it before containers were launched for that project; the raw failure is retained. The bounded workaround replaces existing resource keys within each selected service block and runs compose config --quiet before startup. The explicit --resume-preflight path verified the same pinned checkout and only the known Compose edit; it did not discard source, volumes or locks. Cached image reuse required byte-identical pyproject.toml/requirements.lock inputs. The successful run capped services to1CPU with fixed memory/no-swap limits; test-runner768MiB, API1GiB, database512MiB, smaller supporting services. Existing guest2CPU/8GiB allocation was unchanged. Owned test containers stopped; dev services and existing volumes were preserved. No laptop Docker or cloud action occurred.

## Runtime closure versus current source

The previously deployed artifact bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0 was audited against this merged checkout. Of79 packaged Python modules that correspond to tracked API files,73 are byte-identical after line-ending normalization. The six differences are the previously explicit empty namespace adapters for src, core, models, sdk, services and execution: each excludes server package initialization. There is no unexpected module drift. This is not a proof of those packages' full export surfaces, omitted third-party modules, every upstream module or arbitrary workspace imports.

```sh
node dev/audit-runtime-source.mjs dev/runtime/generated-runtime.mjs <new-output-json>
node backend/verify-upstream-reconciliation.mjs
```

Raw JUnit, source audit and receipt are in results/upstream-reconcile. Live dev API remains on2037ac0 and its runtime/deployment receipts retain their original pins. Merging or source-equivalence checks do not redeploy the API or establish end-to-end acceptance against this newer platform revision. Untrusted admission remains disabled; canonical Cloudflare dispatch and physical CPU termination remain incomplete.
