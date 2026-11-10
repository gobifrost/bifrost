# Explicit real-Azure qualification

These files are deliberately outside default pytest discovery. They access
real Azure and must use a disposable, explicitly authorized storage target with
synthetic data. They do not provision resources or grant access. See
[provider configuration and limits](../../../docs/architecture/object-storage-providers.md).
Record the exact approved account/container, source commit, ownership, cost and
lifetime bounds before running; retain source-bound results and cleanup receipts.

Keep evidence outside application fixture prefixes: the normal test harness
resets application state. Never put credentials in bootstrap objects or point
this lane at an existing deployment or customer storage.

## Local emulator and CI

The merge queue runs `tests/e2e/platform/test_object_storage.py` against
Azurite in a parallel job; the full application suite runs once with S3.
Azurite is not started for ordinary S3 tests. To run the focused lane locally:

```bash
BIFROST_TEST_OBJECT_STORAGE_PROVIDER=azure_blob \
BIFROST_TEST_AZURITE=1 BIFROST_TEST_AZURE_BLOB_RESET=1 \
BIFROST_AZURE_BLOB_ACCOUNT_URL=http://azurite:10000/bifrostcontract \
BIFROST_AZURE_BLOB_ACCOUNT_NAME=bifrostcontract \
BIFROST_AZURE_BLOB_CONTAINER=bifrost-test \
BIFROST_AZURE_BLOB_AUTH=account_key \
BIFROST_AZURE_BLOB_ACCOUNT_KEY=MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA= \
./test.sh tests/e2e/platform/test_object_storage.py -v --durations=0
```

This key belongs only to the public local emulator fixture. The test harness
creates that disposable container. Leave `BIFROST_TEST_AZURITE` unset for real
Azure: its account/container must already exist, and tests do not provision them.

## Dockerized contracts

`blob_contract.py` runs through `./test.sh` against an explicitly selected
target. From an isolated worktree, select the approved account/container:

```bash
export BIFROST_TEST_OBJECT_STORAGE_PROVIDER=azure_blob
export BIFROST_TEST_AZURE_BLOB_RESET=1
export BIFROST_AZURE_BLOB_ACCOUNT_URL=https://YOUR-TEST-ACCOUNT.blob.core.windows.net
export BIFROST_AZURE_BLOB_CONTAINER=adapter-qualification
export BIFROST_AZURE_BLOB_AUTH=managed_identity
unset BIFROST_AZURE_BLOB_ACCOUNT_KEY
export BIFROST_AZURE_QUALIFICATION_ACCOUNT_URL=$BIFROST_AZURE_BLOB_ACCOUNT_URL
export BIFROST_AZURE_QUALIFICATION_CONTAINER=$BIFROST_AZURE_BLOB_CONTAINER
./test.sh tests/azure_qualification/blob_contract.py -v --durations=0
```

The reset flag explicitly approves clearing every object in the configured
application test container, including private Git checkpoints. Use a disposable
container; keep evidence/bootstrap objects in a separate container. Blob tests
refuse to reset without this flag. A new Compose stack does not imply an empty
account.

The existing `tests/e2e/platform/test_object_storage.py` suite is
provider-neutral: with the provider above it exercises the full storage and
Git-checkpoint surface against the same approved container.

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
