# Private Worker TCP Transport

Physical workers can optionally reach private services through an authenticated
WebSocket gateway. This transport carries TCP bytes; it does not dispatch jobs,
retry connections, execute SQL, or change admission and ownership rules.

`BIFROST_WORKER_NETWORK` contains one to sixteen explicit entries with
`hostname`, IPv4 loopback `address`, local `port`, and a `wss://` gateway `url`.
`BIFROST_WORKER_NETWORK_TOKEN` supplies a dedicated bearer token. The gateway
must authorize a fixed target for each route; clients must not select arbitrary
destinations. Do not reuse the platform signing secret as a network token.

Before dropping privileges, the deployment entrypoint runs
`python -m src.worker.network --exec /entrypoint.sh python -m src.worker.main`.
This reads the system hosts file and creates a separate non-secret snapshot,
rejecting conflicting mappings. It never modifies `/etc/hosts`, which may be
managed read-only by the container provider. The image includes `libnss-wrapper`;
bootstrap validates that library and adds it to `LD_PRELOAD`, pointing
`NSS_WRAPPER_HOSTS` at the private snapshot before executing the normal entrypoint.
The resolver is inherited by Python and native subprocesses. Other names retain
normal system resolution. Service connection
strings retain those hostnames and select the configured local listener ports.
Database TLS therefore verifies the original database hostname; neither the
driver nor its certificate validation requires modification. A real TLS test
accepts the original hostname and rejects a mismatched certificate hostname
while both resolve to the same loopback server. Use distinct
loopback addresses where services require different hostnames on the same port.

The launcher opens listeners before connecting to the control host and keeps
them available until its existing drain completes. Each TCP connection opens one
WebSocket with subprotocol `bifrost-tcp.v1` and an Authorization bearer header.
The worker admits at most 64 simultaneous connections. Listener setup failures
close listeners already opened; connection failures close that stream without
replaying bytes. Application drivers retain responsibility for reconnection and
transaction retries.

## Wire Protocol

All messages are binary. The first byte identifies the frame:

| Type | Payload | Meaning |
| --- | --- | --- |
| `0` | 1–65,536 bytes | Ordered TCP data |
| `1` | Four-byte unsigned big-endian integer | Credit for bytes written and drained to TCP |
| `2` | Empty | TCP write-half close after preceding data |

Each direction begins with 262,144 bytes of credit. Sending data consumes credit;
only credit for outstanding bytes may replenish it. The receiver rejects data
exceeding its window, malformed frames, data
after FIN, and duplicate FIN. Control frames are processed independently of the
TCP write queue so simultaneous traffic cannot deadlock waiting for credit.
Adjacent small data frames are coalesced into bounded chunks so frame
fragmentation cannot inflate queue object overhead or reject valid TCP traffic.

FIN preserves half-close: the opposite direction remains available. Normal
WebSocket shutdown follows both FINs and acknowledgement of all sent data.
Unexpected shutdown cancels the bridge. TLS, PostgreSQL advisory locks, Redis,
and other TCP protocols remain opaque to the transport.

This mechanism is optional. Ordinary workers with no network configuration
continue to use their existing network directly. Deployment infrastructure owns
the gateway, private network connector, target allowlist, token distribution,
and connection strings; no environment identifiers belong in upstream defaults.

## Service authentication for worker control

The physical launcher's separate `/api/worker-control` WebSocket can supply
optional handshake headers through `BIFROST_WORKER_CONTROL_HEADERS`, a JSON
object of string names and values. For example, a deployment behind Cloudflare
Access supplies `CF-Access-Client-Id` and `CF-Access-Client-Secret` in that object.
Store its value in the deployment's secret manager, never in source or command
arguments. This setting applies only to worker control, not SDK HTTP calls,
private TCP gateways, or arbitrary outbound requests.

With nonempty headers, the destination must use `wss://` without URL credentials,
query or fragment. Redirects are refused, including same-origin redirects, so
credentials never follow an unexpected login or destination. Configuration is
bounded to 8 KiB and 16 headers; duplicate names, transport handshake overrides,
and invalid names/values fail before connection. Connection wire logging is
disabled for authenticated sockets because DEBUG output includes request headers;
the launcher retains lifecycle messages and exception types without credential
values or transport tracebacks. Neither execution-template profile receives this
environment variable, so forked workflow and agent children do not inherit it.

Leaving the variable unset preserves the existing connection. The API still
requires its ordinary signed launcher hello; edge authentication does not replace
the platform signing secret or change worker admission, ownership or drain.
Infrastructure owns the exact edge application/policy, credential scope,
expiration, rotation and revocation. Header support alone does not authorize or
establish a deployment or a working Cloudflare Access policy.
