# Codex Gateway User Onboarding

This runbook is for onboarding a Bifrost user to Bifrost Codex Gateway with the
user's own ChatGPT/Codex identity. Do not use a shared upstream ChatGPT account
unless it is intentionally labeled and governed as a service account.

## Goal

The target mapping is:

```text
Bifrost user -> Bifrost session/key -> same user's ChatGPT/Codex account
```

If that mapping is ambiguous, stop and fix the identity or token state before
issuing gateway keys.

## Requirements

- The user can sign in to ChatGPT/Codex with their own account.
- Device-code sign-in is allowed for the ChatGPT Business workspace.
- The user has access to the Codex CLI on their workstation.
- The Bifrost platform is reachable over the private/dev endpoint.

## Connect

1. Sign in to the Bifrost instance as the user being onboarded.
2. Confirm the gateway route is available:

   ```powershell
   bifrost api GET /api/codex-gateway/oauth/status
   ```

   A new user should see `connected: false`.

3. Ask Bifrost for the preferred connect method:

   ```powershell
   bifrost api POST /api/codex-gateway/oauth/connect '{}'
   ```

   The response is a `CodexGatewayOAuthConnectResponse`. Use its
   `client_command` value as the recommended user command. The expected
   `preferred_method` is `device_code`, and the expected `client_command` is:

   ```text
   codex login --device-auth
   ```

4. On the user's workstation, run:

   ```powershell
   codex login --device-auth
   ```

5. After Codex login succeeds, import the user's Codex auth cache into the
   Bifrost token vault by sending the cache JSON as `auth_cache`:

   ```powershell
   $authCache = Get-Content "$env:USERPROFILE\.codex\auth.json" -Raw | ConvertFrom-Json
   bifrost api POST /api/codex-gateway/oauth/import-auth-cache (@{ auth_cache = $authCache } | ConvertTo-Json -Depth 20)
   ```

6. Verify the connection:

   ```powershell
   bifrost api GET /api/codex-gateway/oauth/status
   ```

   The response should show `connected: true` and safe account metadata only.
   It must not include access tokens, refresh tokens, ID tokens, or raw cache
   content.

## Disconnect

Disconnect a user's upstream Codex account during offboarding, account rotation,
or a failed onboarding attempt:

```powershell
bifrost api DELETE /api/codex-gateway/oauth
```

Then verify:

```powershell
bifrost api GET /api/codex-gateway/oauth/status
```

The response should show `connected: false`.

## Security Notes

- Treat `auth.json` like a password. Do not paste it into tickets, chat, issue
  comments, or logs.
- Import only over HTTPS/private-network access to the Bifrost platform.
- Bifrost encrypts imported token material at rest and never returns upstream
  tokens in API responses.
- Audit events record provider/workspace metadata and refresh-token presence,
  not raw upstream tokens or raw upstream email.
- If a user loses ChatGPT/Codex entitlement, disconnect and reconnect after the
  entitlement is restored.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `/oauth/status` returns `404` | The platform API image does not include the Codex Gateway OAuth routes yet. |
| Import returns `400` | Confirm the JSON body is `{ "auth_cache": <auth-json-object> }` and contains an access or refresh token. |
| Status stays disconnected after import | Confirm the request was made as the same Bifrost user being onboarded. |
| Another user cannot see the connection | Expected. Upstream Codex accounts are user-scoped. |
| A token appears in output | Stop onboarding and treat it as a security bug. |
