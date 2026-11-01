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
