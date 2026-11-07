# Fence physical requests before deadline bookkeeping

The companion transport previously called `_start_deadline` before delegating to
the request mux's generation check. A stale or no-longer-owned physical request
could therefore mutate deadline bookkeeping before rejection. The outer routing
guard did not eliminate an ownership change during handoff.

The transport now validates expected generation and actual upstream session
ownership, then rechecks the owner snapshot after that awaited lookup, before
starting deadline bookkeeping. It raises the existing CredentialRouteChanged
exception. The mux retains its separate generation and duplicate-request guards.
No Linux, shared remote-pool, claim, authorization or result API is modified.

CT128 fail-first packet `predeadline-fence-v1`: both stale-generation and
lost-owner tests failed because `_start_deadline` was called. With the fix,
`python -m unittest -v test_private_http_pool test_attempt_deadline
test_workflow_request_mux` passes39 tests in3.375s. A further owner-rotation-during-
authority-lookup regression passes separately in0.008s. Ruff and Pyright pass
for the final implementation and all three new regressions. All unit/quality
commands use the cached image, .5CPU/1GiB/64PIDs, network disabled.

Actual current implementation control:
`ct128-canonical-failure.py --api-prepare --revision 90`, exit0. Genuine upstream
engine/SDK workflows, real API credentials, independent effects, peer Success78,
duplicate reuse, lost-ACK/current-owner reconciliation, stale terminal rejection,
401 revocation, unchanged canonical state/log IDs, accounting2,2,1,0 and cleanup
pass. No owned containers remain running; volumes preserved. Supervisor72.862s
includes setup/prewarm/work/cleanup, not startup latency.

Raw receipt and before/final-test readback hashes are in
`../results/predeadline-fence/`; verify with
`node backend/verify-predeadline-fence.mjs`. The real control packet contains the
two initial regressions; the third test was added/run in a separate packet without
changing implementation. This is local transport/fencing proof, not real cloud
control-link or physical-stop acceptance. Full startup still refuses unsupported
controls, public admission remains disabled, and no cloud calls were made.
