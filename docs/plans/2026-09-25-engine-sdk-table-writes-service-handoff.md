# Engine SDK table document writes: shared service extraction

This is a bounded stage of the full engine-local Python SDK project. Work in
`codex/sdk-engine-table-writes`, based on reviewed table-metadata commit
`914525164`. Codex reviews; OpenCode executes. Do not commit, push, merge,
install, change configuration/credentials, or edit unrelated files. Other
contributors are editing the import dispatcher and knowledge CLI routes in
separate worktrees; this stage must not touch those surfaces.

## Goal and allowed files

Extract all HTTP table-document mutation orchestration from
`api/src/routers/tables.py` into new `api/shared/table_document_writes.py`.
The existing HTTP handlers and future engine-parent local dispatcher must
call the same service. Keep HTTP handlers thin and preserve every status,
response, transaction, policy, and publication behavior. This is **service
extraction only**: do not edit `api/bifrost/tables.py`,
`api/bifrost/_local_transport.py`, or `sdk_local_dispatch.py` yet.

Allowed production files: new `api/shared/table_document_writes.py`, existing
`api/src/routers/tables.py`, and existing `api/shared/table_documents.py` or
`api/shared/table_batch_writes.py` only if a helper must be shared rather
than duplicated. Allowed tests: new focused unit test file under
`api/tests/unit/`, existing table route tests whose patch seams change, and
one focused table E2E if needed. No DTO/migration/dependency changes.

## Exact mutation surface

`insert_document` at router line ~648; `upsert_document` ~717;
`update_document` ~829; `delete_document` ~881; `batch_documents` ~937;
`batch_delete_documents` ~1028. The Python SDK maps to those routes as:

- `insert` -> single insert; legacy route `DocumentCreate.upsert` branch must
  remain supported even though SDK insert does not set it.
- `upsert` -> single replace upsert (data is replaced on conflict).
- `update` -> partial merge; `delete_document` -> single delete.
- `insert_batch` -> batch insert; `upsert_batch` -> batch **merge** upsert.
- `bulk_upsert` -> batch **replace** upsert; its SDK-only bounded 409 retry
  stays in the facade in this stage.
- `delete_batch` -> batch delete, missing IDs skipped and returned IDs ordered
  by input. Its SDK facade maps table 404 to empty result.

Existing seams: `shared/table_documents.py` owns `DocumentRepository`,
single-document read helpers, generic policy gates, row shaping, and some
commit behavior. `shared/table_batch_writes.py` owns normalized batch rows,
duplicate detection, locking/preflight, policy gates, SQL writes, and
conflict classification. `shared/table_resolution.py:get_table_or_404` already
owns table resolution.

The new service owns attribution resolution and privilege validation,
pre/post-image policy checks, repository calls, batch policy loading and
preresolution, mapping `DuplicateBatchIds`/`BatchPolicyDenied`/
`ConcurrentBatchWrite` to transport-neutral errors (preserve 422/403/409
details), commits, and publication. Return existing DTO shapes or plain data
that thin handlers can construct. Do not import routers in shared code.
HTTP handlers retain URL/DTO parsing, `get_table_or_404`, Solution write-target
gate, and batch explicit-scope exact-table gate. Those are entry/resolution
gates; do not duplicate them in the new mutation service. Describe the
parent-local preflight needed for them in your final report.

## Behavior to pin

- Privileged attribution overrides are allowed only to the engine sentinel
  or a superuser; ordinary calls use caller defaults. Insert establishes
  `updated_by` as today; batch does this per row.
- Single update and legacy insert-upsert check the `update` policy against
  both old and merged post-image. Direct upsert checks `create` candidate and
  `update` against old and replaced post-image on conflict. Missing single
  document returns 404. Policy denial remains 403 with the existing deny
  audit behavior.
- Batch duplicate IDs: 422 with `duplicate_ids`. Batch policy denial is
  atomic: 403 with `denied_row_indices`; do not turn it into partial success
  or add per-row audit behavior. Insert conflicts are reported in `errors`
  while successful documents preserve submission order and optional
  `return_documents`. Concurrent batch insert conflict: rollback and 409
  with the existing retry message.
- Single writes commit before one `publish_document_change` with the exact
  `insert`/`update`/`delete` action and old/new flattened rows. Batches
  commit before one `publish_table_invalidated`, and publish only when
  state changed. No publication on failed transaction.
- Batch delete skips absent IDs, denies the whole batch if any existing row
  fails policy, commits once, and returns deleted IDs in input order.
- Keep DTO max-1000 validation, table 404, Solution scope gates, and SDK
  auto-create/409 retry behavior unchanged at their existing boundaries.

## Verification

Write focused unit tests for shared-service behavior and thin HTTP adapter
parity, including one single and one batch publication/commit ordering case,
replace-versus-merge, privilege attribution, atomic denial, duplicate IDs,
and batch delete ordering. Run `./test.sh` from this worktree (boot its stack
once) for those tests plus existing focused table router and SDK tests, one
targeted live table E2E for the changed mutation boundary, and
`./test.sh quality api`. Report exact commands/results and broader suites not
run. Diagnose and repair any failure; no retry/timeout/skip/xfail masking.
Stop and report if another production file is required.
