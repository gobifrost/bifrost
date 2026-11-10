# Hyperdrive PostgreSQL Boundary

Hyperdrive is a Workers binding for adjacent read-only PostgreSQL queries.
It is not a replacement for Bifrost's canonical direct PostgreSQL connections.

PostgreSQL remains authoritative for demand, admission, claims, progress,
cancellation, recovery, and scheduler leases. Providers receive resource
metadata only; they never choose jobs. This boundary applies to every
PostgreSQL origin provider.

## Supported origins

Any PostgreSQL 16-compatible origin reachable through a qualified private
route can serve as a probe target. Examples include Azure Database for
PostgreSQL, Neon, Amazon RDS for PostgreSQL, AlloyDB, and self-hosted
PostgreSQL. Upstream defaults name no provider, hostname, account, network,
or secret. Each deployment supplies its own origin, credentials, and private
route through its approved database and network operating paths.

## Prerequisites

Before qualifying a Hyperdrive probe for a deployment:

1. Provision a dedicated test database and a dedicated least-privilege role
   with only connect and constant-query rights. Do not reuse the production
   application or administrator credential. Keep shared secrets in the
   deployment's established secret store.
2. Provide a private origin route in a network that resolves and reaches the
   selected origin. Use a TCP VPC Service with the PostgreSQL protocol and
   the origin's TLS hostname. Keep public ingress disabled.
3. Bind a Hyperdrive configuration to that exact service, database, and role.
   Disable query caching so qualification reaches the database. Require full
   certificate verification against the service's trusted roots and keep the
   origin connection limit explicit and small.

## Qualification probe

The portable probe is a parameterless authenticated `POST /check` that runs
one constant query in a read-only transaction and verifies the expected
database, authenticated login, and effective role:

```sql
SELECT 1 AS ok,
  current_setting('transaction_read_only') AS read_only,
  current_database() AS database,
  current_user AS role,
  session_user AS session_role;
```

It returns only `{ "ok": true, "read_only": true }`. It accepts no SQL,
reads no application tables, writes no data, and enqueues no work. Each
request creates and closes its own client with bounded connection and query
timeouts. Driver failures return a generic error without logging connection
details. A dedicated login must qualify; an administrator login that assumes
the expected effective role must fail.

## Coordination boundary

Hyperdrive does not support PostgreSQL advisory locks or `LISTEN`/`NOTIFY`.
See Cloudflare's supported-databases reference for the current list.

Keep Bifrost's canonical admission, fencing, dispatch, and lease connections
on the qualified direct PostgreSQL path. The probe qualifies an adjacent
Worker-side SQL primitive; it does not externalize the canonical job engine,
scheduler, or worker coordination through Hyperdrive.

## Private transport

Physical workers that need private origins use the authenticated private TCP
transport in `docs/architecture/worker-private-transport.md`. Connection
strings retain the original database hostname so driver TLS verification is
unmodified. No environment identifiers belong in upstream defaults.
