# Lost response after successful effects

The current companion HTTP collector does **not** reproduce upstream remote
result recovery when the provider connection loses its response. This is a
tested backend gap, not an upstream consumer bug or a successful recovery gate.

`provider-response-loss.py` runs the existing canonical fixture with two scoped,
test-process-only patches: route its single provider POST through an ephemeral
loopback TCP listener, and inspect the actual consumer outcome. The listener
forwards the bounded request to the real local Python Dynamic Worker. It waits
for the real success response, independently reads the SDK-written document,
and closes the caller socket without sending HTTP response bytes. No credential
or raw request/response is retained. The listener does not resubmit, report the
buffered success to the journal, or become a recovery service.

Actual observation (`canonical-refresh-response-loss-observed.json`):

* Upstream engine, helper import, config read and table SDK operation completed;
  independent document value was 70, with one successful upsert and one launch.
* The injector discarded 2,439 response-body bytes and closed the caller socket.
* Collection raised actual `httpx.RemoteProtocolError`.
* The unchanged canonical consumer persisted **Failed**, no success result, and
  `Server disconnected without sending a response.` as its error message.
* The original runtime access credential then returned 401.
* The success fixture correctly refused the missing result. Fixture worker and
  claim-cascade cleanup passed; there was no workflow replay.

`process_exit_code: 0` means these negative observations matched their assertions.
`recovery_acceptance: false` is explicit. It is not successful backend recovery,
exactly-once external effects, physical termination or deployed acceptance.

## Why the boundary fails

Current upstream `WorkflowExecutionConsumer.process_message` awaits
`route_execution`, expecting routing/setup to complete and results to arrive
asynchronously through its callback. Its exception handler explicitly covers
setup failure. The local companion fixture instead awaits HTTP result collection
inside that routing call. A late transport failure therefore enters the setup
failure path after actual workflow effects. Shielding a separate caller waiter
cannot recover bytes lost from the collector's own connection.

The smallest next implementation must separate accepted physical routing from
result collection, preserving upstream asynchronous callback/ACK semantics.
Collection needs an existing physical identity and retained terminal receipt or
inventory that can reconnect without rerunning code. A local failed task cannot
reconstruct the missing result. PostgreSQL remains authoritative for attempt
ownership, terminal acceptance and interrupted-attempt recovery; a provider must
not automatically replay an attempt with uncertain effects. Merely returning
early from routing would avoid this setup-error classification but would not
provide missing-result recovery or physical stop, so it is insufficient alone.

No shared consumer, remote pool, controller, Linux or Containers source was
changed. This test does not allege that the upstream Linux remote bridge has
the fixture's synchronous-collection behavior.

## Reproduction and retained failure

With the own isolated test API healthy and the local Wrangler supervisor running,
from the spike directory:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-response-loss-observed response-loss concurrent-runtime-manifest.json
node backend/verify-response-loss.mjs
node backend/verify-transport-waiter.mjs --commit 832ed9d0ddcaee4d28accb6392f4e2ce4eec675c
```

The invocation refuses an existing receipt before effects. Use a separate exact
fixture identity/receipt for any new experiment; never overwrite evidence. The
runtime payload was verified against the existing pin 21b82a20... before use.
Offline verifiers check actual observations, exact source hashes and the earlier
waiter-detachment evidence at its immutable source commit. Docker Ruff and JS
syntax/diff checks pass. No API production source changed or broader suite ran.

The first observer (`canonical-refresh-response-loss.json`) failed with
AttributeError because it assumed `Execution.error_type` was persisted. Current
upstream documents that parameter as logging-only; the ORM stores error_message.
Its exact source is retained as `response-loss-initial-driver.txt`, hash-checked
by the verifier. The corrected observer captures the real transport exception
and reads the actual persisted fields. The failed process was terminal, with no
remaining fixture worker rows, before the new fixed-fixture invocation. This
was an observer correction, not retry-until-green or replay of that business run.

`response-loss-local-cleanup.json` records SIGINT of the exact own Wrangler
supervisor, independent port-8799 connection refusal, bridge-only tools network
and restored stopped API. The ephemeral proxy closes in finally. No cloud call,
endpoint reopening in Cloudflare, new credential grant, spending or production
change occurred. Physical CPU termination and stdout qualification remain open.
