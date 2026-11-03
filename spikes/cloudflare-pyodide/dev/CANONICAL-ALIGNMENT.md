# Closed guard and API alignment

The new guarded candidate runs locally under the cf CLI. An unauthenticated
POST with malformed JSON, a POST with a wrong synthetic key, and GET all return
403 before request parsing/execution. canonical-guard-v2.json records no OOM,
4.116-second complete probe lifecycle and stopped provider. No engine/SDK or
Cloudflare execution is claimed. The runtime used network none, 1 CPU/1 GiB/
128 PIDs, no credentials, expiry 0 and the existing closed config.

V1 failed because cf dev does not support --local. The failed receipt is
preserved. V2 uses the established cf dev command, retaining network isolation;
no credential or alternate access route resolved the failure.

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/ct128-canonical-guard-v2.py
node spikes/cloudflare-pyodide/dev/verify-canonical-guard.mjs
```

## Verified source/schema mismatch

Read-only Git and actual database metadata checks confirm:

* Existing isolated dev API source: 2037ac0d9ec95e94a77ce440001ae016d4215407.
* Canonical test source: 1d33150eb376b71f8a81b427920eda1b0447f829.
* Actual dev migration: 20261031_app_sdk_binding. Newer source includes four
  migrations through 20261101_table_name_unique.
* Dev execution_workers lacks active_handler_counts, capacity_scope,
  capacity_resource_uid and capacity_resource_deleted_at, which the canonical
  source maps. Worker claim/controller/recovery code also differs.

Selected SDK auth/runtime transport files are unchanged between those pins;
that does not establish whole-platform compatibility. The missing ORM columns
alone prevent assuming the current canonical host can use the old dev database.
inspect-dev-api-schema.py ran inside the existing dev API and performed only
SELECT metadata queries. No data, password, signing key or credentials were
exported. No migration, code upgrade or origin change was applied.

api-alignment.json preserves the exact source pins and missing columns. Next:
rehearse the normal migration chain on an isolated schema-only/synthetic database,
or prepare a matched canonical test API origin. Any live window must identify
which exact API/source/schema it uses; the existing Cloudflare Service Binding
name alone cannot prove that alignment. Existing dev volumes remain preserved.

## Disk/evidence preservation

The 8 GiB initial disk guard blocked the first probe before container creation.
It was not lowered. Three exact files from this thread's completed artifacts
were archived, decompressed checksums verified, then only their duplicate loose
copies removed. No volume, lock, source checkout, resource size or image changed.
archived-build-artifacts.json records archive/member hashes and paths:

* canonical-build-v1/compiled-bundle-v2.tar.gz contains the exact compiled JS and
  source map. Restore those two members into the original bundle directory only
  when the loose files are absent and archive/member checksums match.
* canonical-cancel-v3/project/backend/generated-runtime.mjs.gz preserves the
  exact 61d818a runtime copy from a stopped, completed fixture. Decompress into a
  new owned reproduction packet when needed; do not restart its old run.

The current candidate source/runtime input remains available. Existing receipt
paths and hashes remain meaningful through those archives. Free disk was 8194 MiB
after cleanup: still little margin above 8192 MiB. Recheck before another heavy
batch and preserve the existing guard. The probe and Ruff passed; no laptop
Docker or new cloud activity was used.
