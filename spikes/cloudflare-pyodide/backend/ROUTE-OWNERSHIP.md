# Retained routes belong to one physical worker and launcher

The companion AttemptJournal checked whether a supplied RemoteOwnerLease was current, but keyed retained results only by route ID and execution ID. Another independently valid worker session could reuse those identifiers to retrieve the result or invoke terminal reconciliation. Canonical transaction fencing is still required downstream; a valid session for some worker must not authorize adopting another launcher's transport receipt.

Each route now records the canonical worker_id and launcher_id from its first dispatch. Duplicate dispatch, normal terminal reconciliation and retained-result reconciliation require both identities to match, before returning the result, invoking a consumer or returning an acknowledged tombstone. Owner IDs and session generations are deliberately not pinned: upstream can rotate the API owner/session while reconnecting the same physical launcher. Existing live-session and transactional owner/attempt checks remain in force.

This is a companion-only repair. It adds no queue, provider profile, opaque-handle protocol, credential issuance or platform schema. Linux remote-control files and production resources are unchanged. Inventory remains a trusted parent-side diagnostic, not a public results endpoint. Physical stop remains unproved and execution admission stays disabled.

## Reproduce

Use the existing isolated CT128 allocation and cached API image. `ct128-route-owner.py` runs the six focused unittest cases in a network-disabled1CPU/512MiB container, then uses the existing disposable test project and repository test entrypoint:

```sh
python -m unittest -v test_attempt_journal_owners
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py -q
```

The focused cases double only live-session validation, explicitly assuming both callers pass it. Five cross-owner cases fail before the repair; the same-launcher reconnect control passes. With the repair they cover foreign worker, foreign launcher, terminal consumption, acknowledged tombstone replay, retained result after response loss, and legitimate reconnect/one-launch/one-ACK cleanup.

The PostgreSQL regression creates two canonical ExecutionWorker rows and independently acquires current remote-owner sessions using upstream ownership.acquire_remote_owner. It verifies both sessions are live, denies the foreign route retrieval and ACK without calling acceptance, rotates the legitimate session and then reconciles once. Physical launch/result collection remains a deterministic callback, not a Cloudflare or Linux process execution test.

The initial baseline shell wrapper did not propagate unittest's failing exit status through PowerShell expansion; its retained raw output explicitly contains five failures and one pass. The committed supervisor captures subprocess return codes directly and fails closed. No baseline was rerun to manufacture a clean wrapper result.

The supervisor preserves prior checkout overlays and raw logs, applies exact hashed test/journal inputs, limits the run to600s, and stops only its owned test project. Existing volumes, dev services and uncertain locks are preserved. Results belong under `results/route-ownership/`; they must not be presented as deployed canonical execution or physical-isolation acceptance.

Observed result: six focused cases passed in0.017s; all38 journal PostgreSQL/Redis regressions passed with no failures/errors/skips. Focused Ruff passed for the journal, tests and supervisor. Total supervisor elapsed115.249s includes stack lifecycle, not workflow latency. Cached image ID is recorded in the receipt; the owned project had no running containers after cleanup. No laptop Docker or cloud resources were used. Verify source/evidence binding with `node backend/verify-route-ownership.mjs`.
