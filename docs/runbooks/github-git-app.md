# Connect Git Sync With a GitHub App

The App authenticates Bifrost's existing Git repository connection. Each
deployment owns its registration and credentials. Existing PAT connections keep
working until an administrator explicitly migrates them.

## Before Setup

- Configure `BIFROST_PUBLIC_URL` as the deployment's external HTTPS origin,
  without a path or query. Loopback HTTP is supported for local development.
- Keep query strings for `/settings/github` out of any additional ingress or
  reverse-proxy logs. The shipped client suppresses callback access logs and
  sends no-store and no-referrer headers.
- Upgrade API and workers together. New `workspace.git` jobs use payload version
  3; older executors cannot process App-aware jobs. Requeue an older unbound job
  after migrating its connection.
- Use a Bifrost account authorized to change platform Git settings and a GitHub
  account allowed to register/install an App for the repository owner. GitHub
  organization policies can require an owner's approval.
- Have an existing GitHub repository. Create it in GitHub before setup if needed.

## Create and Connect

1. Open **Settings > GitHub > Connect With a GitHub App**.
2. Enter the GitHub owner and App name. Choose Organization or Personal Account.
   A private App can be installed only on its owner's account. Choose **Allow
   Installation on Other GitHub Accounts** only when that is required; it creates
   a public App owned by the selected operator account.
3. Select **Allow Updating GitHub Actions Workflow Files** only when this
   connection needs to push `.github/workflows` changes. The normal permission
   request is Contents read/write; Metadata read is implicit. No webhook events
   or issue/PR/check administration permissions are requested.
4. Choose **Create GitHub App** and approve the registration in GitHub. Bifrost
   exchanges the manifest code and stores the returned key/client secret
   encrypted on the server. Normal setup requires no copied PEM or secret.
5. Choose **Authorize With GitHub**. Install the App if needed, grant access to
   the selected repositories, then return and **Refresh Installations**. User
   authorization and App installation are separate GitHub approvals.
6. Select the installation and repository, then **Use Selected Repository**.
   Bifrost verifies both installation access and the connecting GitHub user's
   access. Pending approval does not count as a connected installation.
7. For a new connection, select the branch, choose **Review connection**, and
   confirm the existing publish/start-from-remote/reconcile strategy. Progress
   uses the normal notifications and shared PlatformJob status.

Git sync uses short-lived installation tokens restricted to that repository.
Background jobs refresh credentials without retaining the connecting person's
GitHub user token. Branch protections and the existing Git commit-author
configuration still apply.

## Migrate an Existing PAT Connection

Complete App setup and select the repository already connected to Bifrost.
Successful verification replaces the credential while retaining the repository,
branch, workspace and history. The replaced PAT is removed from Bifrost's saved
configuration. App failures never fall back to it. Revoke a PAT in GitHub
separately when it is no longer needed elsewhere.

Selecting a different repository is refused while the current connection is
active. Review and explicitly disconnect that connection before changing its
repository. The App does not grant repository-creation or branch-protection
bypass permissions.

## Resume or Recover

- **Registration Needs Review:** inspect the chosen owner's GitHub App settings.
  GitHub may have created the App even if the return page or code conversion was
  interrupted. Do not create another registration until the outcome is known.
  Use **Use Existing App** to resume with its App ID, client ID, private key and
  client secret. If no App was created, explicitly choose the reviewed new
  registration action.
- **Authorization expired or cancelled:** authorize again. Saved App credentials
  can be reused without registering another App; the active Git connection is
  preserved during setup.
- **No installation/repository:** check the GitHub account, organization-owner
  approval, App suspension and selected-repository access, then refresh. A
  callback's installation ID is not proof of access.
- **Access denied during Git sync:** restore the installation/repository grant
  in GitHub and retry the normal operation. Removed or revoked access is an
  error, not an empty repository or a request to use a personal token.
- **Rate limited:** wait for the installation's quota/cooldown. More tokens do
  not create more installation quota.
- **Workflow-file push denied:** review the App's Workflows write permission in
  GitHub before explicitly changing it. Bifrost does not upgrade permissions
  automatically.

Disconnecting clears the local connection. It does not uninstall, delete or
transfer the App in GitHub. App keys and client secrets must remain out of
workspace files, portable exports, logs and normal integration SDK reads.

Provider and callback behavior is covered with synthetic fixtures in local/CI
tests. A real registration/install/pull/push rehearsal requires a separately
authorized disposable GitHub App and repository; local tests do not prove a
production installation is configured.
