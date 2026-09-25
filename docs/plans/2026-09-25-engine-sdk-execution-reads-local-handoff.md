# Workflow and execution read SDK local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree; no commit,
push, main merge, or primary checkout edit. Read AGENTS.md and the aggregate
transport plan first. The reviewer will merge the execution-reads shared
service branch into this worktree before execution starts.

Migrate `workflows.list`, `executions.list`, and `executions.get` to named
parent-local operations. `workflows.get` already delegates to
`executions.get`. Both HTTP routes and local dispatcher must use
`api/shared/sdk_execution_reads.py`; do not copy authorization, pagination,
scope, pending execution enrichment, or serialization rules into the
dispatcher. Preserve external SDK HTTP. Keep all public models, list
continuation tokens, the SDK's 404 `ValueError` and 403 `PermissionError`,
workflow metadata, and observed ordering. Validate filter/query values with
the same route DTOs. Derive actor/org/Solution scope in the parent only; ignore
child claims that could grant access.

Test HTTP/local parity for each operation with real records, filters and
pagination, pending detail, hidden cross-org 404/403 outcomes, and external
HTTP. Include one real engine child with fixed-operation HTTP disabled and
concurrent read calls. Run focused `./test.sh` tests and `./test.sh quality
api`; diagnose failures without retries, skips, or timeout inflation.
