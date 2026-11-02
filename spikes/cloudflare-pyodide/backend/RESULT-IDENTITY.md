# Parent-owned workflow result identity

The smallest shared physical boundary must bind a returned result to its existing canonical run before invoking the completion callback. A route/handle is selected by the trusted parent; the result body must not select a different execution.

At source `1017cca1795540f75b9781d87137822a4a42d685`, `ExecutionInfo.attach_transport_metadata` overwrote `sync` but left a result-supplied `execution_id` untouched. Both native `ProcessPoolManager._handle_result` and `ThinRemoteProcessPool._handle_result` use this helper. Deliberately injecting another ID at that boundary caused the remote callback to receive it; an omitted ID remained absent. This is a source-boundary characterization with transport doubles, not evidence that the current trusted Linux launcher actually emits a forged result or that a deployed exploit occurred.

The fix binds both `execution_id` and `sync` to the existing parent-owned `ExecutionInfo`. It copies the result instead of mutating the received object. Normal matching-ID Linux results retain their meaning. No wire/PID/launcher/placement/recovery change, new queue, guest-chosen principal or production deployment is involved. Service result handling is unchanged.

## Actual CT128 evidence

The six-case initial reproducer failed four forged/missing-ID cases and passed both correct-ID controls. Its exact fixture SHA256 was `7cb922662fdfe7ac20462279472ce138264447f7e0743be04d620c6e33c7c433`. The final test adds three native-handler cases, for nine direct regressions. With the source fix, all 65 selected tests pass, with zero failures, errors or skips. Targeted Ruff and Pyright pass.

An additional real PostgreSQL/Redis test connects `ThinRemoteProcessPool` to the actual `WorkflowExecutionConsumer`: both runs have claims owned by the same worker, route A returns a body naming B, and duplicate delivery follows. A becomes Success/value70; B remains Running with no result and its original claim. The new test passes independently (1 case in 0.87 seconds), with Ruff/Pyright on all three changed source/test files. No SDK, provider or physical runtime is mocked into a compatibility claim: the transport double supplies only the deliberate terminal event, while the actual canonical callback and database perform acceptance.

```sh
./test.sh tests/unit/execution/test_result_transport_identity.py \
  tests/unit/execution/test_process_pool.py::TestProcessPoolManagerResultHandling \
  tests/unit/execution/test_process_pool.py::TestResultTransportMetadata \
  tests/unit/dev/test_thin_remote_pool.py \
  tests/unit/dev/test_pyodide_attempt_journal.py -q

# Additional canonical two-run regression (included in that file for future runs):
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py::test_remote_result_cannot_complete_another_run_claimed_by_the_same_worker -q
```

Raw before/after JUnit and command/source receipts: `../results/result-identity/`. The base checkout was the exact source above, plus the overlays hashed in `identity-after.json` and `identity-canonical.json`. Cached dependency image readback: `sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`. The current baseline helper expects the expanded nine-case test (six expected baseline failures); the preserved initial six-case receipt is historical and is not rewritten.

Guest: pve-t340 CT128, 2 CPU/8 GiB, no swap. Exclusive checkout `/opt/bifrost-tests/pyodide-ddf316c1/result-identity-1017cca`, Compose project `bifrost-test-8e9f191a`; PostgreSQL/Redis/storage and ports remain isolated from dev project `bifrost-debug-074217d4`. The test runner is capped at 768 MiB/1 CPU; service memory/CPU limits are recorded through the deployment overlay hash. The supervisor has a 600-second wall bound. Only this test project's containers were stopped; its volumes/locks and the running dev stack were preserved. No laptop Docker, Cloudflare calls, spending, credentials, runtime security settings or production resources changed.

## Source coordination and remaining guarantees

Before editing, the known Containers worktree was read-only inspected twice: no overlapping edits existed in `process_pool.py` or the remote directory. The helper's last source change was `68139d0a7`; no file-specific lock/release was established by the passive coordinator evidence. Idle status was not treated as a release or new authorization. The patch was narrowed under the existing implementation instruction to this metadata helper on the isolated contribution branch. The separate owner's worktree and all remote control files remain untouched; any broader runtime-handle/placement change still needs specific coordination.

This closes one source-level result identity invariant. It does not implement opaque handle negotiation, canonical Cloudflare dispatch, duplicate/recovery semantics beyond the existing tests, or physical CPU termination. The existing journal's strict result-identity checks remain intact. Arbitrary untrusted admission remains disabled.
