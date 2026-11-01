# Python/JavaScript marshaling measurement

This bounded **local** experiment measures three round-trip paths in one fresh
Python Dynamic Worker. It uses no BiFrost credentials, SDK operation, third-party
wheel or egress. It does not qualify the execution backend or estimate cloud cost.

| Text bytes plus fixed object fields | Python JSON round-trip median ms/op | FFI conversion round-trip median ms/op | FFI + JS JSON round-trip median ms/op |
|---|---:|---:|---:|
| 128 | 0.020 | 0.040 | 0.100 |
| 4096 | 0.020 | 0.040 | 0.080 |
| 65536 | 0.300 | 0.400 | 1.300 |

These are five sample medians, with 100/50/10 operations per sample respectively.
Method order rotates across samples. `results/interop-local.json` retains every
sample and exact serialized byte counts. Correctness is checked before each
timed batch and after its last operation. Clock quantization is visible in the
samples, so the small-payload differences are coarse observations, not precise
microsecond predictions or a monotonic size model. There is one host/runtime
trial, no p95/p99 population, no Linux comparison and no independently measured
CPU or peak memory. Outer observer wall time includes Docker and startup; it is
not isolated interpreter startup time.

The FFI-only path is `to_js(..., dict_converter=Object.fromEntries).to_py()`.
The JS JSON path additionally stringifies and parses that JS object. The Python
path uses `json.dumps`/`json.loads`. These compare complete operations using
different serialization engines, not an additive accounting of pure boundary
overhead. Avoid unnecessary cross-language object materialization; this does
not justify replacing upstream SDK transport or execution semantics.

## Correctness boundary discovered first

The original payload included `None`. The initial run raised
`RuntimeError("Interop correctness mismatch")`, returned a Wrangler HTML error
page, and produced **no valid timing result**. The exact failed source and
diagnostic are retained in `results/interop-initial-source.txt` and
`results/interop-initial-failure.json`.

The corrected experiment reports an explicit null probe: direct FFI round-trip
preserves `{"none": None}`, while FFI plus JavaScript JSON does not. Pyodide's
default None/undefined conversion causes a property to disappear when JS JSON
serializes it. The timed payload therefore uses only strings, integers, lists,
dictionaries and booleans that pass equality checks. This restricted timing
domain is not a claim of JSON-equivalent semantics for every Python value.
The current SDK's Python JSON path must not be silently replaced with that
object conversion. See the [Pyodide FFI API](https://pyodide.org/en/stable/usage/api/python-api/ffi.html).

Observed runtime self-report: Pyodide **314.0.6**, Wrangler 4.156.0, local workerd
1.20261023.1. The fetched current documentation describes 314.0.7. The earlier
dependency lock/resolver statement of 314.0.7 is not the observed runtime version
for this minimal Worker; do not transfer package ABI or runtime acceptance from
the resolver version. This experiment uses built-ins only and no wheel build.

## Reproduce and verify

Use a fresh local supervisor:

```sh
wrangler dev --config backend/wrangler.interop.jsonc --port 8801 --ip 127.0.0.1
node backend/interop-benchmark.mjs --run
node backend/interop-benchmark.mjs --verify
```

The retained CLI ran in the existing local `bifrost-pyodide-ddf316c1` container;
the observer invokes that container's loopback endpoint. `--run` refuses to
overwrite its receipt before invocation; use an isolated output checkout for
an independent reproduction. `--verify` checks raw samples, medians, correctness
and exact source digests without invoking Python. Observation failures are
recorded as failures, not retried. The first run used the old observer, whose
non-JSON response parse failure is documented separately.

The exact interop supervisor was stopped afterward, with port 8801 checked
closed. No test-network attachment or cloud invocation occurred. Provider
physical termination of untrusted CPU work remains unresolved; stopping this
local test supervisor is only fixture cleanup.
