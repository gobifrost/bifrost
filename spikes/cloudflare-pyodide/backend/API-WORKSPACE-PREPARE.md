# Authenticated API workspace preparation

`workspace_prepare.py` replaces fixture-supplied execution hashes with hashes
obtained through the actual upstream SDK module endpoints. The trusted host
supplies a bounded list of declared workspace imports. The helper snapshots the
run context/token, fetches the entry module and resolves those imports using
`GET /api/sdk/modules/{path}` and `GET /api/sdk/modules-resolve`. It reuses
upstream's path/name helpers, rather than implementing Python import semantics.
The API derives source scope from the signed run principal; this helper supplies
no query-selected organization, Solution scope, storage credentials or grants.

Each returned source is SHA256-checked against its API hash, expected path and
unflattened storage path. An existing workflow pin must match. The helper returns
a new context with the API-derived entry pin and a module manifest; the original
context is unchanged. It does not execute, import or persist retrieved source.
Prepared-object repr excludes credentials. A non-200 response is refused without
reading/logging its body, retrying credentials or following a redirect.

Preparation is bounded to 31 declared imports plus the entry, 2 MiB per response,
4 MiB total and 20 seconds overall. Canonical dotted or slash-separated import
declarations are required before network I/O. A fail-first test exposed upstream's
normalization of `../escape` to `escape`; the helper now refuses that ambiguous
declaration before making even the entry GET. The original failed source/test
and observed one-failure/23-case result remain preserved.

Packages and namespaces keep the API's paths. A namespace needs explicitly
declared pinned descendants; missing imports, scoped storage flattening, corrupt
hashes, stale entry pins, invalid credentials, oversized responses and redirects
have focused refusal coverage. Package/namespace preparation has native HTTP
contract tests; the real Worker qualification still uses the supported flat
two-module fixture shape.

## Actual qualification and boundaries

The shared native fixture no longer assigns a known entry hash into the context
or pre-binds fixture-computed hashes. It calls this preparation helper with the
genuine upstream-minted credential, independently compares its manifest against
the published source as a test oracle, then supplies the prepared copy to the
reusable private adapter. The oracle is not an input to preparation or dispatch.
An invalid credential is also tried against the actual API and must return 401
before plan binding/physical execution.

CT128 V2 passes that complete API-preparation-to-engine/SDK path: each run makes
two successful preparation GETs, then the actual private adapter/engine/SDK writes
70/78, preserves failure ACK loss and stale-owner fencing, retains the authorized
peer, and settles accounting 2 → 2 → 1 → 0. Twenty-three focused native cases,
Ruff and Pyright pass. [Raw receipt](../results/api-prepare/v2.json) pins exact
source/workspace/image hashes; native API source/resolver files read back clean
against the tested base. The provider remained running/no OOM before owned
cleanup, and no owned test containers remained running afterwards.

Supervisor 98.657 seconds includes setup, explicit prewarm, preparation, barriers
and cleanup. It is not pure startup or isolated SDK overhead. The observed two
extra parent GETs per execution are request-count overhead; no CPU, peak memory,
bill or Linux-comparison numbers are inferred from that total.

This is preparation for **declared** workspace imports. It does not discover an
arbitrary dynamic dependency closure or prove all imports safe before effects.
The API does not return an atomic whole-workspace commit: these are individually
authenticated/hash-pinned source observations. The existing Worker integrity
checks reject a changed fetched module, but atomic release provenance and
arbitrary untrusted admission remain separate gaps. Unknown dependencies must
not be silently added to a running plan. Solutions/artifact workspaces remain
unsupported rather than flattened into loose-workspace semantics.

The [source-change control](SOURCE-CHANGE-CONTROL.md) now exercises an actual
helper mutation after preparation. Two local runs reject that eager dependency
before workflow effects and preserve the held peer, using a recorded HTTP 409
for the expected digest denial. The original uncaught-error failures remain
preserved; this does not imply atomic revisions or safe late dynamic imports.

The normal private adapter still owns transport retention and delegates claims,
credential issuance and terminal acceptance to upstream. Automatic provider
startup, public profile selection, signed combined control transport, cold
concurrent startup and deployed abnormal CPU settlement remain unaccepted.
No runtime/admission/security setting or Cloudflare resource was changed.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/api-prepare-v2/ct128-canonical-failure.py --api-prepare --revision 2
node spikes/cloudflare-pyodide/backend/verify-api-workspace-prepare.mjs
```

Same assigned CT128 2 CPU/8 GiB/no swap, cached image/runtime bundle and isolated
project. No laptop Docker, new dependencies/credentials/grants, cloud invocation,
deployment or production change. Prewarm remains credential-free and local;
normal completion/retirement does not require isolate destruction.
