# Historical canonical fixture findings

Reviewed against source head `3942b2358d650e8d495706074e6ae2d73659a8ac`.
This is a source disposition, not an alert dismissal or fresh CI result.

* [1836 / side-effecting assert](https://github.com/gobifrost/bifrost/pull/1161#discussion_r4433865690):
  historical `assert await storage.write(...) == manifest[name]` could omit the
  write under Python optimization. Commit `4c2f09017` already moved the awaited
  write into `written_digest` before asserting its value. Current source retains
  that correction; no additional change is needed for this reported statement.
* [1834 / statement has no effect](https://github.com/gobifrost/bifrost/pull/1161#discussion_r4433865696):
  current `canonical-local.py:525` awaits the cancelled heartbeat task inside
  `suppress(asyncio.CancelledError)`. Awaiting the task joins its cancellation;
  the discarded return value does not make the wait ineffective. Retain it.
* [1835 / statement has no effect](https://github.com/gobifrost/bifrost/pull/1161#discussion_r4433865699):
  current `canonical-local.py:550` performs the same join in outer cleanup when
  the task still exists and is unfinished. Retain it for exceptional exits.

The two waits are source-reviewed false positives. No suppression, altered
security control, or removal of cleanup is proposed. No Docker or cloud test
was run for this documentation-only disposition. REST comment reads received
HTTP 403 rate-limit errors; bounded GraphQL review-thread reads supplied the
exact original/current line mapping. The separate failed CodeQL check is not
established as resolved by this review.

Filesystem receipt race follow-up (1837-1840): canonical-isolation.mjs and
cold-concurrency.mjs now reserve their output with exclusive open before any
fixture effect. Updates use that same open file handle, avoiding path-based
check/use and reopen races. A failed observer leaves a reserved, explicitly
unverified receipt; no automatic retry removes it. New source manifests include
the shared helper. Historical receipts remain unchanged.

Verification: node --test backend/exclusive-receipt.test.mjs (2 passed),
node --check for both callers, git diff --check. No Docker/cloud invocation.
This is local helper coverage, not fresh CodeQL or execution acceptance.

Finding 1841 in interop-benchmark.mjs used the same check-before-invocation
pattern. It now uses the exclusive receipt helper before the benchmark effect,
and includes that helper in its source manifest. Verification: the two shared
helper regression tests and caller syntax check pass. The benchmark was not
invoked; existing interop measurements remain historical and unchanged.

Findings 1842 and 1843: guarded-runtime-probe.mjs and runtime-probe.mjs now
reserve their receipts with the same exclusive helper before any invocation.
Both include the helper in new source manifests. Shared helper tests (2) and
both caller syntax checks pass; no probe was invoked or old receipt rewritten.

Findings 1844 and 1845: canonical-refresh.mjs now reserves its receipt before
even the Docker health inspection, and writes both invoking/observed snapshots
through the retained file handle. The helper is included in new source hashes.
The two shared helper tests, caller syntax check and diff check pass. No local
stack, refresh fixture, control authentication or cloud execution was invoked.

Refresh-test follow-up: use only the pyodide_refresh module import, qualifying
all bridge references through it. The reported no-effect statement is
`await waiter` inside `assertRaises(asyncio.CancelledError)`: this explicitly
checks cancellation propagation and must remain. It is a source-reviewed false
positive, not a dismissed alert. Python tests were not rerun because the owned
Docker stack is stopped pending remote capacity; diff checks cover this edit,
and functional verification remains deferred.

Finding 1848, api/tests/unit/dev/test_pyodide_attempt_journal.py:116, flags
`await waiter` inside `pytest.raises(asyncio.CancelledError)`. The cancelled
waiter awaits the shielded journal receipt; joining it verifies that cancellation
reaches the waiter while the subsequent assertions verify the underlying route
remains pending and can complete. Removing the await would stop observing the
exception under test. Retain it as a source-reviewed false positive without
dismissing the alert or changing controls. This documentation change has only
diff-check verification; the Docker test remains deferred to remote capacity.

Historical 6194730 findings 1849-1852 were mapped from review comment original
lines before disposition:

* 1849, canonical-local.py original 429: `await waiter` in the already-done
  branch propagates the original provider failure before raising the checkpoint
  failure. Current code retains this exception observation.
* 1850, original 434: `await waiter` joins the cancelled observer before checking
  cancellation and the journal's still-pending collection state. Retain it.
* 1851, original 443: `await sent` joins the cancelled Event.wait task in cleanup.
  Retain it; cancellation request alone does not join the task.
* 1852, provider-response-loss.py original 91: `await observed` waits for the
  response-loss observation checkpoint before database/credential assertions.
  Current code also explicitly awaits the journal receipt and verifies the
  expected RemoteProtocolError; neither wait is an unused-value defect.

These are source-reviewed false positives, with no alert dismissal or runtime
rewrite. Verification for this documentation-only follow-up is git diff --check;
no fixture replay, Docker startup or cloud call was performed.

Findings 1857 and 1858 map to the previously reviewed cancelled-waiter joins
(canonical-local.py:454 and test_pyodide_attempt_journal.py:116). Finding 1859
maps to test_pyodide_attempt_journal.py:142: `await receipt` inside
`pytest.raises(error)`, parameterized for ConnectionError and CancelledError.
It observes the failed collection before checking sanitized inventory state;
removing it would omit the exception assertion. Retain all three as
source-reviewed false positives, without dismissal or suppression. Only the
documentation changed; git diff --check passed, with no runtime test replay.
