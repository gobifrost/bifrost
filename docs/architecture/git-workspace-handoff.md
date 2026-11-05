# Git workspace handoff

Git PlatformJobs can run on different executors. A successful local Commit must
remain available when a later Sync, Diff or conflict-resolution job uses another
checkout. The published `_repo/` tree remains the application source of truth;
unpublished commits and conflicts are retained separately.

Under the existing deployment repository lock, a workspace operation reads a
conditional current-state pointer at
`_workspace_sync_checkpoints/local-state.json`. It restores the referenced
immutable checkpoint if another executor advanced the state, then runs the Git
operation. Mutating operations persist the resulting checkpoint and conditionally
replace the pointer before acknowledging success. Read-only operations hydrate
without publishing a new checkpoint. The original editor-write baseline remains
attached to the snapshot, including conflicts and ambiguous publication retries.

Checkpoint publication verifies downloaded bytes and a second local fingerprint.
An uncertain conditional PUT retains both snapshots because the service may
already have accepted the pointer. A local receipt beside each checkout detects
unacknowledged local changes before hydration; such work is refused rather than
overwritten. A filesystem installation is not atomic, so cancellation is deferred
until its worker thread finishes and releases staging and the repository lock.
Preflight uses an isolated read-only snapshot and preserves pending local work.

Workspace jobs use payload version 2. Older runners must not consume the new
payload. Executor rollout must preserve this runner-version fence. Defaults keep
the checkout at `/tmp/git`; `BIFROST_GIT_WORK_DIR` permits separate executor cache
paths. A checkout and its sibling receipt share one cache lifecycle. The test
harness places both beneath its worktree-specific fixture mount.

## Operating limits and review

- The existing Redis lock has a 300-second lease and is not renewed here. Git,
  network and checkpoint verification must fit it; large repositories require
  separate performance qualification. This change does not establish a size SLA.
- Immutable checkpoints can remain after crashes or ambiguous requests. Existing
  retry checkpoints and current-pointer references must be considered before any
  manual deletion. No orphan collector or automatic retention policy is added.
- Checkpoints include Git internals and may contain the configured remote URL.
  They require the same trusted-platform storage access as existing Git state.
  Disconnect sanitizes published, checkpointed and durable local Git configs;
  it must fail if it cannot verify the referenced snapshot. This is a sensitive
  credential-lifecycle surface requiring normal maintainer review.
- Conflict state stays in private checkpoints; normal application reads use
  `_repo/`. Source freshness checks, conditional writes and refusal of unsaved
  local changes remain enforced. Do not treat refusal as successful publication.

The standalone prerequisite uses the existing S3 transport and AWS CLI mirror.
It introduces no optional storage provider or Azure dependency. Relevant tests
cover independent executor handoffs, stale/warm checkouts, conflicts, uncertain
pointer publication, checkpoint corruption, read-only preservation, repeated
cancellation and checkout/receipt restoration.
