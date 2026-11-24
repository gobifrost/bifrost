# Delivery

Use the repository's [work tracking guide](../../../../docs/work-tracking.md) for issue hierarchy and ownership, and AGENTS.md for worktrees and verification.

## Set up authorized work

1. Search for existing tracking and ownership. Reuse a matching issue. For non-trivial work without one, offer to track it and honor a request to skip tracking. Show the proposed title, body, labels, and assignee before filing a new issue, then file on approval. Use the relevant issue template. Batch triage ends with recommendations and drafts unless the user authorizes filing or implementation. Change labels and assignments only as requested.
2. Work in an isolated worktree based on current `origin/main`, or continue in the worktree already assigned to the task. Use the issue number in a new branch name when available. Check whether the branch is missing migrations from main before operating its stack. Follow `bifrost-debug` when a live development instance is needed.
3. Before opening or queueing a PR, verify the changed surface and commit the candidate on a clean branch containing current `origin/main`. Follow the current repository preflight rules. For skill changes, run `bash scripts/sync-codex-skills.sh`, `python3 scripts/check_skill_mirrors.py`, and `./test.sh tests/unit/test_skill_appendix_fresh.py`. For CLI, DTO or public skill changes, also run the affected contract and generated-appendix checks in AGENTS.md.

## Open and follow a PR

- Link the issue with `Fixes #N` or `Closes #N` only when the PR completes it. Use `Part of #N` for partial work. Include focused test evidence and identify sensitive paths for review. Follow the repo's Product Updates rules for user-visible changes.
- Use T3 PR tools when available and link each PR or issue taken on to the thread. Otherwise use GitHub tooling.
- With merge authorization, use the native merge queue for protected `main` (`gh pr merge <N> --repo gobifrost/bifrost` when using the CLI). The queue selects the strategy and reruns integration checks. If protections are absent, wait for the required checks and reviews before exercising merge authorization.
- Follow review comments, check results, and merge state together. In T3, use `watch_pull_request` when authorized work is pending on checks or reviews. Investigate failed jobs, fix the cause on the same branch, and verify the updated candidate before requeueing. Check queue state after a push or a queue failure.
- After merge, verify the terminal state and offer cleanup of the worktree and branch. Preserve any unmerged local work.
