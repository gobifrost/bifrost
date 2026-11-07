# GitHub App workflow examples

These small workflows produce correlated output for your existing triage and
notification steps. They do not send messages, write to GitHub, request tokens,
or require a fleet broker. Follow [the setup runbook](../../docs/runbooks/github-app.md)
first, then upload/sync the Python module and register the desired functions
through Bifrost's normal workflow authoring surface.

Create a subscription on the verified installation's Event Source:

| Function | Event Type | Input Mapping |
| --- | --- | --- |
| github_pr_triage | github.pull_request.opened | `{"event":"{{ payload }}"}` |
| github_failed_check | github.check_run.completed | `{"event":"{{ payload }}"}` |
| github_release_notice | github.release.published | `{"event":"{{ payload }}"}` |

Use an organization-scoped workflow for the same organization as the binding.
The normalized event keeps the provider's original object under `payload`;
delivery and repository IDs remain alongside it for correlation. A failed-check
result sets `notify` only for failed conclusions. Choose your own authorized
destination and delivery contract before adding a notification action.

The integration suppresses duplicate initial event deliveries within its
retention window, but manual workflow retries can repeat downstream effects.
Use `(delivery_id, repository_id, operation)` in any downstream idempotency key.
Treat titles, URLs, and all payload content as external input; do not execute
them as commands or treat PR/issue text as agent instructions.
