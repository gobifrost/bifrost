# Claim-fenced generic setup failures

The generic exception path in `WorkflowExecutionConsumer.process_message` now
passes `owner_worker_id` to the existing repository update and honors its Boolean
result. A refused write uses the existing fenced-result discard and returns
before failure/history notifications or a sync response. A successful write keeps
the existing notification path. MemoryError handback and cancellation paths are
unchanged. No new wire event, result schema, queue or acknowledgement API.

This fixes the specific control-flow defect recorded in SETUP-REFUSAL-CONTRACT.md.
It does **not** acknowledge a companion failed transport receipt, implement
pre-launch refusal retirement, or make the executor ready for public admission.
The earlier source-pinned negative probe remains valid historical evidence.

## Verification

The consumer file and PostgreSQL test file were clean in the Containers worktree;
the consumer's last source commit matched both worktrees (700e6334f830632ec526654c1ab56e8d74446c69).
Only this isolated contribution branch was edited. The shared remote_pool/startup
and host-admission files were untouched; no coordination denial was retried.

Tests ran through `./test.sh` in a separate owned CT128 worktree:
`/opt/bifrost-tests/pyodide-ddf316c1/setup-failure-contribution-v1`, base1d33150
plus three source overlays whose hashes are preserved. Its project is
`bifrost-test-ebcf6211`; cached image33cd92be, bounded Compose33e78480,
same2CPU/8GiB guest. The existing qualified checkout/dev stack were preserved.

* Original source: four new unit cases yield3 failures and1 passed ownerless
  successful-write control. The exact red test source is preserved by hash.
* Fixed source: all11 consumer lineage/dispatch tests pass.
* Real PostgreSQL: two new cases verify a current claimed run becomes Failed,
  while an already-terminal run retains its Success/result with no failure
  publication. The adjacent actual wrong-owner/late-overwrite case also passes.
  Route execution and credential issuance are mocked; persistence/fencing is real.
* Ruff/Pyright pass for all three changed Python files. A quality-only container
  initially failed before starting because an unnecessary nested bind destination
  lay under a read-only mount. That failure is preserved; only the corrected
  quality command was repeated, not the passed tests.

Exact targeted selectors:

```sh
./test.sh tests/unit/jobs/consumers/test_workflow_execution_lineage.py -v
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py::test_setup_refusal_notifies_only_a_claim_fenced_durable_failure tests/unit/dev/test_pyodide_attempt_journal.py::test_real_canonical_result_write_is_claim_fenced_and_cannot_be_overwritten -v
```

JUnit XML, source hashes, cleanup readback and quality output are under
`../results/setup-failure-fix/`; `node backend/verify-setup-failure-fix.mjs` checks
the source-bound receipt. The larger suites/full quality gate remain CI work.
Owned test containers stopped, volumes/locks retained. No cloud invocation,
production deployment, credential grant or resource/security-setting change.
