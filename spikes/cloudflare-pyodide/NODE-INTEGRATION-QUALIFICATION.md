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
