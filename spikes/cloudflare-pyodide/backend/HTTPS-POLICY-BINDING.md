# Trusted HTTPS policy binding, local companion

Prototype 0.1.0. This extends the private companion adapter, not an upstream
scheduler API, public authorization surface or provider admission decision.
Default configuration still refuses platform-fetch-https. A trusted host must
configure a named policy and select that name while binding reviewed workflow
provenance; the requirement and selected policy must both be present.

https_policy_binding.py validates and snapshots named tenant-scoped policies
into frozen dataclasses with tuple origins. No package/native dependency is
added: this is standard-library Python. Origins are a conservative canonical
DNS-only HTTPS subset; custom ports, IP literals and noncanonical spellings are
refused. This is not DNS/private-address safety qualification. Each manifest
plan receives its own descriptor bound to the execution ID. The existing
attempt fingerprint covers that plan and pinned workspace. Duplicate dispatch
cannot rebind it. Mutating caller-owned configuration cannot change it.

Before native bookkeeping or physical dispatch, the adapter requires policy
org_id to match upstream context.organization.id and active_execution.org_id.
The descriptor is included in both request-budget preflight and actual envelope.
The generated local receiver accepts only its two fixed synthetic policy IDs,
vendor.example origin, budget6, matching execution and organization; it refuses
missing/changed descriptors before creating Python/API capabilities. It does
not authenticate an internet caller. The transport remains an isolated local
test link and public host admission is still disabled.

## Evidence and correction

36 native tests pass: the original SDK-only refusal cases plus policy snapshot,
single dispatch, explicit declaration/name matching, tenant mismatch before
effects, invalid configuration and frozen-data cases. 14 Node policy/generator/
deadline tests pass. Ruff and Pyright pass in bounded network-disabled guest
containers. Ruff initially tried to write a read-only cache; --no-cache fixes
that harness invocation, without changing any source or lint rule.

The first canonical integration (v3) failed closed before workflow POSTs because
the new code incorrectly checked context.org_id. Unit fixtures had repeated that
wrong assumption. Current upstream WorkflowExecutionConsumer builds organization
as an object with id; active execution metadata separately uses org_id. The
adapter, tests and receiver now use exactly those upstream fields, with no
fallback. The failed receipt and exact guest packet remain preserved.

Corrected v4 exits0, whole supervisor lifecycle76.16171273821965seconds. Two
real engine/SDK attempts explicitly declare platform-fetch-https and choose
separate named tenant policies. Config/table operations, fresh state, peer403,
known run-token header403, synthetic vendor writes and cross-origin redirect403
pass. Independent destination sees exactly four approved forwards. Canonical
Failed/Success, one launch each, stale-owner rejection, duplicate reuse,
lost-ACK reconnect, unchanged replay, terminal401, SDK close and retirement pass.
Owned test project/providers stopped; volumes and separate dev stack preserved.

Same CT128 2CPU/8GiB allocation; provider1CPU/2GiB/128PIDs; cached image33cd92be,
API1d33150, enginef49d4ba, SDK139596497 and integrity-pinned staged bundle61d818ae.
No deployment, Cloudflare invocation, new credentials/grants or resource change.
No new CPU/memory/cost measurements are claimed.

```sh
# On CT128, after staging the packet with the same fixture/config/runtime pins:
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-https-v4/ct128-canonical-failure.py --https-fixture --revision 4
# Native focused tests, inside the existing constrained dependency container:
python -m unittest test_https_policy_binding test_private_http_pool
# Repository evidence verification:
node spikes/cloudflare-pyodide/backend/verify-https-binding.mjs
```

Packet and run roots remain under /opt/bifrost-tests/pyodide-ddf316c1. Source
hashes, exact commands and runtime observations are in results/https-binding.
Older canonical-HTTPS and SDK-only guard verifiers pin the changed source to
338eac91a; their older receipts are not evidence for this new binding mode.

## Next boundary

This is pre-dispatch companion admission, **not upstream preclaim selection**.
Trusted production policy storage/selection, live control authentication through
dispatch, general receiver policy distribution and external TLS are unfinished.
Workflow declarations are trusted reviewed-source input, not static discovery
of arbitrary Python behavior. Cleartext header matching is not malicious
exfiltration prevention. Deadline/revocation races and already-dispatched vendor
writes remain to be tested; resource uncertainty must stay accounted for.
CPU cessation, deployed memory enforcement and arbitrary untrusted admission
remain unresolved. Do not enable a public backend based on this receipt.
