# Capacity providers

Implementation kit for the external worker capacity protocol
([`docs/architecture/external-worker-capacity.md`](../../docs/architecture/external-worker-capacity.md)).
A *provider* is an HTTP service the Bifrost controller calls to prepare, start,
and delete worker incarnations. It never receives job payloads and never chooses
which jobs run.

## Layout

| Path | Purpose |
|---|---|
| `python/capacity_core.py` | Shared protocol core: auth, scope, routing, reservation validation, resource UIDs, lifecycle, capacity accounting, tombstones. |
| `providers/floci/{gcp,aws,azure}/capacity.py` | Reference adapters for the Docker-backed Floci emulators. |
| `conformance/conformance.py` | Black-box conformance suite. Point it at any provider. |
| `conformance/hooks/control_http.py` | Reference hook: drives a provider's admin endpoints for restart / stop / fault. |
| `conformance/selftest.py` | Proves the suite accepts a correct provider **and rejects a duplicate-launching one**. |

Only `prepare`, `start`, `observe`, and `release` are adapter-specific. Everything
else is shared, so a new cloud means a small adapter, not a new implementation of
the protocol.

## Adapter contract

The API-only worker spike derives each external launcher's control key from the
dedicated CAPACITY_TOKEN and its canonical worker/resource identities. The three
Floci adapters reuse api/shared/worker_control_auth.py; run them from a complete
checkout. WORKER_ENV_JSON supplies the API URL and runtime paths, not database,
Redis, storage or installation signing credentials. Privileged platform fields
are rejected. Environment construction is covered by
api/tests/unit/test_capacity_adapter_worker_env.py; that unit coverage does not
qualify any adapter against a real cloud.

```python
prepare(ctx, spec) -> handle      # MUST NOT start a process
start(ctx, handle) -> Observation  # at most ONE launcher per incarnation
observe(ctx, handle) -> Observation
release(ctx, handle) -> Observation("deleted")  # only after deletion is confirmed
```

Rules the core relies on:

- **Never report absence on error.** Raise instead; `absent` means "certain
  nothing exists". Reporting absence for an unreachable cloud fabricates a
  stopped incarnation.
- **Prepare launches nothing.** Where a cloud cannot create-without-starting
  (Azure Container Instances), prepare is ledger-only and creation happens in
  start. The core records a durable prepare intent before calling an adapter;
  adapters must make repeated prepare calls with the same `operation_id`
  idempotent, because a lost reply is reconciled before start or deletion.
- **A lost start reply fails closed.** A `prepared` observation after an
  interrupted start is ambiguous and must never cause another launch. Only an
  adapter observation with `start_fact="never_started"` may authorize that
  retry; otherwise the caller must delete the incarnation. An unconfirmed delete
  also fences further starts. A reference adapter must retain uncertainty when
  a task-list read has not yet exposed an accepted start.
- **Handles are opaque JSON.** Cloud identifiers belong here, never in
  `resource_uid`: that field is `^[a-zA-Z0-9._:-]+$`, which excludes `/`, so ECS
  ARNs and ACI/Cloud Run resource paths are invalid UIDs.

## Running a provider

```bash
CAPACITY_TOKEN=...            # 32-512 non-space ASCII
CAPACITY_SCOPE=demo
WORKER_VERSION=<image build>
# The provider reports hardware/build only. The version-matched image registers
# its capabilities automatically during authenticated worker hello.
WORKER_IMAGE=<registry digest>
TLS_CERT_FILE=... TLS_KEY_FILE=...   # required: the API dials HTTPS only
STORE_PATH=/var/lib/capacity/ledger.json  # durable ledger; omit for memory-only
python deploy/capacity/providers/floci/gcp/capacity.py
```

The ledger is the source of truth for idempotency and tombstones. The JSON
implementation is single-process; a multi-process deployment must supply a
linearizable store (the Cloudflare provider uses Durable Objects for exactly
this reason).

## Conformance

```bash
python deploy/capacity/conformance/conformance.py \
  --base-url https://127.0.0.1:8443 \
  --token-env CAPACITY_TOKEN \
  --scope demo \
  --hook-command "python deploy/capacity/conformance/hooks/control_http.py"
```

Exit 0 requires every required case to pass with zero skips. Failures exit 1;
missing/unsupported observation or fault hooks exit 2 (incomplete). Restart and
physical cases stay in the report even when no hook is configured.

The suite covers capabilities, prepare idempotency, conflicting reservations,
UID fencing, duplicate and stopped-incarnation starts, tombstones, scope and auth
isolation, provider-restart durability, uncertain accepted starts across restart, and
physical launch identity. The self-test rejects both ordinary and uncertain-only
duplicate launches and verifies that missing hooks cannot produce success.

The hook is required for the physical cases. It must return the **cumulative set
of launch identities ever observed** — a boolean or a count cannot distinguish
one long-lived launcher from two sequential ones.

## Floci qualification boundary

The Floci adapters are reference implementations for the shared protocol. Their
SDK-fake checks cover a repeated AWS prepare using a stable worker/operation
identity, accepted-but-invisible ECS starts over the HTTP capacity API, and
deletion readback before they report `deleted`. These SDK-fake regressions do not
run the Floci emulator containers. They do not prove
real-cloud eventual-consistency, IAM, or provider idempotency beyond the Floci
emulator; qualify those properties against each production provider separately.

## What conformance does not prove

It proves the provider honours the protocol. It does **not** prove cloud
behaviour: IAM sufficiency, networking, quotas, image compatibility, or that a
launch fits the 300 s start timeout. Those need a runtime qualification against a
real account, with receipts.
