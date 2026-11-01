# Historical termination evidence audit

This is an offline source/result audit, not a new runtime experiment. Heavy
laptop testing remains stopped; no Worker or cloud request was made.

The cloud CPU-cancel fixture requested cancel in its input but reported
`abort_requested: false`. It completed with before/after effects before its
parent timer fired. This means **cancellation was not exercised**, not that a
received cancellation failed to stop the child. Human notes already described
the missed timer, but the machine verifier only checked for an after effect and
reported a false cancellation result. It now reports not_exercised/null and
still fails the acceptance gate.

The separate real cloud cpu_catch observation is different: no abort was
requested, Python caught CpuLimitExceeded, then emitted caught and after in
order. It remains a counterexample to treating the catchable CPU exception as a
trusted physical-stop receipt. The uncaught exception case observed only before;
that bounded absence also does not prove physical isolate termination.

The local external observer records an abort request and before/after effects.
Its event timestamps measure about 1.809 seconds **between before and after**.
It does not record the abort timestamp, so exact abort-to-effect latency and
ordering are not directly measured by that artifact. Do not relabel that interval
as measured time after abort. A future controlled test must record an independent
abort timestamp/receipt as well as effects; no new probe is authorized here.

`results/termination-evidence-audit.json` retains these distinctions. Its three
historical local probe/driver source hashes match the recorded receipt at the
exact Git snapshot recorded in the audit. The driver changed afterward, so a
current-file hash is not a substitute. These are recorded local-source and
deployment-version associations, **not per-version deployed source attestation**.
No new Cloudflare read/deploy or credential access was used to strengthen them.

The existing historical fresh-cloud-verification.json is preserved byte for
byte. The current verify-fresh-cloud.mjs is now read-only and prints its updated
classification; it deliberately exits 1 because physical-stop acceptance still
fails. Run only the explicit lightweight tests below, not the networked probe
test glob:

```sh
node --test backend/termination-evidence.test.mjs
node backend/verify-fresh-cloud.mjs  # expected exit 1; no writes or cloud calls
```

Four tests passed, including the actual historical verifier's expected rejection
and before/after byte equality of the old report. Unknown/unexercised signals
cannot produce physical-stop proof. No SDK, auth, CPU quota, isolation or
production routing guarantee was weakened. Arbitrary untrusted execution stays
unqualified; the full backend goal remains incomplete.

## Prepared guest observer correction

The timestamped guest driver now retains correlation for all three fixed case
IDs throughout the bounded observation session. Previously, advancing `active`
would reject late callbacks from a previous case; that could discard a useful
counterexample. Final classification is recomputed from retained event arrays.
Unknown IDs, invalid events and excess observations remain refused. No fixture
execution, runtime setting, quota or deployment was changed by this correction.

```sh
node --test backend/termination-observer.test.mjs backend/timed-termination.test.mjs backend/termination-evidence.test.mjs
```

Nine offline tests pass, including a deterministic previous-case late effect
after a subsequent case starts. These are observer regression tests, not a
physical termination experiment. The prepared actual-workerd fixture remains
unexecuted pending legitimate availability of the pinned runtime bundle.

Current official documentation describes `load()` as creating a new Worker and
custom CPU limits as raising exceptions, but does not document an explicit
terminate operation on the loader/stub. Documentation is API evidence, not
physical-stop evidence:
[API reference](https://developers.cloudflare.com/dynamic-workers/api-reference/),
[custom limits](https://developers.cloudflare.com/dynamic-workers/usage/limits/).
