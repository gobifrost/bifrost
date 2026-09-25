# Engine SDK file mutation and signed URL service extraction

Owner/reviewer: Codex. Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`.
Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-files`.
Baseline: `fc3b44fb7` (reviewed file-read service extraction). This is a
preparatory stage for the complete engine SDK local transport. No main merge
until the full fixed-operation coverage is complete.

## Scope

Move business orchestration for `/api/files/write`, `/delete`, and
`/signed-url` from `api/src/routers/files.py` into `api/shared/sdk_files.py`
and have the HTTP handlers call those shared operations. Move the mutation
advisory-lock helper into `api/shared/file_access.py` if needed. Preserve
external HTTP behavior exactly. HTTP retains DTO decoding/encoding,
authentication dependencies, and `FileServiceError` to `HTTPException`
mapping. Do not add local transport yet; full token-equivalent file principal
work follows in the aggregate branch. Do not move browser `/complete-upload`.

Write/delete must retain effective scope, declared Solution location and
inbound gates, policy checks and denial audit, advisory lock, expected-version
and create-only conflict semantics, backend write/delete, cloud metadata
upsert/delete, commit before file-change publish, and intentionally absent
cloud metadata/publish in local mode. Signed GET must retain tier cascade,
per-tier policy, first existing permitted object preference, permitted
missing-object presign, and audited all-tier deny. Signed PUT must retain
scope/location/policy resolution and presign without metadata mutation.

Leave `/api/files/search` router-owned for this stage; it is admin-only and
uses a different global text-index path. Leave workspace list
`include_metadata=true` router-owned. Preserve the existing file-read
extraction and its tests. Add `stat` to SDK Solution query parity tests.

Allowed production files: `api/shared/sdk_files.py`,
`api/shared/file_access.py`, and `api/src/routers/files.py`. Tests under
`api/tests/unit/sdk/`, `api/tests/unit/routers/`, existing file unit and
E2E test paths. If another production file is needed, report blocker before
editing. Do not commit, push, merge, install packages, alter credentials or
configuration, or touch primary checkout. Preserve unrelated work.

## Verification

Add focused shared service tests for mutation success/conflicts, policy deny
audit, metadata/commit/publish order, local-mode side effects, signed GET
tier selection, and signed PUT policy/presign. Retain thin HTTP adapter tests.
Run `./test.sh tests/unit/sdk/test_sdk_files.py`,
`tests/unit/routers/test_files_mutation_commit.py`,
`tests/unit/routers/test_files_signed_url.py`,
`tests/unit/test_files_sdk_solution_scope.py`, and relevant file-policy E2E,
then `./test.sh quality api`. Report exact results and broader suites not run.

Progress log: `/tmp/bifrost-sdk-file-writes-opencode.jsonl`. Owner reviews
the diff and independently verifies before committing and cherry-picking
into the aggregate branch. Resume explicitly with `--session`.
