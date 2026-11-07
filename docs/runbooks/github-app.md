# GitHub App connections

GitHub Apps are optional. Each Bifrost deployment uses an operator-owned
GitHub.com registration. Existing Settings → GitHub PAT repository sync stays
independent. The App integration does not register/install an App, change GitHub
permissions, transfer ownership, or migrate Git sync for you.

## Manual setup

1. Register an App under your own GitHub account or organization. Record its
   numeric App ID and generate an RSA private key of at least 2048 bits. Store
   the downloaded key in your approved secret-management system.
2. Set an HTTPS webhook URL on this deployment:
   `https://<your-bifrost-host>/api/hooks/github-app/<numeric-app-id>`.
   Generate a high-entropy webhook secret of at least 32 characters and enter
   the same value in GitHub and Bifrost. Keep TLS certificate verification on.
3. Select only the events you need and the corresponding read permission:

   | GitHub Events | Required Repository Permission |
   | --- | --- |
   | repository | Metadata: Read |
   | pull_request | Pull Requests: Read |
   | issues | Issues: Read |
   | check_run, check_suite | Checks: Read |
   | release | Contents: Read |

   Installation lifecycle events are control-plane notifications. This version
   does not request user authorization or repository write permissions.
4. In Settings → GitHub App, enter the App ID, PEM private key, and webhook
   secret, then choose **Verify And Save App**. Deployment settings write and
   Secrets read permission are required. The backend verifies the App identity
   before storing either secret encrypted; neither is returned on reads.
5. Install the App on the intended GitHub account and selected repositories.
   Copy the installation ID from GitHub. Select the Bifrost organization and
   choose **Verify And Bind Installation**. This requires deployment management
   and Secrets read permission. An installation can belong to one Bifrost org;
   an org can have multiple installations. A redirect or an ID alone does not
   authorize a binding.
6. Choose **Refresh And Recover**. The shared PlatformJob and notification
   system reports progress. A binding stays stale until GitHub verifies its
   account, permissions, and repository inventory. Automatic reconciliation
   runs every five minutes when the App is configured.
7. Use the binding's Event Source in the existing Events UI to subscribe a
   workflow. Match a normalized event type such as
   `github.pull_request.opened`, `github.check_run.completed`, or
   `github.release.published`. Pass the normalized event with an input mapping
   `{"event": "{{ payload }}"}`. See [the examples](../../examples/github-app/README.md).

The event contains provider, delivery ID, event, action, installation ID,
repository ID, and the original parsed GitHub payload. Bifrost's event envelope
and raw-body handling remain shared with other event sources. Tenant identity
comes from the stored binding, not a field in the webhook body.

## Health and recovery

Stored credentials prove configuration, not connectivity. Check inventory
verification time, binding status, granted/available event permissions, last
delivery, quota cooldown, and the last reconciliation result. Inventory older
than 15 minutes cannot admit new repository workflow events. Configure the
canonical scheduler/executors before enabling this integration.

GitHub does not automatically retry failed webhooks. Reconciliation refreshes
installation inventory, resumes accepted pending EventDelivery records, and
requests redelivery of failed provider deliveries not already accepted locally.
It inspects up to 1,000 provider attempts per sweep, looking back at most three
days. The first sweep looks back 24 hours; later sweeps overlap by ten minutes
to catch late-visible history and bounded clock skew. A missed retention window or scan
limit is shown as a recovery gap; reconciling current state does not reconstruct
lost event history.

An administrative redelivery request is recorded before sending. A response
lost after sending remains uncertain and is never automatically sent again.
Review GitHub's delivery history and local delivery/event correlation before
requesting any additional redelivery through GitHub. No personal credentials
are selected after an App denial, outage, or quota failure.

Delivery identity is unique by App and `X-GitHub-Delivery` for 30 days. A duplicate
with conflicting content is rejected. Raw accepted bodies follow the shared
event-retention policy; identity tombstones survive event deletion until their
own retention expires. Redelivery outside the supported window can run again.
Workflows with external side effects still need their own idempotency contract;
the integration does not promise exactly-once external actions.

Installation quota is shared by its tokens. Replica-wide token/inventory
coalescing, bounded request concurrency and provider cooldown reduce load;
issuing more tokens does not increase that budget. Expired/revoked access,
permission errors, and rate limits are distinct operator failures. Inventory is
bounded at 5,000 repositories per installation and fails visibly above that
limit rather than returning a partial inventory.

The installation's existing Event Source rate limit applies to new deliveries;
duplicates remain recoverable during a cooldown. Tenant Events writers can
manage subscriptions, activity and rate settings. Binding ownership, adapter
configuration and source deletion stay under GitHub App administration.

## Rotation, disconnect, and ownership

Leave a credential field empty to retain it when saving. Enter replacement
material to rotate it. Coordinate webhook-secret replacement with GitHub;
there is no unsigned mode or old-secret compatibility path. Saving invalidates
binding/cache generations and requires fresh inventory verification. Requests
already sent to GitHub cannot be recalled, but late results cannot restore a
changed binding. Remove the old key from the GitHub registration after verifying
the replacement, through your own approved operational process.

**Disconnect Installation** fences its access and clears inventory. Rebinding a
disconnected installation creates a fresh event source, preserving the old
organization's event history and subscriptions. **Disconnect App** clears stored
secrets and disconnects its bindings. These actions affect Bifrost; GitHub
uninstallation and key revocation are separate operator actions.

App credentials, bindings and their event sources stay outside workspace Git
manifests. Git sync does not recreate or delete them. Recreate subscriptions
against the destination deployment's explicitly verified binding when moving
workflow source between deployments.

GitHub [supports transferring a registration](https://docs.github.com/en/apps/maintaining-github-apps/transferring-ownership-of-a-github-app)
to a user or organization, including
a later handoff to `gobifrost`. That does not by itself transfer backend hosting,
domains, secret custody, or Bifrost bindings. Plan and verify those separately.
GitHub can warn that a transfer will uninstall the App from the current
account. Review that consequence and verify installation continuity and each
binding before relying on the transferred registration.
Manifest onboarding, write-back, and App-based Git sync are later work.

## Verification boundaries

Automated local tests use synthetic provider responses and isolated databases,
cache, API and worker processes. They do not establish live GitHub qualification.
Use a separately authorized test registration and installation to qualify the
real provider before enabling a production connection. Keys, JWTs, tokens, raw
webhook payloads, and installation-wide delivery history must not be copied into
logs, screenshots, source, exports, or tenant-visible diagnostic responses.
