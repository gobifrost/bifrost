# Private Solution repositories

Private Solution sources require the GitHub App setup from #1295. They do not
change the instance's workspace Git connection.

1. In **Settings → GitHub → Solution Repositories**, choose the target
   organization or Global scope. Source administration requires GitHub settings
   authority and Solution management permission for that scope.
2. Create an App using the manifest flow, enter an existing App, or explicitly
   reuse the workspace App registration. Complete GitHub authorization and
   installation, then choose a repository. A Solution-only manifest requests
   Contents read. Fetch tokens are restricted to Contents read and one repository
   even when an existing App has broader permissions.
3. Authorize additional repositories through **Add Repository**. Each source
   identifies one App installation and one exact Bifrost scope. Several Solutions
   can share it. Global sources do not apply implicitly to organization installs.
4. When installing from a repository, importing a workspace snapshot, or editing
   a Solution's Git connection, select **Repository Access** and the private
   repository. Choose its subfolder and ref, preview, and confirm the operation.
   Existing Solution install and update permissions still apply.

Repository installs default to your organization. Choose Global explicitly when
using a Global source.

The CLI accepts the non-secret source UUID:

```bash
bifrost solution git sources --org Acme
bifrost solution install-repo https://github.com/acme/solutions \
  --git-source-id <source-uuid> --org Acme --subpath packages/example --ref main
bifrost solution git connect <install-id> https://github.com/acme/solutions \
  --git-source-id <source-uuid> --subpath packages/example --ref main
bifrost solution sync <install-id>
bifrost solution import-workspace --repo https://github.com/acme/solutions \
  --git-source-id <source-uuid> --org Acme --path packages/example --preview
```

Use `--global` to select Global explicitly; omitted scope on source listing and
installation means the caller's home organization. Workspace snapshot imports
retain their existing Global default. Reconnection uses the install's scope.
REST callers pass `git_source_id` with repository coordinates. Existing MCP
tools do not expose these Solution repository operations.

**Disconnect Source** disables future fetches and preserves installed bindings.
It does not revoke the GitHub installation or interrupt accepted, staged installs.
Reconnect explicitly after restoring access. Repository removal, installation
revocation/suspension, changed source generation and rate limits stop a private
fetch; they never select public or personal-token authentication. Changing scope
retains the selected binding until an operator chooses a valid source or Public
Repository explicitly.

App keys and client secrets are encrypted in instance configuration. They are
absent from normal responses, Git remotes, job payloads and portable Solution
packages. A package moved to another instance requires an explicitly configured
source there. Public repositories and ZIP packages retain their existing paths.

Upgrade all APIs, schedulers and executors before enabling private sources and
drain old executors first. New Git sync and update-check jobs use payload version
2; current runners can finish version 1 jobs. An old scheduler's version 1 sweep
cannot enforce private-source authentication, so mixed-version use is unsupported.
No live App registration, installation, permission change or production rollout
is performed by the source contribution's tests.
