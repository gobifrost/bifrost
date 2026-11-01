# Bifrost Web SDK

The web SDK is the installable `bifrost` package used by independent and
Solution-owned Apps:

```tsx
import { BifrostProvider, BifrostHeader, useWorkflowMutation } from "bifrost";
```

It is not published to npm. Each Bifrost instance serves its own npm tarball at
`/api/sdk/download`.

## Source And Build

The source of truth is `client/src/lib/app-sdk/`. The installable package entry
is `client/src/lib/app-sdk/index.v2.ts`; it intentionally exports only the
self-contained SDK surface that can run outside the main Bifrost client.

The API image builds the complete SDK as a static JavaScript factory. Its
provider, hooks, components, and HTTP/WebSocket clients remain together. The
factory uses the App's own React and JSX runtime, with a separate SDK instance
for each binding. Icons are bundled into the hosted artifact.

The `/api/sdk/download` npm tarball contains a small binding, shared public error
constructors, and self-contained TypeScript declarations. Its React-facing
exports keep the existing `bifrost` imports. `BifrostProvider` obtains the hosted
factory before rendering its children; the platform preloads that factory before
mounting deployed Apps. No App source or Vite target change is required.

The instance serves the current implementation for public SDK API major 1 at
`/api/app-sdk/v1/manifest`. The uncached descriptor selects an immutable,
integrity-checked script under `/api/app-sdk/assets/<sha256>.js`. Shared platform
storage retains advertised scripts across instance rollouts. Opening an App
loads static JavaScript; it never starts npm, an App build, or a dev server.

## Local Apps

App scaffolds deliberately omit an instance-specific SDK dependency from
`package.json`. Both connected development commands install the selected
instance's SDK transiently without rewriting project metadata:

```bash
bifrost app start
bifrost solution start
```

An independent App stores only `BIFROST_API_URL` and `BIFROST_APP_ID` in its
gitignored `.env`. A Solution stores its install binding in `.env` and its
portable ownership metadata in the descriptor and manifests.

For an independent App, start again after removing a stale
`node_modules/bifrost` package. For a Solution App, use the explicit update
command:

```bash
bifrost solution sdk update [path] --app <slug>
```

Do not commit an instance URL or authentication token to `package.json`.

`bifrost solution start` resolves the selected app's portable manifest ID to its
installed ID before starting Vite. Local Python functions and browser API calls
use the CLI user's credentials plus a separate, ten-minute signed development
context for the bound install. The CLI and local proxy renew that context as
needed; it stays in memory and is never included in the browser bundle or saved
credentials. This also works before the selected app's first deployment.

Starting a Solution preview requires current Platform Admin membership, checked
against stored role grants. The context establishes the caller's install and
does not grant workflow powers or bypass table policies, ownership, organization
scope, or another install's inbound rules. An inactive install, changed install
organization, revoked admin grant, or mismatched app context stops access. Use
matching current CLI and server builds for this development path.

In local Python, a table query or count that receives a not-found response raises
an explained error instead of appearing empty. The response does not distinguish
a missing table from a protected one. Individual document reads retain their
normal not-found behavior, and a successful query with no matching rows is empty.

## Deployed Apps

Server-side independent-App and Solution builds do not call the public SDK
download URL and do not need network access to npm for the SDK itself.
`SolutionAppBuilder` is the shared compiler: it generates
the same tarball from the running API image, writes it into the temporary build
directory as `bifrost-sdk.tgz`, and injects:

```json
{
  "dependencies": {
    "bifrost": "file:./bifrost-sdk.tgz"
  }
}
```

Then it runs `npm install` and `vite build`. `bifrost app deploy` uploads source,
stores a sanitized source archive beside the immutable compiled deployment,
and atomically activates it. `bifrost solution deploy` builds the same App shape
as part of full Solution reconciliation and retains the deployed Solution
archive. These retained artifacts make an SDK-only rebuild possible without a
full redeploy; `.env*`, installed dependencies, generated output, caches, and VCS data are
excluded.

A deployed App bundle remains immutable until redeployed or rebuilt. Existing
bundled-SDK Apps need one **Update SDK** build to adopt server delivery. After
migration, compatible hosted SDK fixes take effect on document reload without
changing the App deployment or rebuilding its Solution. Already open documents
retain their loaded code until reload; there is no live hook replacement.

The **App SDK Upgrades** setting controls reviewed automatic binding rebuilds.
It does not pin the hosted implementation. The initial hosted-delivery release
does not declare an automatic migration; update Apps manually after the normal
instance upgrade while that migration is dogfooded. See
[Application SDK updates](application-sdk-update.md).

Prebuilt disconnected Solution packages carry the binding major and build
metadata recorded by their CLI build. The server treats this as the privileged
deploy author's attestation; it does not inspect the JavaScript to prove it.
A legacy prebuilt bundle stays legacy; importing it onto a new instance does
not change its embedded SDK.

## App Mount Lifecycle

Apps keep the module graph exactly as Vite emits it. The host loads the
content-hashed entry through its canonical URL, without per-mount query
parameters, then invokes the entry's exported `mount(mountEl, bootstrap)` for
each visit. `mount` must return a teardown function (normally
`root.unmount()`). Bootstrap includes the basename, API base URL, viewer token,
organization scope, app id, theme, and logout callback. The optional
`solutionId` identifies the owning Solution, or is null for an unowned App.
Forward it to `BifrostProvider` alongside `appId`; Apps can then read it from
`useBifrostContext()` without inferring ownership from URLs or install-specific
constants. This applies to both the live App and its editor preview. It does
not grant access to Solution administration; those routes retain their
platform-administrator checks.

Ordinary signed-in viewers call workflows as themselves. The SDK's
`X-Bifrost-App` header, or the older workflow request's `app_id` field, selects
the App's install; it does not make the App a security principal. A viewer who
can currently open an App can select its own active, sealed Solution without
enabling inbound access. Their organization reach, workflow sharing and
execution permissions, and table/file policies still apply. Runtime credentials
continue to use their verified install provenance for inbound checks.

Solution Detail tabs accept `?tab=overview`, `contents`, `access`,
`configuration`, or `exports`. Links to an owning Solution's backup history
can use `/solutions/${solutionId}?tab=exports`. Missing or unknown tab values
open Overview, and changing tabs preserves other query parameters and browser
history.

Current scaffolds declare the contract in `index.html`:

```html
<meta name="bifrost-app-runtime" content="mount-v1" />
```

The entry registers its reusable factory under its own canonical identity:

```ts
(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });
```

This registration is platform plumbing, not per-mount state transport. The
authoritative bootstrap is passed directly to `mount`.

Entries generated by older scaffolds mounted as a top-level side effect and
read `window.__BIFROST_APP__`. The host retains a first-mount compatibility
path for deployed apps, but same-document re-entry requires `mount-v1`. Migrate
by taking the current scaffold's `index.html` marker and `src/main.tsx`
lifecycle, then moving the existing `<App />` tree into its `mount` function.
Do not add cache-busting queries to the entry or disable Vite code splitting.

## Versioning Contract

The npm binding package version follows the Bifrost instance release version.
Its public SDK API major is a separate identity. The App records the major it
uses, and the instance supplies the latest maintained implementation for that
supported major. The first hosted API major is 1.

An ordinary instance upgrade never silently advances an App's public major.
Genuinely breaking changes to App-facing arguments or results require a new
major and an explicit App code/binding migration. Supported majors use the same
backend and retain their promised behavior, including current security checks.
They are maintained interfaces, not frozen copies of old server protocols.

The App's stored fingerprint describes the binding actually compiled into it.
A hosted implementation or server wire-contract change must not mark a supported
binding stale or automatically enqueue another App build. Raw `authedFetch`
paths and App-authored workflow result schemas still belong to App code; hosting
the SDK does not rewrite arbitrary requests or business schemas.

## Header Ownership

The platform does not wrap V2 Apps in a shell. Apps own their
layout. `<BifrostHeader>` is an optional SDK component that an app can compose
when it wants familiar platform chrome.

The header must remain self-contained: no Tailwind, shadcn, or `@/` imports.
It carries its own minimal inline styling so it works in local dev, deployed
apps, and apps that do not include the main Bifrost client CSS.

## Branding and organizations

Both hooks run inside `BifrostProvider` and use its API URL, authentication,
and organization scope. They return `data`, `loading`, `error`, and `refetch()`.

```tsx
import { useBranding, useOrganizations } from "bifrost";

function OrganizationHeader() {
  const branding = useBranding();
  const organizations = useOrganizations();

  if (branding.loading || organizations.loading) return <p>Loading…</p>;
  if (branding.error || organizations.error) return <p>Unable to load settings.</p>;

  return (
    <header>
      {branding.rectangleLogoUrl && (
        <img src={branding.rectangleLogoUrl} alt={branding.applicationName ?? "Logo"} />
      )}
      <select aria-label="Organization">
        {organizations.data?.map((org) => (
          <option key={org.id} value={org.id}>{org.name}</option>
        ))}
      </select>
      <button style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}>
        Continue
      </button>
    </header>
  );
}
```

`useBranding()` reads public global platform branding and applies the same
light/dark CSS variables as the main client, including `--primary`,
`--primary-foreground`, `--ring`, `--bf-primary-hover`, and
`--bf-activity-gradient`. The provider's `.dark` theme class selects the dark
palette. An unset primary color uses the platform's standard palette. Apps
still own their CSS and layout.

It exposes `squareLogoUrl`, `rectangleLogoUrl`, `applicationName`,
`primaryColor` (the stored color), `palette` (both themes), and `colors` (the
current theme's palette). Logo URLs, including those in `data`, resolve against
the configured API URL so local apps load images from Bifrost. Missing logos
and an unset application name remain null. To consume branding without
changing document styles, use `useBranding({ applyTheme: false })` and apply
`colors.primary`, `colors.primaryForeground`, and `colors.primaryHover`
directly. A failed request is exposed through `error`.

`useOrganizations({ enabled, includeInactive })` reads only the organizations
returned by the existing `/api/organizations` endpoint. It defaults to loading
active organizations. Set `enabled: false` to defer loading, or
`includeInactive: true` to request disabled organizations too. Server-side
permissions remain authoritative; a refused request appears in `error`.

These hooks are compatible SDK additions. Apps need a binding/package update
and an App build when their source starts importing newly added exports. Existing
imports receive compatible implementation fixes from the hosted SDK on reload.
The wire-contract version changes only for an intentional wire break.
