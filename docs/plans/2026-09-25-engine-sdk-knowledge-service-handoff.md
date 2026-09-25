# Engine SDK knowledge shared service extraction

This is one bounded stage of the full engine-local SDK project. Work only in
`codex/sdk-engine-knowledge`; the parent branch is the reviewed table-metadata
service extraction. Codex owns final review. Do not commit, push, merge, edit
configuration or credentials, or change unrelated files. The aggregate branch
is concurrently adding a local import channel; leave local transport and
dispatch for the next stage.

## Goal

Extract all seven `/api/sdk/knowledge/*` operations in
`api/src/routers/cli.py` into a transport-neutral application service in
`api/shared/sdk_knowledge.py`. HTTP handlers and the future worker-parent
dispatcher must invoke the same service, with no second implementation of
repository, embedding, commit, or response-shaping rules. Keep authentication
and scope resolution at the transport boundary. Keep the HTTP routes and
external SDK behavior exactly the same.

Allowed production files: new `api/shared/sdk_knowledge.py`, existing
`api/src/routers/cli.py`. Allowed tests: new
`api/tests/unit/sdk/test_sdk_knowledge.py` and existing focused knowledge
unit/E2E test files only if their mocks need updating. No DTO, dependency,
migration, SDK facade, or dispatcher edits in this stage.

## Existing behavior to preserve

- `_deny_external_knowledge()` executes before scope lookup, repository work,
  or embedding for every operation. Direct external users get 403 first.
- `_resolve_sdk_org_id()` is the HTTP scope edge. It already uses the shared
  authoritative scope resolver. Do not rewrite its semantics here.
- `store`: create one embedder/repository, chunk/embed/upsert, commit, return
  the first physical chunk ID as `{"id": ...}`.
- `store_many`: create one embedder and one repository for all documents,
  sequentially chunk/embed/upsert, then one final commit. Return one first
  chunk ID per document. A failure before that commit rolls back all prior
  flushed rows. Preserve the current generic failure for a document missing
  `content`; no new DTO validation in this stage.
- `search`: embed the query once, use hybrid repository search, return the
  existing document shape, no commit.
- `delete`: exact-scope keyed delete, commit, `{"deleted": bool}`.
- `delete_namespace`: exact-scope namespace delete, commit,
  `{"deleted_count": int}`.
- `list_namespaces`: exact org plus optional global listing, no commit.
- `get`: exact-scope keyed lookup and full-content reassembly; HTTP 404 on
  repository miss, which the Python facade later maps to `None`.

The repository is `api/src/repositories/knowledge.py`; embedding factory is
`api/src/services/embeddings/factory.py`. Transport-neutral service functions
should take an already resolved org UUID and the existing request DTO or
explicit fields, return JSON-serializable data, and own all four current
commits. Keep any 404/status mapping centralized so the future dispatcher can
send the same status in its response frame. HTTP handlers should become thin
adapters that retain external denial, scope resolution, and DTO construction.
Do not import routers into the shared service.

## Verification

Add focused service tests for one embedder/repository across `store_many`,
commit ordering and failure rollback, exact-scope get/delete, 404 mapping,
search embedding once, response shape, and external denial before any service
call. Adapt existing route tests only where a moved dependency patch target
changes. Run tests using `./test.sh` from this worktree (boot its stack once),
targeted existing knowledge E2E, and `./test.sh quality api`. Do not run broad
suites or mask failures with retries, skips, xfail, or larger timeouts. Report
every failure with a durable disposition, exact commands/results, and broader
suites not run. Stop and report if a different production file is required.
