# Canonical pre-claim eligibility boundary

The current source cannot safely advertise the Pyodide companion as a general
`workflow.execute` worker. The real claim query selects by handler/version,
worker handler advertisement and memory; neither `WorkDispatch` nor
`ExecutionWorker` records workflow-specific runtime requirements. A reserved
dispatch bypasses the free-memory comparison, but not the handler check.

Source authority is the checked-out upstream implementation:
`api/src/services/worker_pool/claims.py:claim_next`,
`api/src/services/execution/workflow_dispatch.py:stage_workflow_run`, and the
`WorkDispatch`/`ExecutionWorker` models. The architecture's “one engine, two
backends” currently describes PlatformJobs; it is not an existing workflow
provider-selection API. Workflow ownership remains its PostgreSQL claim path.

## Enforceable today

An unqualified provider must advertise no workflow handler. The new PostgreSQL
regression cases in `test_pyodide_attempt_journal.py` exercise empty and
agent-only handler advertisements, each with and without a reserved dispatch.
They require the workflow to remain PENDING, unassigned, without a claim,
start timestamp or credential version; its original payload and queue position
remain intact. A separately registered eligible Linux-control executor then
claims that exact dispatch through the ordinary upstream transaction. There is
no provider fallback call, second business queue or copied job.

Verification: `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py -k unqualified_provider -v`
passed all four selected cases (21 other cases deselected) in 5.16 seconds.
`results/preclaim-eligibility.xml` preserves the JUnit receipt. Focused Docker
Ruff passed. No provider runtime was invoked and no production source or schema
was changed. The full suite/type check was not rerun locally for this test-only
increment; normal CI remains required.

This is an existing fail-closed mechanism, not a new backend implementation or
proof that Python is safe. The companion's credentialed local tests deliberately
advertise the workflow handler inside the isolated test stack to qualify a fixed
trusted fixture. That exception must not become production registration.

## Smallest required extension (proposal, not an implemented API)

Persist an authority-generated execution requirement with the durable dispatch,
bound to the immutable workspace, entrypoint and dependency artifact. It must
express runtime/ABI compatibility, required OS/network capabilities, trusted
deadline and memory requirements, and the execution isolation/termination
contract. A workflow's own request flag or import scan is not a trust decision.

Persist the provider's versioned qualification separately from its run principal.
Compare requirement and qualification in the canonical claim transaction, before
assignment, RUNNING or credential issuance. Preserve this comparison for reserved
work, head-of-line blocking and capacity accounting; incompatible work must not
block otherwise eligible work. Reconnect must retain the same requirement,
qualification and attempt fence. A dependency/workspace change invalidates prior
qualification, and uncertain effects must never trigger automatic redispatch.

Existing Linux dispatches must keep their existing Linux semantics during any
reviewed rollout. Do not reinterpret absent requirements as Pyodide-compatible,
and do not overload handler versions to encode trust. Migrations, planner,
claim query, capacity controller, diagnostics and recovery need one coordinated
change; a companion-only predicate after claim cannot satisfy that contract.

This contribution does not alter those shared files or create a migration.
It records the exact missing boundary for the Containers owner and maintainer
review. The six physical operations remain a proposal, not upstream protocol.

## Still blocking Pyodide qualification

Fresh local state canaries and genuine scoped SDK execution pass for fixed warm
fixtures. Cold concurrent Python startup fails even without BiFrost. More
fundamentally, CPU cancellation permits later effects, including after catching
the provider CPU exception. Until physical termination and deployed run-scoped
acceptance are proved, arbitrary untrusted workflows cannot be admitted. Runtime
memory/capacity remains unqualified; the fixture's synthetic 1 GiB scheduling
budget must never be treated as the 128 MiB isolate capacity.

The next implementation must extend canonical placement in coordination with the
shared execution owners, or obtain a provider runtime primitive that satisfies
the missing guarantee. Adding more successful execute-code fixtures does not
close this gap.
