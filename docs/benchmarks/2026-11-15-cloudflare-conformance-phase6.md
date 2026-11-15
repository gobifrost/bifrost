# Cloudflare capacity conformance phase six: unresolved fixture qualification

Phase six exercised disposable Cloudflare capacity-provider fixtures while the
API-only-worker source and worker modules were frozen at `304c0d130`. Its goal
was to run the 17-case black-box capacity conformance suite with Cloudflare's
dashboard-backed physical-process hook. It did not reach that suite: every
fixture stopped before the first physical `ContainerStarted` event. Therefore
this report records failure dispositions and configuration evidence, not a
Cloudflare conformance, reliability, cost, invoice, or production qualification.

The sanitized, machine-readable companion is
[the phase-six baseline](baselines/cloudflare-conformance-phase6-2026-11-15.json).

## Disposition

| Fixture | Boundary reached | Disposition | Cleanup |
|---|---|---|---|
| p6 | Wrangler deploy and secret setup | Wrangler named its application `-workercontainer`; the harness expected the HTTP adapter's `-worker-container` convention and did not start observation or protocol traffic. | Exact owned application and script deleted. |
| p6b | Wrangler deploy | Secret upload failed with Wrangler DNS resolution failure. No capacity request ran. | Exact owned application and script deleted. |
| p6c | Wrangler deploy | Wrangler upload's version readback returned Cloudflare API code `10007` for the fresh Worker. No capacity request ran. | Owned script deleted; no owned application was discovered. |
| p6d | HTTP-adapter fixture, first physical start | Provider `POST /start` returned HTTP 502. Dashboard readback had one Durable Object assignment and zero instances. | Application and script deletion returned HTTP 200; namespace was absent after script removal. |
| p6e | Same-incarnation cold-start reconciliation | The one permitted readback/reconciliation path later encountered a provider `URLError`; no `ContainerStarted` event was observed. Earlier helper code did not persist each intermediate status. | Application and script deletion returned HTTP 200; namespace was absent. |
| p6f | Repaired adapter, index image digest | The initial 502 reconciled to one same-incarnation replay returning HTTP 200; observation later hit typed DNS `gaierror`. No physical start was observed. | Application and script deletion returned HTTP 200; namespace was absent. |
| p6g | Repaired adapter, resolved amd64 image, isolated DNS | Initial HTTP 502, same-incarnation GET 200, and exactly one contract-gated replay POST 200. For 240 seconds all subsequent provider and hook reads were HTTP 200, but dashboard telemetry remained one Durable Object and zero instances. | Application and script deletion returned HTTP 200; namespace was absent. |

No fixture executed a formal conformance case, so there are no 17-case pass,
fail, or skip counts. The separate concurrent-start and two-slot prepare probes
were likewise not run. A skipped fault operation is not a pass, and a dashboard
`VMStarted` event would not be evidence of a physical worker launch; this phase
observed neither a qualifying `ContainerStarted` event nor a completed fixture
process.

## Bounded cold-start reconciliation

The frozen provider persists a start intent before it calls the physical
container seam. On a 502 it may observe the same incarnation and replay only
when the Durable Object reports the durable `never_started` fact. Accepted,
exited, destroyed, and uncertain results are fenced rather than replayed.

P6g followed that rule: it issued one original start, one readback, and one
conditional replay. It then observed only until the 240-second deadline. This
is not a retry of a conformance case and must not be interpreted as a
single-launch proof: no physical start was observed.

## Configuration differential

The early HTTP adapter uploaded container metadata with only
`class_name: WorkerContainer` and omitted a compatibility date. The known
Wrangler deployment included a named container and compatibility date
`2026-11-10`. The corrected adapter used for p6f/p6g explicitly sent:

```text
compatibility_date = 2026-11-10
containers = [{ name: <adapter application name>, class_name: WorkerContainer }]
observability = enabled
```

Read-only Cloudflare version metadata confirmed that the current root-managed
script has the named `WorkerContainer`, the same compatibility date, all three
Durable Object classes, a linked container application, a two-instance limit,
and the resolved amd64 image used by p6g. This supports the configuration
repair, but does not isolate it as the cause of any prior failure.

The remaining material fixture-only differences are deliberate: p6g supplied a
sleeping `python -c` entrypoint and a public dummy API origin so that a physical
proof would not depend on admitting fabricated worker identities. The successful
root path used the image's normal Bifrost entrypoint, its real API origin, and
its dedicated stored capacity credential. The fixture's derived capacity token
was valid for the protocol but is not the same credential. These differences
leave the cause of p6g's no-instance result uncertain.

P6f used the multi-architecture index digest
`sha256:8ebb986875fc4bfa9d977c5f46ebff56e2fb0cb3c735bd8fb4a2afa6a1f0c410`.
P6g instead used the resolved amd64 manifest digest
`sha256:7138a8c77dada29af2bc573176f2e9e41cf3d68b900694a751791074155e1fa7`,
which is the image identity used by the working root path. That correction did
not cause a physical fixture start within p6g's observation window; it is not a
causal conclusion about image architecture.

## Evidence and limits

P6g ran all provider, hook, and Cloudflare API traffic in a disposable Docker
qualification container with DNS set to `1.1.1.1`. DNS resolution for both
`api.cloudflare.com` and the disposable Worker hostname succeeded before the
warm-up. The incrementally flushed operation timeline retained the HTTP 502,
the one allowed replay, each subsequent 200 read, and cleanup statuses without
recording bearer tokens, secret bindings, environment values, or raw error
bodies.

The root-managed onboarding application separately started real work after the
configuration repair. That is useful differential evidence, but it is outside
this fixture's 17-case contract and does not make phase six a production or
invoice qualification. No cost, invoice, capacity, sustained-load, failure
recovery, or multi-worker scale claim follows from these receipts.
