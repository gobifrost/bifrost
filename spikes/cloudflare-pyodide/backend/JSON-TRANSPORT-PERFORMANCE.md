# Lossless parent JSON transport performance

The source-context adapter fixed real Python integer corruption. Its first
implementation invoked the reviver on every JSON value, including ordinary small
integer control fields. A native Node parent benchmark measured that overhead.

The new conservative screen selects ordinary native parsing only when no possible
16-digit integer, negative zero, fraction or exponent occurs. Up to15 decimal
digits are exactly representable. Numeric-looking string text can cause harmless
slow-path false positives. Both paths retain native grammar validation; there is
no replacement JSON parser or lossy fallback. Runtime source-context/rawJSON
support is still required before either path, including before fixture effects.

Ten numeric/terminal-reader tests passed on CT128, including root/array/object
numeric boundaries, whitespace, signed zero, exponent/fraction preservation and
escaped numeric-looking strings. Actual `--numeric-result --revision 4` passed
through the real upstream engine/SDK and current companion fence: Python checks
large signed input integers before effects and canonical Success preserves the
exact input9007199254740993. Peer, lost-ACK replay, stale-owner rejection,
credential401, accounting2,2,1,0 and cleanup passed. Supervisor72.554s is whole
lifecycle, not startup.

## Measured Node parent round trips

Cached guest dependency image; Node26.12.1/V8 14.6.202.34-node.40, network disabled,
.5CPU/512MiB/64PIDs. Each case runs50 samples per parser, alternating order,
after one correctness/warmup round. Containers are removed; the owned qualification
test stack was stopped during both benchmarks. Unrelated guest load was not
controlled. All samples, including quota/GC outliers, remain.
Median here is sorted index25, p95 index47, for50 values.

| Fixture | Bytes | Baseline median, after run (ms) | Lossless median before (ms) | Lossless median after (ms) | Lossless p95 after (ms) |
|---|---:|---:|---:|---:|---:|
| Small structured JSON |4800|0.025|0.189|0.033|0.049|
| Medium structured JSON |98900|0.469|3.718|0.633|0.900|
| Near-retention string |900014|0.543|0.568|1.266|2.059|

The structured cases improve; the large string regresses due to scanning.
Before/after are separate constrained runs, not simultaneous identical scheduling.
Both versions retain the expected precision control: ordinary JSON parsing
corrupts the large integer, lossless parsing preserves it. Ordinary parsing is
not an acceptable whole-workflow substitute.

Raw inputs are deterministic synthetic data and their hashes are recorded.
`results/json-transport/raw.json` is before, `after.json` is after, and
`runtime-receipt.json` is the separate real engine/SDK control with source/runtime
pins. No cloud, SDK request, Pyodide FFI, startup, billed CPU, peak-memory or cost
measurement is inferred from this Node microbenchmark.

Repeat from the spike directory in the same bounded guest container:

```sh
node backend/json-transport-benchmark.mjs
node backend/verify-json-transport.mjs
```

Full native startup, canonical pre-launch refusal retirement, deployed memory/
isolation and abnormal cancellation remain unqualified. Admission stays disabled.
