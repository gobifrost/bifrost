# SSO-Only Login Policy

Excluded users and organizations can use password and passkey sign-in. Keep at
least one active platform admin excluded for recovery. Restricted sign-in methods
remain available through **Excluded Account Sign-In** on the normal login page.

## Enabling SSO-only

1. Configure at least one SSO provider under **Settings → Authentication**.
   The policy refuses to save while zero providers are configured, and the
   last fully-configured provider cannot be removed while a method is off.
2. In the **Login Policy** card, turn off **Password Sign-In** and/or
   **Passkey Sign-In**, then **Save Login Policy**.
3. Verify in a private window: SSO buttons stay primary, the other methods
   sit behind **Excluded Account Sign-In**.

Notes:

- **Exclude From SSO Enforcement** has the same meaning on the user and
  organization screens: on means excluded. The central **SSO Exclusions**
  selections use the same policy. At least one active platform admin must remain
  excluded.
- Enabling the policy does **not** revoke live sessions. Password-derived
  sessions keep working until they expire. To force re-authentication,
  each user can sign out everywhere via `POST /auth/revoke-all`, or an
  admin can revoke one user via `POST /auth/admin/revoke-user`.

## SSO-outage runbook

If the identity provider is unreachable:

1. An excluded platform admin signs in through **Excluded Account Sign-In**
   expander with a password or passkey. No special URL or flag is needed.
2. Fix or disable the broken provider under **Settings → Authentication**.
   If SSO cannot be restored quickly, re-enable password sign-in in the
   **Login Policy** card so users can work while the provider is down.
3. Optionally revoke user sessions created before the fix (same endpoints
   as above) if any were established against a misbehaving provider.
