# SSO-only login enforcement (admin-exempt) — Design

Status: **Draft** — implements `gobifrost/bifrost#567` per the maintainer's
direction (jackmusick, 2026-08-09): exempt platform admins, no hidden
break-glass page. The issue's hidden `/login/recovery` + forced-MFA path is
**explicitly rejected** below — it is a second auth system to maintain and
audit, and the admin exemption already solves lockout.

## 1. Goal

Let a deployment say "everyone signs in through our IdP" so IdP
deprovisioning and conditional-access policies actually bind: disabling an
employee in the IdP must end their Bifrost access. `prefer SSO`
(auto-redirect, shipped 2026-08-10) is convenience, not enforcement — the
password form still renders and every password/passkey endpoint still works
(`api/src/routers/auth.py:1319` hardcodes `password_login_enabled=True`).

## 2. Non-goals

- Per-org policy. SSO config is already global (`system_configs` with
  `organization_id IS NULL`, `oauth_config_service.py:81`); policy stays
  global until per-org SSO provider config exists.
- Per-user exemption lists. Only one exemption exists: platform admins
  (`is_platform_admin` / `is_superuser`). A named-user list is future work
  and is not in this spec.
- Retroactive session kill on flip. Enabling the policy does not revoke live
  sessions (see §7). Touching the token/session system is out of scope.
- New rate limiters, new audit event types, new tables/migrations.

## 3. Policy model

Extend `OAuthLoginPreference` (`api/src/models/contracts/oauth_config.py:35`,
stored as `value_json` under category `oauth_sso` / key `login_preference` —
no schema migration; old rows decode via pydantic defaults):

```python
password_login_enabled: bool = True   # password methods for non-admins
passkey_login_enabled: bool = True    # passkey methods for non-admins
```

Semantics: both default `True` (current behavior, backward compatible).
When `False`, the method is refused for **non-admins only**; platform admins
always pass. First-run bootstrap (`needs_setup`, no users exist) bypasses both
regardless of values — the first user becomes PlatformAdmin through the
password or passkey setup path as today.

Validators (extend the existing `require_provider_when_enabled` pattern and
`OAuthLoginPreferenceError` / `OAuthProviderPreferenceConflict`):

- Refuse to persist a preference with either flag `False` unless
  `get_available_providers()` (`oauth_config_service.py:411`) is non-empty.
- Refuse `DELETE /api/settings/oauth/{provider}` when it would remove the
  last fully-configured provider while either flag is `False` (extend the
  existing preferred-redirect guard at `oauth_config_service.py:334-347`,
  surfaced as 409 by `oauth_config.py:289-307`).

Wire the existing `PUT /api/settings/oauth/login-preference`
(`oauth_config.py:85-112`, `settings.readwrite`) — no new settings endpoint.

## 4. Enforcement helper (one place)

New methods on `OAuthConfigService`, next to `get_login_preference`:

- `is_password_allowed_for(is_admin: bool) -> bool`
- `is_passkey_allowed_for(is_admin: bool) -> bool`

Both return `True` when the corresponding flag is `True`, when `is_admin` is
true, or when the DB holds no users (bootstrap). Routers call these; no router
reads the flags directly. For unauthenticated login endpoints the caller is
resolved by email (`user_repo.get_by_email`) and `user.is_superuser`
determines `is_admin`; unknown emails take the generic invalid-credentials
path (no user enumeration change).

## 5. Endpoint table (server is the boundary, not the form)

| Surface | Endpoints | Behavior under enforcement (non-admin) |
|---|---|---|
| Password login | `POST /auth/login` (`auth.py:223`), `POST /auth/mfa/login` (`:551`), `POST /auth/mfa/verify` (`:539`, defense-in-depth — its `mfa_token` is only minted by `/auth/login`) | 403 `Password login is disabled (SSO-only). Sign in with SSO.` Reuse the `_refuse_login` audit path (`auth.login.failed`), no new event type |
| Password creation | `POST /auth/register` (`:1142`), `POST /auth/register-from-invite` (`:1747`), `PUT /profile/password` (`profile.py:262`) | 403 for the password path. `register` keeps the bootstrap exception (no users → allowed). `register-from-invite` rejects the password variant only; the invite passkey variants (`:1773`, `:1795`) stay open. `profile/password` rejects both set and change for non-admins |
| Passkey login | `POST /auth/passkeys/authenticate/options` + `/verify` (`passkeys.py:145`, `:178`) | 403 `Passkey login is disabled (SSO-only).` |
| Passkey enrollment | `POST /auth/passkeys/register/options` + `/verify` (`:60`, `:92`), `POST /auth/setup/passkey/*` (`auth.py:1553`, `:1614`) | Authenticated enrollment: 403 for non-admins. Setup endpoints: bootstrap-only already (no users) → unchanged, always allowed |
| Invite passkey | `POST /auth/register-from-invite/passkey/*` (`:1773`, `:1795`) | Allowed — SSO-compatible onboarding, no password created |

Explicitly **not** gated: device flow (`POST /auth/device/code|token`,
`POST /auth/device/authorize|deny` — authorization happens behind an existing
authenticated session at `auth.py:1682`, so CLI device login is SSO-compatible;
the CLI's direct password grant `api/bifrost/cli.py:466-508` is covered by the
`/auth/login` gate and surfaces its 403). No forgot-password/email-reset flow
exists — nothing to gate. OAuth callback/link/unlink unchanged (the unlink
guard at `oauth_sso.py:419-428` already prevents the inverse lockout).

## 6. Status + login UI

- `AuthStatusResponse` (`contracts/auth.py:23`): keep `password_login_enabled`,
  add `passkey_login_enabled: bool = True`. `GET /auth/status`
  (`auth.py:1270`) returns real values from the helper (anonymous view —
  global policy, not per-viewer; admins see the same page).
- `Login.tsx`: consume both flags (today it ignores `password_login_enabled`
  and gates passkey only on `supportsPasskeys()` at `:490`). Under SSO-only:
  SSO buttons stay primary; password form and passkey button collapse into a
  visible (not hidden, not linked-from-nowhere) `<details>`-style
  "Platform admin sign-in" expander. Server still rejects non-admins who try
  it — the expander is a UX de-emphasis, not the boundary. Suppression of the
  `passkey_auto_attempted` auto-trigger (`Login.tsx:255-290`) follows the
  passkey flag. `auto_redirect_to_sso` interplay unchanged (one-shot,
  back-button safe).
- Settings → Authentication (`client/src/pages/settings/OAuth.tsx`,
  `PreferredSignIn.tsx`): add a "Login policy" card with two switches
  (password / passkey for non-admins), a locked-on "Platform admins are always
  exempt — this is the recovery path" notice, and helper text disabling the
  switches when zero providers are configured. Save through the existing
  `useUpdateOAuthLoginPreference` mutation.

## 7. Sessions, rate limits, audit (decisions, not code)

- Sessions: enabling the policy does **not** revoke live password-derived
  sessions (`POST /auth/refresh` keeps working). Document the manual
  follow-up in the settings copy and admin docs: self-revoke via
  `POST /auth/revoke-all`, per-user via `POST /auth/admin/revoke-user`
  (`auth.py:1040-1076`). No new revocation job.
- Rate limits: reuse `auth_limiter` (10/min) and `mfa_limiter` (5/min) in
  `api/src/core/rate_limit.py:178-180`. No `breakglass_limiter` (deleted with
  the hidden page).
- Audit: rejections flow through existing `auth.login.failed` /
  `emit_audit` call sites; policy changes are covered by existing settings
  audit. No new event type. (The issue's "break-glass must be loud" audit
  requirement dissolves — there is no break-glass login; admin password
  logins already audit as normal logins.)

## 8. Tests (required by repo rules, kept to the surface)

- Unit (`api/tests/unit/`): preference defaults/back-compat (old
  `value_json` without new keys decodes to both-`True`); validators
  (enable-without-provider raises; delete-last-provider raises);
  helper truth table (admin × flags × bootstrap).
- E2E (`api/tests/e2e/`, hitting the API directly, not the hidden form):
  each row of §5 — non-admin 403, admin success, bootstrap success;
  `GET /auth/status` reflects flags; `DELETE` last provider → 409 while
  enforced; `PUT login-preference` without provider → 400. CLI device flow
  under SSO-only (browser SSO authorization → token) still passes.
- Client: vitest for `Login.tsx` gating + admin expander and the new
  settings card (`OAuth.test.tsx`, `Login.test.tsx` patterns); one Playwright
  happy-path spec for SSO-only login page rendering. Regenerate types
  (`generate:types` against the debug stack) — `AuthStatusResponse` and
  `OAuthLoginPreference` changes trip `test_dto_flags.py` and the contract
  fingerprint test by design; refresh the fingerprint and confirm no
  `MIN_CLI_VERSION` bump is needed (additive bools with defaults).
- Docs: settings/authentication page — enabling SSO-only, "admins are the
  recovery path" (no secret URL), SSO-outage runbook (admin signs in with
  password/passkey, fixes provider, optionally revokes user sessions).

## 9. Rollout (two PRs)

1. **Policy + status**: contract fields, service helper + validators,
   delete-guard extension, `/auth/status` wiring, settings UI card.
2. **Enforcement + UI + docs/tests**: §5 gates, `Login.tsx` gating +
   admin expander + passkey auto-trigger suppression, e2e/vitest/Playwright,
   docs. The merge queue is the full-suite gate; local proof is the §5 e2e
   plus the DTO/contract tripwires.

## 10. Why not the issue's break-glass URL

A non-default `/login/recovery` needs its own rate limit, its own
MFA-always-required code path (contradicting `auth.py:295-297` where
`mfa_enabled=false` means no second factor), its own audit special-casing,
and its own first-run/setup interplay — all to replicate what admin
exemption gives for free. The failure mode it guards (IdP outage) is handled
better by "admins can always use password/passkey" because there is nothing
secret to discover, nothing unlinked from `/auth/status`, and nothing that
rots from disuse. If a future deployment cannot tolerate *any* password path
(even for admins), that is a new proposal, not this one.
