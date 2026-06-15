# Application Name branding setting (+ logo size guidance)

**Date:** 2026-06-15
**Status:** Approved design, pending implementation plan

## Summary

Add a customizable **Application Name** to the existing branding system so the
product's proper name (today hardcoded as `Bifrost` / `Bifrost Integrations`)
can be white-labeled on the auth screens, the browser tab title, and the header
fallback wordmark. Bundle in a small fix to the logo upload UI that adds
recommended pixel dimensions and corrects an inaccurate aspect-ratio label.

This is an **additive** change to the existing branding infrastructure (DB
`branding` table, `BrandingRepository`, the `/api/branding` router, the
`useBranding` hook, and the Branding settings page). No new architecture.

## Motivation

White-labeling currently covers logos, primary color, and renamable nouns
(`app` / `agent` / `form` via `BrandingTerminology`), but the product's own
name is hardcoded in several user-facing places. A reseller deploying Bifrost
under their own brand cannot replace "Bifrost" on the login screen.

## Part A — Logo size guidance (bundled fix)

File: `client/src/pages/settings/Branding.tsx`.

The current labels show only an aspect ratio, and the rectangle one is
inaccurate — the rectangle logo renders **height-constrained** in the header
(`Logo.tsx`), not as a 16:9 box.

| Element | Today | After |
|---------|-------|-------|
| Square logo label | `Square Logo (1:1 ratio)` | `Square Logo (1:1 ratio)` + helper text `Recommended: 512×512 px` |
| Rectangle logo label | `Rectangle Logo (16:9 ratio)` | `Horizontal Logo (~4:1 ratio)` + helper text `Recommended: 800×200 px` |

Helper text uses the existing `text-xs text-muted-foreground` style. The
"Rectangle" → "Horizontal" rename is approved.

The `BrandingSettings.rectangle_logo_url` field description in
`api/src/models/contracts/common.py` (`"...for headers, 16:9 ratio"`) is also
corrected to `"...for headers, ~4:1 ratio"`. The DB field name
(`rectangle_logo_*`) and API logo-type path (`/logo/rectangle`) stay unchanged —
only display copy changes.

## Part B — Application Name field

### Data model

Single nullable field, mirroring the existing `primary_color` pattern.

- **DB** (`api/src/models/orm/branding.py`, `GlobalBranding`):
  `application_name: Mapped[str | None] = mapped_column(String(40), default=None)`.
  New Alembic migration adds the column.
- **Contracts** (`api/src/models/contracts/common.py`):
  - `BrandingSettings.application_name: str | None` — the **raw** stored value
    (`None` when unset). The "Bifrost" fallback is applied only in the frontend
    helper, never baked into the API response.
  - `BrandingUpdateRequest.application_name: str | None = Field(default=None, min_length=1, max_length=40)`.
- **Repository** (`api/src/repositories/branding.py`, `set_branding`): accept and
  persist `application_name` using the same non-destructive update pattern as
  `primary_color` (only overwrite when the caller passes a value; the dedicated
  clear path sets it back to `None`).
- **Router** (`api/src/routers/branding.py`): thread `application_name` through
  the `GET` response builder and the `PUT` handler. The existing reset/delete
  paths set it to `None`.

`application_name` is **empty by default**. When empty, the UI falls back to the
literal `"Bifrost"` (short) / `"Bifrost Integrations"` (long) exactly as today,
so existing installs see zero change.

### Frontend plumbing

- `useBranding` (`client/src/hooks/useBranding.ts`) and `OrgScopeContext`
  (`client/src/contexts/OrgScopeContext.tsx`) expose `applicationName: string | null`
  alongside the existing logo/color/terminology values. `/api/branding` is a
  **public** endpoint (no auth required), so the name is available on the
  pre-auth login/setup screens.
- New helper `useApplicationName()` returns `applicationName ?? "Bifrost"`.
  A single fallback constant `DEFAULT_APPLICATION_NAME = "Bifrost"` lives next to
  it so the literal is defined once. (Spots that historically used the long form
  "Bifrost Integrations" now use the single custom name when set, and the long
  literal only as their hardcoded fallback when unset — see table.)

### Replacement sites (in scope)

| Surface | File(s) | Today | After (name set) | After (name unset) |
|---------|---------|-------|------------------|--------------------|
| Login h1 | `pages/Login.tsx` | `Bifrost` | `{name}` | `Bifrost` |
| Setup title | `pages/Setup.tsx` | `Welcome to Bifrost` | `Welcome to {name}` | `Welcome to Bifrost` |
| Register / AuthTransition / MFA `alt` + any visible name | `pages/Register.tsx`, `components/auth/AuthTransition.tsx`, `pages/MFASetup.tsx` | `Bifrost` | `{name}` | `Bifrost` |
| Header fallback wordmark | `components/branding/Logo.tsx` | `Bifrost Integrations` | `{name}` | `Bifrost Integrations` |
| Browser tab title | `lib/useDocumentChrome.ts` | `… | Bifrost`, base `Bifrost Integrations` | `… | {name}`, base `{name}` | unchanged literals |

`alt` attributes that currently read `"Bifrost"` become `{name}` for consistency
(screen-reader accuracy), using the same fallback.

### Explicitly out of scope

- `client/index.html` static `<title>` stays `Bifrost Integrations` — it is the
  first paint before JS/branding loads. `useDocumentChrome` overwrites the tab
  title once branding resolves, so a branded install shows the custom name a beat
  after load. (Approved.)
- `VersionUpdateBanner` and secondary mentions on MCP / developer / security
  pages keep the literal "Bifrost". Not part of this change.
- `Logo.tsx`'s `defaultLogo` image (`/logo.svg`) is governed by the existing
  logo-upload feature, not this field.

### Settings UI

In `client/src/pages/settings/Branding.tsx`, add an **Application Name** text
input at the top of the first branding card (above the logos card), with a
clear/reset affordance that mirrors the existing primary-color control. Label:
`Application Name`; helper text: `Shown on the login screen, browser tab, and
header. Leave blank to use the default.`

## Testing

- **Backend unit** (`api/tests/unit/`): round-trip `application_name` through
  `BrandingRepository.set_branding` (set, update, clear-to-None); 40-char max and
  1-char min validation on `BrandingUpdateRequest`.
- **Backend e2e** (`api/tests/e2e/`): `PUT /api/branding` with `application_name`
  then `GET` returns it; reset/delete clears it.
- **Frontend vitest**: `useApplicationName()` returns the name when set and
  `"Bifrost"` when null; `useDocumentChrome` test updated to assert the suffix
  uses the resolved name (extend existing `useDocumentChrome.test.ts`).
- **Type regen**: `npm run generate:types` after the contract change.

## Risks / notes

- The `/api/branding` payload is public; `application_name` is non-sensitive
  display copy, so exposing it pre-auth is intended.
- Existing `useDocumentChrome.test.ts` hardcodes `"… | Bifrost"` /
  `"Bifrost Integrations"`; those assertions move to the resolved-name path with
  the literal as the unset fallback.
