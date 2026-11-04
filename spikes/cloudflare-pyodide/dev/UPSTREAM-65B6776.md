# Upstream reconciliation at 65b6776

The PR GraphQL read reported base `025f6c0` while `git ls-remote origin
refs/heads/main` returned `65b677669755dece507bd0e2f3103e90fb92b360`.
The reported old base merged cleanly; the actual fetched main conflicted in
`api/tests/unit/jobs/consumers/test_workflow_execution_lineage.py`.

Resolution retains the owner-fenced setup-failure regression and upstream's
new queue-refresh helper signature. The production consumer merge retains
upstream `request_queue_position_refresh()` instead of the prior awaited
queue-position publication workaround. No independent queue was added.

CT128 focused verification used source overlays for the merged consumer,
queue_tracker and test file over the previously qualified native API checkout,
with the cached dependency image. This is focused merge regression coverage,
not whole-new-main runtime acceptance. Existing engine/SDK/API receipts keep
their original pins and do not become current-main qualification.

First run: 7 pass, 2 fail because the standalone harness omitted the synthetic
test signing key; Settings raised missing `secret_key` before token creation.
The corrected harness supplies the repository's existing synthetic test key.
Final result: **9 passed in 4.79 seconds**, exit 0. Network none, 0.5 CPU,
1 GiB memory/swap cap, 64 PIDs, 60-second outer bound. Exact packet:
`/opt/bifrost-tests/pyodide-ddf316c1/inbox/merge-65b6776-v1`.

Pytest invocation inside the container:

```sh
pytest -q -o addopts= -o cache_dir=/tmp/pytest-cache \
  --confcutdir=/qualification /qualification/test_workflow_execution_lineage.py
```

Full CI, current-main API/engine qualification and deployed acceptance remain
separate. No cloud deployment, production change, shared remote-pool checkout
edit or human approval bypass was performed.
