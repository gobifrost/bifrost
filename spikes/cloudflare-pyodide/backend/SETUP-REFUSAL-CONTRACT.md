# Setup failure is not yet a refusal acknowledgement

The source-pinned consumer's generic `process_message` exception handler is not
equivalent to normal terminal consumption. It awaits `update_execution` without
passing `owner_worker_id`, ignores its Boolean result, then publishes failure,
history and a sync result. Both an accepted and a refused write return `None`.
An exception from persistence propagates before those publications.

`setup-refusal-probe.py` executes that actual consumer with mocked persistence,
publication, credentials and pool routing. CT128 returned:

| Persistence outcome | Return | Owner argument | Failure/history/sync publications |
|---|---|---|---|
| Accepted | None | Absent | 1 / 1 / 1 |
| Refused | None | Absent | 1 / 1 / 1 |
| Raises ConnectionError | Exception | Absent | 0 / 0 / 0 |

This is a control-flow observation, **not** a live PostgreSQL race, credential
revocation test or real SDK execution. The repository source independently shows
that `owner_worker_id` adds the run-status/worker-claim predicates, while the
remote-session fence is separate. A refused write can therefore not be inferred
to have produced the notified durable failure. No production incident is claimed.

The normal `_process_failure` path instead passes the attempt owner, checks
`written`, rolls back/drops a fenced result, and publishes after commit. Normal
transport acknowledgement means canonical consumption, including an intentional
stale-result discard; it does not always mean a new terminal row was written.
The generic setup path has not established that same consumption contract.

## Consequence for the companion

Do not clear the retained pre-launch refusal because `process_message` returned
or a failure event appeared. Preserve the original failed receipt and no-send
evidence; never re-execute it to discover what happened. A disconnect or generic
error also cannot establish no-send evidence.

The smallest proposed shared change is to give setup failures the existing
terminal-consumption semantics: attempt and remote-owner fencing, explicit
handling of a refused write, durable commit before notifications, and cleanup
only after consumption. It must preserve stale-discard semantics, credential,
claim and accounting obligations, plus Linux behavior. It should extend the
existing consumer/recovery boundary, not create a separate queue or direct
provider database update. Shared ownership/review reconciliation remains required
before changing that path; the earlier denied coordination receipt is preserved
in PRELAUNCH-RETENTION.md and has not been retried.

## Reproduction and limits

Native source: 1d33150eb376b71f8a81b427920eda1b0447f829. Exact consumer,
repository and fencing hashes plus raw synthetic output are preserved under
`../results/setup-refusal/`. The cached Docker image remains
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Run `python setup-refusal-probe.py` in the existing CT128 qualification mount,
network disabled, .5CPU/1GiB/64PIDs, 60-second outer bound. Ruff/Pyright passed.
Verify source binding with `node backend/verify-setup-refusal.mjs`.

No shared platform source changed, no cloud action, no access change, no new
dependency or runtime setting. Public/untrusted admission remains disabled;
physical cancellation/isolation and full startup remain independently open.

Follow-on: SETUP-FAILURE-FIX.md now fixes the generic handler on the isolated
contribution branch, with owner-fenced writes and no notifications after refused
persistence. The original probe above remains pinned to its historical source.
Explicit companion receipt acknowledgement/retirement is still separate work.
