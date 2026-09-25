# Engine-local table operations

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not commit,
push, merge, or touch the primary checkout.

Goal: complete the fixed Python SDK `tables` facade over the existing
child-parent local transport, using the shared services already in this branch:
`api/shared/sdk_table_metadata.py`, `table_document_writes.py`, and the
existing table document read service. The HTTP handlers already use them.
External SDK callers must continue over HTTP with identical public results.
The child must send no HTTP request for these fixed table operations and must
never open a database connection. Add only explicit allowlisted operations.

Cover `create`, `list`, `delete`, `insert`, `upsert`, `get`, `update`,
`delete_document`, `insert_batch`, `upsert_batch`, `bulk_upsert`,
`delete_batch`, `query`, and `count`. The last three are already partly local;
preserve them. Keep SDK composite behavior: missing table creation outside a
Solution, `bulk_upsert` 409 retry logic, filtered count through query, and
exact wire shapes/errors. Parent reconstructs actor, org, Solution and source
context; never trust scope claims in child frames. Reuse HTTP DTO validation
and shared service authorization, transaction, attribution, policy and event
behavior. Preserve request/response bounded chunking for large batches.
For no-actor or invalid context fail closed. A failed local write never retries
via HTTP.

Write focused transport, dispatch, HTTP/local parity, scope and live-worker
tests. A live workflow test must disable fixed-operation HTTP requests in the
child and verify committed state from an external HTTP client, including at
least one metadata mutation, single document write and batch write. Exercise
service-child identity if relevant. Run focused `./test.sh` tests and
`./test.sh quality api`, relevant DTO/contract tripwires. Report exact
commands/results and any failure; no retry, skip, or xfail masking.

Do not edit import transport/process pool/template process; this stage will
be rebased into an aggregate branch with a separate import-channel change.
