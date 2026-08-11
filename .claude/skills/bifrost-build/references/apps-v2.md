# v2 Solution Apps

Use `standalone_v2` for every new Bifrost app. A v2 app is a normal React/TypeScript/Vite project owned by a Solution and deployed from source.

Read `app-quality.md` before designing user-visible UI and `web-sdk-v2.md` when using Bifrost data or workflows.

## Scaffold first

From a bound Solution root:

```bash
bifrost solution scaffold-app operations
```

Use the scaffolded structure instead of recreating it from memory:

```text
apps/operations/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── components.json
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── index.css
    ├── components/
    ├── lib/
    └── pages/
functions/
.bifrost/apps.yaml
bifrost.solution.yaml
```

Keep the scaffold's `index.html` and reusable `mount()` lifecycle in `src/main.tsx`. It registers the app module, receives per-mount URL/token/org/app/theme bootstrap data, wraps the app in `BifrostProvider`, and mounts the router at the host-provided basename. Hand-written legacy entrypoints often work locally and fail when the platform mounts the app more than once.

The scaffold creates the app manifest entry. App metadata, source path, logo path, and dependencies deploy from the Solution; do not create or update the managed app record live.

## Import boundaries

The `bifrost` package contains only the v2 runtime SDK:

```tsx
import {
  BifrostHeader,
  useTable,
  useWorkflowMutation,
  useWorkflowQuery,
} from "bifrost";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
```

- Import React APIs from `react`.
- Import routing from `react-router-dom`.
- Import icons from `lucide-react`.
- Import shadcn components from the app's `@/components/ui/*` files.
- Import user components by local path.
- Never copy v1 examples that import React, router, icons, or UI components from `bifrost`.

Use `../generated/web-sdk-surface.md` to confirm SDK exports. Do not guess an export.

## App-to-workflow contract

Reference Solution workflows with portable workspace-root-relative locators:

```tsx
const query = useWorkflowQuery("functions/list_clients.py::run", { status: "active" });
const save = useWorkflowMutation("functions/save_client.py::run");
```

Prefer `useWorkflowQuery` for reads that run on mount and `useWorkflowMutation` for explicit actions. Guard nullable initial data, render the hook's error, and prevent duplicate mutations while loading.

Enforce authorization and integration access in the workflow. Client-side hiding is a convenience, not a security boundary. Keep config secrets and OAuth tokens out of browser code.

## Dependencies and components

Use the app's normal package manifest for browser dependencies. Install shadcn components into the app and import their local source; the platform does not inject the host client's private component tree into v2 apps.

Prefer dependencies that are actively maintained, browser-safe, and compatible with the app's React version. Do not introduce a library for behavior that the platform or browser already provides simply because an old example used it.

## Local development

Run the Solution's connected development server:

```bash
bifrost solution start operations
```

Open the proxy origin printed by the command, not Vite's internal port. App and workflow code hot-reload behind the same origin.

The preview uses the bound install and selected live instance. Reads and writes to tables, managed files, configs, integrations, and fallback resources use real instance state. Seed disposable data or obtain permission before exercising production-sensitive mutations.

## Completion checks

Before offering deploy:

- run the app's tests, type check, and production build;
- exercise local workflows through the app, not only as isolated Python functions;
- inspect every changed route and important state using `app-quality.md`;
- verify `supportsTheme` only after both themes pass;
- confirm the app and supporting entities share compatible organization/access/roles;
- verify portable workflow refs and no environment-specific UUIDs leaked into source;
- ask the user to preview the printed local origin.
