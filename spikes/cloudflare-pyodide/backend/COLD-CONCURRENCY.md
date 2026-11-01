# Cold Python concurrency: minimal provider reproduction

One bounded pair on a fresh local Wrangler supervisor reproduces the native
failure without any BiFrost module, dependency wheel, credential, tenant API or
outbound networking. Participant 0 fails with internal error reference
`jt60v2c57mkbbu3pkff0jr60`; participant 1 returns `{before:null,value:42}`.
The supervisor logs `kj/table.c++:49: failed: inserted row already exists in table`
with the same reference. Parent HTTP 200 means the observation was captured;
it does not mean both Python runs succeeded.

Runtime: Wrangler 4.156.0, workerd 1.20261023.1; binary digest and native offsets
are in `results/cold-concurrency-native.json`. The structured response and source
digests are in `results/cold-concurrency-minimal.json`. No retry or warmup was
performed for this experiment. Two harness unit tests prove simultaneous entry,
separate restricted definitions, and preservation of a peer failure; they do not
substitute for the actual runtime result.

## Reproduction

Use a fresh supervisor process so Python has not already initialized:

```sh
wrangler dev --config backend/wrangler.cold-concurrency.jsonc --port 8800 --ip 127.0.0.1
```

The exact retained invocation ran that CLI inside the existing local
`bifrost-pyodide-ddf316c1` container at `/spike`. From the spike directory:

```sh
node backend/cold-concurrency.mjs --run
node backend/cold-concurrency.mjs --verify
node --test backend/cold-concurrency.test.mjs
```

`--run` refuses to overwrite the existing receipt before invoking anything.
For an independent reproduction use an isolated checkout/output directory;
preserve the original evidence. `--verify` checks observation structure and
source integrity and prints participant outcomes, including errors. It is not
a success assertion for the Python pair. The endpoint accepts only POST,
ignores request content and runs the fixed tiny fixture. Egress and subrequests
are disabled; no binding or environment capability is passed into Python.
The config is local qualification only and has not been deployed.

The exact local supervisor was stopped after observation. Port 8800 was checked
closed; no test-stack network attachment was added for this experiment.

## Disposition and next step

This is a native host-runtime startup failure, not an unsupported Python package
or missing PyEmscripten wheel. There is no candidate package to compile. Patching
BiFrost's engine or replacing its SDK cannot fix this minimal reproduction.
Compiling a private workerd fork would not establish compatibility with the
Cloudflare-managed runtime and is outside this bounded workaround.

The warm canonical two-tenant result remains valid within its stated condition.
Do not silently warm/retry a business attempt, claim cold-start acceptance or
route arbitrary untrusted workflows based on it. The smallest next safe work is
explicit provider admission before claim/effects, while a provider-side fix or
separately authorized deployed reproduction is needed to resolve the runtime
boundary. CPU physical termination remains a separate material blocker.
