# In-flight local provider collection

Continued from ea35a2ec23f5b17aa6cc56d2aa8322df72c7bd5e. Its PostgreSQL
regression fixed a raw-task cancellation bug in the companion transport journal.
This increment qualifies the same shielded receipt with a real local Python
Dynamic Worker, upstream engine, workspace helper and authorized SDK effects.

The fixed fixture observes HTTPX's `http11.send_request_body.complete` trace
event on the provider request, retaining no headers, body or trace metadata.
While the result is still pending it cancels a separate receipt waiter and
requires `CancelledError`; the journal must still report nonterminal collection.
The independent collector retains its HTTP connection and awaits the result.
This is caller detachment, **not a deliberately dropped socket**, supervisor
loss, whole-host recovery, workflow cancellation or physical isolate stop.

The actual run passed on the first fixture invocation:

* Real canonical claim and protocol-2 consumer-issued run identity.
* Actual pinned upstream engine/SDK and virtual workspace helper import.
* Config read and table creation/upsert; independent document readback value 70.
* One physical launch despite duplicate dispatch and cancelled waiter.
* Canonical terminal persistence, deliberately lost callback ACK, owner rotation
  and replay without another launch or another persisted log row.
* Other-tenant access 403; original access credential 401 after completion.
* Existing completed-claim retention followed by fixture worker FK-cascade cleanup.

The SDK's first upsert receives 404 and it creates the fixture table before its
successful upsert. There is one successful upsert, not a claim of one HTTP write
request or arbitrary exactly-once external effects. This mode does not probe
refresh-token revocation; the separate refresh fixtures remain its evidence.

## Reproduction and evidence

Use the isolated setup in CANONICAL-LOCAL.md, the existing cached generated
runtime, and a healthy own test API. No cloud deployment or public endpoint is
part of these commands. From the spike directory:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-transport-waiter transport concurrent-runtime-manifest.json
node backend/verify-transport-waiter.mjs
node --test probe-io.test.mjs backend/fixture-admission.test.mjs backend/test-api-route.test.mjs
```

The run command refuses an existing named receipt before effects; do not use it
to overwrite this evidence. `canonical-refresh-transport-waiter.json` preserves
the actual result and source hashes, including the host journal. The offline
verifier checks those hashes and canonical/SDK/cancellation observations. All 26
focused Node cases and Docker Ruff on canonical-local.py passed. API production
source is unchanged from the prior 29-test/API-quality increment; no broader
suite was run for this companion-fixture change.

Preflight initially refused invocation while the API was starting; no receipt
or business run had been created. Direct raw-source hash comparison also failed
for src/__init__.py: the packer reads Python as text but hashes raw source bytes,
so line-ending normalization must be accounted for. The exact generated bundle
was instead parsed and hashed with the packer's own sorted Python JSON method;
it matched the existing payload pin
`21b82a201061b86a025265b371b057a3516bb8f549c5a701634390f0a8ca9dc9`.
No bundle regeneration, dependency replacement or native build was needed.

`transport-waiter-local-cleanup.json` records the exact local Wrangler supervisor
SIGINT, independent port-8799 connection refusal, bridge-only tools network and
restored stopped API. This is fixture supervision cleanup, not a workflow
physical-termination receipt. No cloud invocation, new credential grant,
production resource or Containers/shared execution source changed.

Stdout capture, physical CPU termination, actual socket/whole-host interruption,
selective production eligibility and authenticated deployed execution remain
unqualified. The full backend goal is not achieved.
