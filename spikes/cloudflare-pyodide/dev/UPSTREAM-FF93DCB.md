# Reconciliation against upstream ff93dcb

Merged upstream main ff93dcb47f9aac602021b170a8b5eb8497490bb2 into the
experimental feature branch. The only textual conflict was lineage-test imports;
retained PendingExecution and upstream's _legacy_unattended_pending_actor.
Upstream unattended-actor tests and this branch's setup-failure owner fencing
remain present. Automatic consumer/process-pool merges preserve owner-fenced
failure reporting and parent-owned execution identity binding.

CT128 bifrost-pyodide-test-01, packet inbox/merge-ff93-v1: full merged api/src,
shared and bifrost mounted read-only over cached native image
bifrost-test-api-dev:bifrost-test-7ddfac36. Four selected files (lineage,
stream capture, optional context, runtime metadata): 28 passed in 3.37s, exit0.
Command: pytest -q -o addopts= -o asyncio_mode=auto
-o cache_dir=/tmp/pytest-cache --confcutdir=/qualification /qualification
Ruff passes consumer, process pool and conflicted lineage test.
Network disabled, 0.5 CPU, 1 GiB memory, 64 PIDs, 90-second test bound.
Disposable test containers removed automatically; existing dev stack preserved.

Dependencies remain the previously cached image: this is focused native source
compatibility evidence, not whole-main dependency qualification or full-suite CI.
No laptop Docker, cloud invocation/deployment, production changes, admission
change or new physical-termination claim. Packaged Pyodide pins and closed cloud
artifact retain their earlier provenance. Hosted checks/review remain required.