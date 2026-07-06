# Bifrost Web SDK

The v2 web SDK is the installable `bifrost` package used by
`standalone_v2` Solution apps:

```tsx
import { BifrostProvider, BifrostHeader, useWorkflowMutation } from "bifrost";
```

It is not published to npm. Each Bifrost instance serves its own npm tarball at
`/api/sdk/download`.

## Source And Build

The source of truth is `client/src/lib/app-sdk/`. The installable package entry
is `client/src/lib/app-sdk/index.v2.ts`; it intentionally exports only the
self-contained SDK surface that can run outside the main Bifrost client.

During the API image build, those sources are copied into
`api/src/services/sdk_package/sdk_src/`. At runtime,
`api/src/services/sdk_package/build_sdk.js` bundles that source into one ESM
file, keeping `react`, `react-dom`, and `lucide-react` external peer
dependencies. The `/api/sdk/download` endpoint returns a gzip npm tarball with:

- package name: `bifrost`
- package version: the running Bifrost version coerced to npm semver
- package entry: `dist/index.mjs`
- peer dependencies: React, React DOM, and lucide-react

This mirrors `/api/cli/download`: the SDK is tied to the instance that served
it.

## Local Apps

Scaffolded v2 apps declare the SDK dependency as an instance URL:

```json
{
  "dependencies": {
    "bifrost": "https://your-bifrost.example.com/api/sdk/download"
  }
}
```

That is why local development needs no npm registry publish. `npm install`
downloads the SDK tarball from the Bifrost instance the app targets.

To update a local app's SDK after upgrading Bifrost, reinstall the dependency
from the target instance:

```bash
npm install bifrost@https://your-bifrost.example.com/api/sdk/download
```

Commit the resulting `package-lock.json` change if the app source keeps a lock
file. If the app should point at a different Bifrost environment, change the URL
in `package.json` and run the same install command.

## Deployed Apps

Server-side Solution builds do not call the public SDK download URL and do not
need network access to npm for the SDK itself. `SolutionAppBuilder` generates
the same tarball from the running API image, writes it into the temporary build
directory as `bifrost-sdk.tgz`, and injects:

```json
{
  "dependencies": {
    "bifrost": "file:./bifrost-sdk.tgz"
  }
}
```

Then it runs `npm install` and `vite build`. Redeploying or reinstalling a
Solution after a Bifrost upgrade rebuilds the app against the SDK bundled with
that upgraded instance.

Prebuilt disconnected Solution packages are different: if a bundle ships a
ready `dist/`, the platform skips the server-side Vite build. In that case the
SDK version is whatever the bundle was built with.

## Versioning Contract

There is no independently published web SDK version stream today. The SDK
version follows the Bifrost instance version returned by `shared.version`.

Practical consequences:

- App authors should depend on an instance URL, not `bifrost@latest`.
- SDK changes ship with the Bifrost deployment that contains them.
- Local apps update by reinstalling from `/api/sdk/download`.
- Deployed source-built apps update when the Solution is rebuilt on the target
  instance.
- Breaking SDK surface changes should be treated as Bifrost release changes and
  documented in release notes, because apps consume the SDK from their host
  instance.

## Header Ownership

The platform does not wrap `standalone_v2` apps in a shell. Apps own their
layout. `<BifrostHeader>` is an optional SDK component that an app can compose
when it wants familiar platform chrome.

The header must remain self-contained: no Tailwind, shadcn, or `@/` imports.
It carries its own minimal inline styling so it works in local dev, deployed
apps, and apps that do not include the main Bifrost client CSS.
