# Authorized local qualification

This is a manual disposable-test-stack qualification, not a dispatcher or a
deployable authenticated ingress. Do not deploy `wrangler.authorized-local.jsonc`.
The entry accepts fixture-selected manifests and bearer tokens; it relies on
the isolated local driver, not production admission. No cloud calls occur.

`results/authorized-local-liveness.json` records process exit 0, actual upstream
`run_execution`, pinned workflow/helper loading, config SDK read, table SDK
create/upsert, independent API readback (70), structured logs and credential
redaction. Cross-tenant access returns 403. The same credential returns 200
while its fixture execution is running and 401 after the fixture becomes
terminal; it also returns 401 after teardown. This terminal transition is a
test fixture mutation, **not canonical result acceptance**.

The canonical issuer creates protocol-2 claims-based credentials. Only the
run credential crosses into the fresh local Python Dynamic Worker. Database,
signing and parent credentials do not. Fixture setup/teardown uses upstream
`committed_world`; config setup/cleanup uses API calls, and audit teardown
removes only exact fixture IDs. Worker and scheduler must remain stopped during
this manual fixture so they cannot race its synthetic execution record.

## Reproduce in the existing isolated tools environment

From `spikes/cloudflare-pyodide`, with the repository Docker test stack booted
through `./test.sh stack up`, API running and its worker/scheduler stopped:

```powershell
py -3 backend/prepare-runtime.py --manifest authorized-local-source.json
docker cp backend/. bifrost-pyodide-ddf316c1:/spike/backend/
docker exec bifrost-pyodide-ddf316c1 /root/.local/share/uv/tools/workers-py/bin/python backend/pack-engine.py --current-runtime --manifest authorized-local-runtime-manifest.json
docker network connect bifrost-test-79c5c14e_default bifrost-pyodide-ddf316c1
docker exec -w /spike/backend bifrost-pyodide-ddf316c1 npx wrangler dev --config wrangler.authorized-local.jsonc --port 8799 --ip 0.0.0.0
```

In a second terminal, run `node backend/run-authorized-local.mjs`. This wrapper
is intentionally pinned to this worktree's existing test-tools container and
test project. Read it before adapting to another worktree. It captures Docker's
real process exit separately from stderr and writes only the fixture's checked,
redacted report. Run credentials are never arguments or evidence files.
Copy the generated runtime manifest back from the tools container if retaining
a new evidence set; preserve historical receipts rather than overwriting them.

Stop that Wrangler process and always disconnect the temporary network:

```powershell
docker network disconnect bifrost-test-79c5c14e_default bifrost-pyodide-ddf316c1
node --test probe-io.test.mjs backend/module-response.test.mjs backend/resolution-response.test.mjs
```

Only loose workspace modules are admitted. Solution storage-path remapping is
explicitly rejected. Missing/changed hashes fail closed, while upstream auth
challenges and not-found resolution retain their semantics.

Canonical dispatch ownership, duplicate/terminal reporting, reconciliation and
physical termination remain unproven. No fresh cloud test with these credentials
was attempted. Previously observed catchable provider CPU failure still prevents
first-class arbitrary untrusted execution. No native dependency build was needed
for this increment: the failures were proxy path and fixture cleanup contracts.
