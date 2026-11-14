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
| `providers/floci/{gcp,aws,azure}/capacity.py` | Thin adapters against the Floci emulators — the free, Docker-backed proof of provider-agnosticism. |
| `conformance/conformance.py` | Black-box conformance suite. Point it at any provider. |
| `conformance/hooks/control_http.py` | Reference hook: drives a provider's admin endpoints for restart / stop / fault. |
| `conformance/selftest.py` | Proves the suite accepts a correct provider **and rejects a duplicate-launching one**. |

Only `prepare`, `start`, `observe`, and `release` are adapter-specific. Everything
else is shared, so a new cloud means a small adapter, not a new implementation of
the protocol.

## Adapter contract

```python
prepare(ctx, spec) -> handle      # MUST NOT start a process
start(ctx, handle) -> Observation  # at most ONE launcher per incarnation
observe(ctx, handle) -> Observation
release(ctx, handle) -> Observation
```

Rules the core relies on:

- **Never report absence on error.** Raise instead; `absent` means "certain
  nothing exists". Reporting absence for an unreachable cloud fabricates a
  stopped incarnation.
- **Prepare launches nothing.** Where a cloud cannot create-without-starting
  (Azure Container Instances), prepare is ledger-only and creation happens in
  start.
- **Handles are opaque JSON.** Cloud identifiers belong here, never in
  `resource_uid`: that field is `^[a-zA-Z0-9._:-]+$`, which excludes `/`, so ECS
  ARNs and ACI/Cloud Run resource paths are invalid UIDs.

## Running a provider

```bash
CAPACITY_TOKEN=...            # 32-512 non-space ASCII
CAPACITY_SCOPE=demo
WORKER_VERSION=<image build>
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

Exit 0 means every executed case passed. `SKIP` is never a pass.

The suite covers capabilities, prepare idempotency, conflicting reservations,
UID fencing, duplicate and stopped-incarnation starts, tombstones, scope and auth
isolation, provider-restart durability, and physical launch identity.

The hook is required for the physical cases. It must return the **cumulative set
of launch identities ever observed** — a boolean or a count cannot distinguish
one long-lived launcher from two sequential ones.

## What conformance does not prove

It proves the provider honours the protocol. It does **not** prove cloud
behaviour: IAM sufficiency, networking, quotas, image compatibility, or that a
launch fits the 300 s start timeout. Those need a runtime qualification against a
real account, with receipts.
