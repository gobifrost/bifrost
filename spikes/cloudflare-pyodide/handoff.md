# Spike handoff: package 0.1.0

Latest increment: [concurrent canonical local qualification](backend/CANONICAL-ISOLATION.md).
Two warm fresh tenant runs passed actual engine/SDK execution, wrong-peer denial,
state canaries, duplicate delivery, terminal reconciliation and cleanup.
Cold concurrency failures are retained; no cloud invocation was performed.
The smallest safe next implementation step is a credential-free minimal
reproduction of the cold workerd startup race, followed by explicit admission
tests for the supported runtime boundary. Physical termination remains blocking.

Current contribution checkpoint: see [combined canonical local proof](backend/CANONICAL-LOCAL.md)
at a00ae73e9 and the subsequent [post-commit recovery boundary](backend/POSTCOMMIT-RECOVERY.md).
The latter qualifies the earlier one-log claim to the after-full-callback fault
window. A mid-callback fault preserves the durable outcome but leaves follow-up
unrepaired by stale replay; native log recovery is explicitly at least once.
The contribution also includes runtime seams, a shared envelope and tests
under `api/`, plus the narrow test-module mount in `docker-compose.test.yml`.
The initial package-only snapshot below predates those changes. None is a
production deployment, supported-provider acceptance or physical-stop proof.

Decision: **YELLOW** for a restricted useful subset; **RED** for arbitrary
untrusted code in a shared Worker interpreter.

Working: exact upstream engine/SDK source reuse, small settings adapter, local
and deployed Cloudflare fixtures, real SDK integration/config and table writes
against the isolated local API, independent REST readback, Linux engine-only
comparison, raw metrics/security evidence, repeatable scripts and setup docs.
Initial tested implementation commit: `46f49107d`; final source/evidence commit
is the branch HEAD reported with the handoff. All edits are under this directory.
The source-hash manifest pins the authoritative platform baseline separately.

Executed verification:

- `node sdk-proof.mjs` and `node verify.mjs results/benchmark-sdk.json --sdk`
- `node security.mjs`, `node termination.mjs`, bounded memory and sync HTTP probes
- `docker exec bifrost-pyodide-ddf316c1 uv run python linux_bench.py`
- `cf deploy --dry-run`, isolated `cf deploy`, `node cloud.mjs`, `node cloud-security.mjs`
- `node verify.mjs results/benchmark-cloud.json`, `node verify-sources.mjs`
- Scoped `cf sql query` telemetry, public access disabled and HTTP 404 readback
- Node syntax checks; Docker Python AST parsing without bytecode output; git whitespace validation

No platform/backend/client source changed. Full repo test suites, pyright,
client tsc/lint, CI, PR publication and merge were not run/performed. The runnable
spike harnesses provide the relevant runtime validation, not full platform CI.

Material limits: no genuine run-scoped credential test, full dispatcher/launcher
adapter, DB-backed stale result test, result ACK/recovery proof, per-run isolation,
full Linux pool baseline, precise peak memory or controlled cold-start experiment.
Local SDK success does not prove cloud-to-Bifrost connectivity. Cloud SDK requests
intentionally lack credentials; their HTTP 200 envelope is not workflow success.

Cloud resource left: dedicated private Worker, no public targets, test auth
secret only; preview deleted. Local debug stack and spike container remain
available for reproduction. Cleanup commands are in README. Source was not pushed.

Smallest next experiment: establish cloud-to-isolated-API connectivity with
genuine scoped run credentials, while separately proving a fresh per-run security
boundary. Do not build a full launcher adapter before resolving isolation.
