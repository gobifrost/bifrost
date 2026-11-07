# Live control authentication, without execution admission

The actual isolated API `/api/worker-control` accepted upstream Launcher's signed
hello and same-owner reconnect. PostgreSQL generation advanced from 1 to 2.
Invalid HMAC proof, exact captured-hello replay, superseded socket and stale
generation on the current socket each closed with policy code 1008. Invalid
proof acquired no owner; replay/stale messages did not advance its generation.
This is actual WebSocket/API/DB evidence, not a simulated control link.

Safety boundary: the fixture first uses canonical register_static_worker with
an empty supported_handlers list. It sends no inventory or ready/execution
message. Upstream ApiExecutor waits for inventory before executor startup; the
row stayed starting, ready_at remained unset, supported handlers stayed empty,
and the fixture worker had zero claims. Both Linux templates remained unstarted.
No Pyodide or workflow execution is implied by authentication success.

This avoids the unqualified production path: a previously unknown static
launcher is registered by RemoteControlHost with the general remote handler set.
Do not use that path to advertise this experimental backend. Persisted selective
runtime eligibility and safe physical-route inventory adoption remain required.

`canonical-refresh-control-auth.json` contains the real observations and exact
source hashes; runtime_manifest and payload_sha256 are null because no Python
Worker was invoked. Signed hello/HMAC material was used only in memory for the
replay test and is not retained. The existing isolated stack key was used; no
credential/grant or security setting changed. The fixture worker was deleted,
all sockets closed, and the own API stopped cleanly.

Offline evidence verification from the spike directory:

```sh
node backend/verify-control-auth.mjs
```

The invocation that ran before the laptop-test migration checkpoint was:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-control-auth control-auth
```

It refuses existing receipts. Do not invoke another heavy laptop batch. The
wrapper still contains laptop-specific container paths; remote execution must
use the verified owner-specific harness described in REMOTE-TEST-HANDOFF.md.

Local Docker had stopped and lost its host tmp bind directory before this probe.
The exact own directory and existing helper were restored, then test.sh rebuilt
the ephemeral test database normally. No source evidence was lost. After the
migration instruction arrived, the completed test was checkpointed and all own
test infrastructure/helper containers were stopped without volume or lock
deletion. No remote execution has occurred; VM108/Kata is not a candidate.
