# Journal custom-awaitable review

PR1161 comments 4449160478 and 4449160462 report CodeQL Statement has no effect
at api/tests/unit/dev/test_pyodide_attempt_journal.py:89 and
backend/test_attempt_journal_owners.py:54 (alerts 1887 and 1886).
Both statements intentionally await AttemptResult.__await__, which shields the
underlying collection task. Removing them would discard collection/error proof.

The successful await now checks the exact result envelope; unittest setup also
checks its result. Expected-exception cases explicitly discard the awaited value
with `_ = await receipt`. Exceptions still propagate into the existing raises
checks. No production code, exception semantics or runtime policy changed.

Hypothesis/change/test/result: make custom receipt consumption explicit, then run
`timeout 650 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/journal-await-v1/ct128-journal-await.py`
inside the existing assigned CT128. Actual PostgreSQL journal cases: 38 passed,
zero failures/errors/skips. Separate network-disabled owner-guard unit tests:
six passed. Ruff passed for both changed test files and the qualification helper.
The supervisor completed in 101.924 seconds. Existing bounded Compose/project
bifrost-test-5dc8d94e was stopped before and after; dev stack/volumes preserved,
guest unchanged at 2 CPU / 8 GiB, no cloud calls or heavy laptop Docker.

results/journal-await/ retains the exact commands, source hashes, JUnit and unit
output. The existing owned checkout retains the prior ACK correction overlays;
the new helper verifies the journal test was unmodified before staging. Its
exclusive inbox/run paths are not reusable. Broader suites and full type check
were not rerun for these test-only expression changes; normal CI remains required.

Alert-state readback through the existing GitHub credential returned HTTP 403
for both alerts: "You are not authorized to read code scanning alerts."
The CLI additionally suggested an admin:repo_hook scope. No scope refresh,
alternate credential, identity or route was attempted. This records the source
fix and local tests, not provider-side alert closure, dismissal or acceptance.
Current-head CodeQL/maintainer review must confirm the final disposition.

Offline evidence check:

```sh
node spikes/cloudflare-pyodide/backend/verify-journal-await.mjs
```

The deployed physical-stop counterexample remains unchanged; arbitrary untrusted
execution admission stays disabled.
