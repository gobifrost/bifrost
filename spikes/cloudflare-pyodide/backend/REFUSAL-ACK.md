# Explicit private no-send acknowledgement

The companion can now retire a known pre-launch refusal without inventing a
workflow result. `PrivateHttpWorkflowPool.acknowledge_refusal(execution_id,
accept)` is a trusted-host proposal, not an existing upstream/public API.
`accept(execution_id)` must explicitly return `True` after canonical
failure/recovery consumption. It must not execute or re-dispatch the workflow.
Normal return with `None`, False, exceptions and ownership loss retain evidence.

Only the physical handoff's ownership fence can mark a no-send refusal. The
adapter additionally requires zero launches, no invocation, no deadline task,
no mux collection and no native execution handle. Missing ACKs, transport
errors, request cancellation and an empty observation are not no-send proof.
Pending/cancelled/successful journal tasks cannot use this acknowledgement.

The journal serializes ACKs per route, checks the physical worker/launcher and
current database session before and after consumption, and preserves the original
failed task. Duplicate ACKs do not call the consumer again. The mux records a
no-start tombstone, refuses subsequent dispatch of it, and clears it only after
the acknowledged journal/whole adapter retires. No engine result is fabricated;
the failed route and delivery receipts continue raising their original error.

This does not autonomously decide business-job status, release a canonical claim,
or implement recovery after parent process loss. The upstream owner must provide
the acknowledgement after its own transactional consumption. Automatic public
host wiring, portable startup and untrusted admission remain absent/disabled.

## Evidence

CT128 .5CPU/1GiB/64PID network-disabled native checks:45 tests pass, including
explicit/None/False/lost ACK, pre/post-callback ownership, same-launcher reconnect,
duplicate refusal, no rerun, started/completed/uncertain invocation rejection,
normal delivery and peer progress. Ruff/Pyright pass. No deployed claim follows.

Two targeted `./test.sh` PostgreSQL cases use the actual canonical consumer and
repository with the b992ba364 generic setup guard. Persistence and remote session
ownership are real; physical launch, credential issuance and ACK loss are
synthetic. Both current-failure and intentional stale-discard cases verify normal
canonical consumption once, lost ACK retention, real old-generation rejection,
new-generation ACK, duplicate ACK, original failed receipt and cleanup. No direct
business-row mutation substitutes for the consumer's terminal update.

Actual `ct128-canonical-failure.py --sdk-refresh --revision 5` separately passes
real engine/SDK config/table/refresh/redaction, duplicate/fencing/reconciliation,
revocation, accounting[2,2,1,0] and cleanup in76.987s (whole harness lifecycle).
Its native API remains1d33150, packaged enginef49d4ba, SDK139596497; the canonical
setup fix is qualified separately by the PostgreSQL overlay, not silently claimed
deployed in that API. Both owned test projects stopped; volumes/locks preserved.

Raw scope-separated receipts, JUnit, hashes and quality are in
`../results/refusal-ack/`. Run `node backend/verify-refusal-ack.mjs`.
Reproduce native checks with `python -m unittest -v test_private_http_pool
test_workflow_request_mux test_attempt_journal_owners test_attempt_delivery` in the
pinned Docker fixture. PostgreSQL selector:
`./test.sh tests/unit/dev/test_pyodide_attempt_journal.py::test_setup_refusal_notifies_only_a_claim_fenced_durable_failure -v`.

No Cloudflare invocation/deployment, spending, grants, runtime/security changes or
resource expansion. Physical stop/isolation/memory enforcement remain unproved.
