# Independent engine-local streaming channel

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout. Read AGENTS.md and
`docs/plans/2026-09-24-engine-sdk-local-transport.md` first. The reviewer
will merge the latest aggregate branch after the agent-run dispatcher stage
finishes, before execution starts.

Add a dedicated async child/parent streaming pipe pair and a small generic
stream protocol in the execution process and pool. Leave the existing
request/response SDK pipe and independent synchronous import pipe intact.
The goal is for one long-running local stream to deliver chunks incrementally
while ordinary fixed SDK calls and synchronous imports remain usable. No AI
business logic or SDK facade change in this stage; a later stage will bind
`ai.stream` to this channel.

Each request is a named allowlisted operation with a correlation ID and
parent-derived principal; never arbitrary route dispatch. Parent results are
ordered event frames plus an explicit terminal frame or HTTP-style error.
Use bounded JSON frames (same 64 KiB invariant as existing transport),
chunking for large event payloads, pipe backpressure, a bounded deadline
covering setup and idle periods, and no pickle or unbounded queue. A stream
generator must yield each event as it arrives rather than accumulating the
whole response. The child must support early `aclose()` and task cancellation:
send a cancel control frame, the parent must stop/close its source, acknowledge
termination, then permit a later stream on the same child. If graceful
cancel/ack fails, fail closed and break only the stream channel; never switch
to HTTP. A provider error must map to one terminal error without silently
dropping already delivered events.

Use one active stream per child and explicit one-event credit: the child sends
`pull` from `__anext__`, and the parent advances the source at most once per
credit. A separate `cancel` frame must race a blocked source read. Keep the
stream descriptors and pump independent of the unary SDK and sync import
channels. The outer terminal frame must be distinct from a business `done`
event so the provider generator can finish its post-`done` work before the
stream channel closes. Preserve the ability to start a later stream after a
graceful cancel acknowledgement.

The parent stream pump must not keep a database session or connection open
while awaiting an external source, and it must close the generator on child
EOF, parent shutdown, protocol error, or cancellation. Size/order/version
validation applies to every request, event, chunk and cancel frame. The
existing SDK and import pumps must remain independent.

Keep production stream operation registration empty until the AI binding
stage. Inject a fake operation and async source only through the test pump
seam; do not ship a test-only allowlisted operation or duplicate AI logic.

Test wire bounds, out-of-order/malformed frames, EOF, idle timeout,
incremental delivery/backpressure, child early close and cancellation,
second stream after graceful cancellation, and an ordinary SDK call plus
synchronous import while a stream is active. Include a real fork/process-pool
path. Use focused `./test.sh` tests and `./test.sh quality api`; diagnose
failures rather than adding retries, skips or longer timeouts. This stage
must not add a new HTTP route, AI call, or fallback.

## Reviewer correction pass (2026-09-25)

The first implementation is in this worktree but has not passed review. Keep
its code; diagnose and repair these exposed failures before adding more tests:

- The verbose `test_sdk_stream_transport.py` run failed incremental delivery,
  task cancellation followed by a second stream, child EOF closure, and parent
  shutdown closure. Identify each cause and fix implementation defects. A
  task-cancelled `asyncio.to_thread(recv_bytes)` can leave a reader alive to
  steal the cancel ack; use cancellable `poll` before a short read instead.
  Do not mask failures by extending timeouts or retries.
- The oversized wire test deadlocked the event loop because `send_bytes` was
  called synchronously while the pump needed that loop. Send from a thread in
  the test. Keep the actual byte-bound assertion.
- A rejected `open` followed by the child's in-flight `pull` must leave the
  stream channel reusable. The first test exposed that and the draft added a
  rejected-id rule; run and verify it.
- `ai.stream` supports up to five 25 MB input files. The generic stream `open`
  currently exceeds its 64 KiB frame bound for a large request. Add bounded
  sequential chunking/reassembly for opening requests, with order/size/version
  validation and a test above 64 KiB. Preserve pipe backpressure. Avoid a
  test-only operation in the production registry.

Run the stream unit file with `./test.sh` (Docker), then the real-fork test
and focused existing pool/transport regressions. Run `./test.sh quality api`
with a background shell if OpenCode's 120-second command limit is too short;
the reviewer can also run it. Do not commit.
