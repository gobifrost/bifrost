# Explicit real-Azure qualification

These files are deliberately outside default pytest discovery. They access
real Azure and must use a disposable, explicitly authorized storage target with
synthetic data. They do not provision resources or grant access. See the
[cloud plan](../../../docs/contributions/azure-blob-cloud-plan.md) for resource
ownership, cost bounds and verified cleanup, and the
[readiness packet](../../../docs/contributions/azure-blob-readiness.md) for
source-bound commands, results and remaining limits.

Keep bootstrap/evidence in a separate container from the application test
container. The normal test harness resets application state. Never point it at
an existing deployment or customer storage.

## Dockerized contracts

From an isolated worktree, select the approved account/container explicitly:

```bash
export BIFROST_TEST_OBJECT_STORAGE_PROVIDER=azure_blob
export BIFROST_AZURE_BLOB_ACCOUNT_URL=https://YOUR-TEST-ACCOUNT.blob.core.windows.net
export BIFROST_AZURE_BLOB_CONTAINER=adapter-qualification
export BIFROST_AZURE_BLOB_AUTH=managed_identity
unset BIFROST_AZURE_BLOB_ACCOUNT_KEY
export BIFROST_AZURE_QUALIFICATION_ACCOUNT_URL=$BIFROST_AZURE_BLOB_ACCOUNT_URL
export BIFROST_AZURE_QUALIFICATION_CONTAINER=$BIFROST_AZURE_BLOB_CONTAINER
export BIFROST_TEST_S3_BUCKET= BIFROST_TEST_S3_ENDPOINT_URL=
export BIFROST_TEST_S3_ACCESS_KEY= BIFROST_TEST_S3_SECRET_KEY=
./test.sh stack up
./test.sh tests/azure_qualification/blob_contract.py -v --durations=0
```

Managed identity requires an Azure-hosted runner whose existing identity has
the approved Blob data/delegation permissions. It does not fall back to a
developer login. For explicit-key qualification, select `account_key` and inject
`BIFROST_AZURE_BLOB_ACCOUNT_KEY` through a private environment mechanism; never
place the value in source, command arguments or logs. Remove it after the lane.
Use `./test.sh`, not host pytest, for these contracts and platform worker tests.

## Azure-hosted diagnostic programs

Run these standalone programs in the candidate's Docker image on the approved
Azure runner. They are not pytest suites:

- `identity_probe.py`: `QUAL_ACCOUNT_URL` selects the positive account and
  `QUAL_SOURCE_SHA` records the checked-out commit. Uses the existing private
  `adapter-qualification` container. Verifies managed-identity reads/writes,
  stale writes, delegation GET/PUT, synchronous reads, a 300 MiB multiblock copy
  with streamed SHA-256 readback, and native mirror recovery.
- `identity_boundaries.py`: additionally requires `QUAL_DENIED_ACCOUNT_URL`, a
  task-owned account with no data grant to the runner identity. Verifies actual
  read/delegation refusal, seven-day and expired SAS access, and independent
  conditional completion. Uses `adapter-qualification` in the positive account.
- `ambient_identity_probe.py`: run as the intended untrusted/non-root container
  user with Blob configuration absent. Reports whether host metadata still
  issues a credential, without printing it. A positive result demonstrates a
  missing isolation boundary; it is not a successful security qualification.

The programs print case/status summaries, not tokens or SAS URLs. Preserve
failures, exact source, runtime configuration without secrets, JUnit/browser
artifacts and cleanup readback. Do not retry until green, add a fallback for
Azurite's unsupported native copy API, or infer cloud identity acceptance from
an emulator result.
