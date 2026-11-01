# Actual integration SDK qualification in guest Pyodide

The real upstream `bifrost.integrations.get()` ran under official Pyodide 314.0.6 on Node in CT128 against the isolated upstream API. It returned synthetic organization configuration `qualification_value=42`; the same SDK call targeting the peer organization received 403. The workflow then upserted its document, independently read back as **70**. This closes a distinct integration-SDK gap beyond the earlier generic config read.

The actual canonical consumer issued the run identity, claimed the attempt, consumed the terminal result and reconciled a lost acknowledgement with one provider launch. Post-terminal credential use returned 401. The existing credential-redaction probe remained `[REDACTED]`. This case does **not** qualify OAuth exchange, encrypted integration secrets or their registration/redaction; its integration config is deliberately a synthetic integer.

## Exact execution and limits

- API source `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`; prepared unchanged runtime source `785b560b9973020e1c13401383e1a73a87a54940`.
- Additional fixture packet hashes, full commands, exit codes and canonical observations: `results/node-integration-resume/success.json`. SHA256 `7b5c96ff7c651a8145afc1826fa57c3f9f9ac07a7a5a9388d0ce993c957dc1eb`, independently matched after transfer.
- Supervisor `backend/ct128-node-integration-qualification.py`; actual test command is its Docker Compose test-runner invocation of `python /qualification/canonical-local.py --node-pyodide --integration-probe`.
- Existing CT128, 2 CPU / 4 GiB, no swap. Provider 1 CPU / 1 GiB / 128 PIDs, unchanged isolated test network; wall guard 900 seconds, disk-used guard 24 GiB. Single job; 60.444 seconds total observed supervisor time. No cloud invocation, new login, resource expansion or laptop Docker.
- All owned project containers stopped, no running containers after cleanup. Existing volumes, failed packets and receipts retained.

Offline verification:

```sh
node spikes/cloudflare-pyodide/backend/verify-node-integration.mjs
node spikes/cloudflare-pyodide/backend/verify-node-sdk.mjs
```

The earlier SDK verifier now reads its exact published fixture revision rather than comparing historical evidence to a subsequently edited working file.

## Bounded experiment log

| Hypothesis/change | Command | Observation | Next action |
|---|---|---|---|
| Existing config/table role and org config row suffice | v1 supervisor, `--integration-probe` | Workflow success assertion failed; original source and sanitized diagnostic retained | Inspect upstream service permission and mapping semantics |
| Grant required `integrations.read` plus `secrets.read` only on fixture organization; create actual org mapping | v2 supervisor, same bounded command | Actual integration API 200, peer call 403, document 70, canonical reconciliation passed | Qualify encrypted synthetic integration secret handling separately |

The v1 diagnostic does not retain the provider's inner error, so it establishes fixture failure rather than proving a particular HTTP status. Source review found both setup defects: `shared.sdk_integrations.get_sdk_integration_dict` requires `integrations.read`, and without an organization mapping the service selects integration defaults rather than this org config. No production authorization or engine code was changed.

The supervisor refuses existing output paths and active containers; future execution needs a new reviewed exclusive run/packet name. Do not overwrite these receipts to rerun. This Node qualification does not establish Cloudflare SDK execution, fresh isolate security or physical CPU termination. Execution admission remains disabled. The exact workerd bundle's recorded 403 and the cloud hold remain unchanged.

## Subsequent encrypted synthetic secret case

`backend/ct128-node-secret-qualification.py` executed the same real canonical/API/SDK path with `--integration-probe --integration-secret-probe`. The fixture generated a fresh synthetic value in memory, encrypted it with upstream `encrypt_secret` into a test-only SECRET config row and declared a secret schema key. The workflow verified the decrypted value against a pinned SHA256 digest, then intentionally returned it and logged it. The unchanged SDK registered the secret and the unchanged engine redacted both outputs. No production secret, OAuth credential, encryption-key change or persistent access was involved.

Receipt: `results/node-secret-resume/success.json`, SHA256 `4a4523b7bc77a8d26a0635e870dac17614f6177df9bfc3d8a912dbe4ef4d471b`, matched on transfer. Exact tested additional canonical fixture SHA256 `5ebf9c12d5727807922921f31767e09d8c5be0e71a6b064cf4a9b89604a0c9cf`; API/runtime/provider pins and resource bounds are unchanged from above. Supervisor elapsed 61.566 seconds, all commands exit 0. One launch, two persisted logs, terminal credential 401, lost-ACK reconciliation and cleanup passed. No running owned containers remain; volumes retained. An after-completion Docker memory read returned 0B for the stopped container and is **not a memory measurement**.

```sh
node spikes/cloudflare-pyodide/backend/verify-node-secret.mjs
node --test spikes/cloudflare-pyodide/probe-io.test.mjs
```

The eight diagnostic boundary regressions also pass. The trusted fixture's existing raw-secret guard withheld evidence if the synthetic plaintext survived serialized provider/canonical output; the preserved successful receipt contains only redacted probe values and a digest in pinned source. This case adds encrypted integration-secret result/structured-log coverage. It does not establish OAuth refresh, exception/stdout redaction, transformed-secret protection, comprehensive memory isolation or Cloudflare execution. Next useful independent qualification is actual WASM memory usage for this dependency closure; Node RSS must not be presented as Cloudflare isolate memory.
