# Apps Reference

Bifrost apps are React + TypeScript (TSX) applications built with Vite. The current app model is `standalone_v2` (for Solution apps) and the legacy inline v1 model. This file covers v2-first.

For the web SDK hooks used inside apps, see `references/web-sdk-v2.md`. For import rules and platform export names, see `references/import-patterns.md` and `references/platform-api.md`.

---

## v2 App Structure (standalone_v2)

A `standalone_v2` app is a normal React project that mounts at `/apps/{slug}`. It owns its own `createRoot` and `<BrowserRouter>`.

```
my-solution/
  apps/my-app/            # what `bifrost solution scaffold-app` writes
    package.json          # vite + react deps (root of the app)
    vite.config.ts        # Tailwind v4 via @tailwindcss/vite
    tsconfig.json
    index.html            # loads /src/main.tsx
    src/                  # ALL app source lives under src/
      main.tsx            # createRoot + <BifrostProvider> (keep as scaffolded)
      App.tsx             # <BrowserRouter> + <Routes>
      index.css           # @import "tailwindcss" + shadcn token layer
      lib/utils.ts        # cn() helper
      components/         # your app components (e.g. ui/* shadcn components)
      pages/              # your route pages
  functions/
    hello.py              # @workflow decorated function (solution root, not under the app)
  .bifrost/files.yaml     # optional Solution runtime file-location declarations
  bifrost.solution.yaml   # Solution descriptor
```

The app's `main.tsx` wraps the tree in `<BifrostProvider>`. **Use the `main.tsx` that `bifrost solution scaffold-app` writes verbatim — do not hand-roll it or copy a snippet from memory.** It exports a reusable `mount(mountEl, bootstrap)` lifecycle. The platform loads the content-hashed entry at its canonical Vite URL once, then passes per-mount bootstrap data directly to `mount`; local Vite dev calls the same lifecycle with env values. `index.html` declares `<meta name="bifrost-app-runtime" content="mount-v1">`. The scaffold's actual shape (abridged here; let `scaffold-app` write it):

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { BifrostProvider } from "bifrost";
import App from "./App";
import "./index.css";

interface Bootstrap {
  basename: string;
  baseUrl: string;
  token: string;
  orgScope: string | null;
  appId: string | null;
  theme: "light" | "dark";
  onLogout: () => void;
}

export function mount(mountEl: HTMLElement, boot: Bootstrap) {
  const root = createRoot(mountEl);
  root.render(
    <StrictMode>
      <BifrostProvider {...boot} supportsTheme>
        <BrowserRouter basename={boot.basename}><App /></BrowserRouter>
      </BifrostProvider>
    </StrictMode>,
  );
  return () => root.unmount();
}

(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });

if (import.meta.env.DEV) {
  mount(document.getElementById("root")!, {
    basename: "/",
    baseUrl: import.meta.env.VITE_BIFROST_API_URL ?? window.location.origin,
    token: import.meta.env.VITE_BIFROST_TOKEN ?? "",
    orgScope: import.meta.env.VITE_BIFROST_ORG_ID ?? null,
    appId: import.meta.env.VITE_BIFROST_APP_ID ?? null,
    theme: "light",
    onLogout: () => window.location.assign("/login"),
  });
}
```

Older side-effect entries that read `window.__BIFROST_APP__` are a migration-only compatibility path. They can load once through a canonical URL, but cannot be re-executed within the same document without violating ES-module identity. Re-scaffold or port `index.html` and `main.tsx` to `mount-v1`; do not add query strings to the entry, inline dynamic imports, or remove `React.lazy()`.

Imports in a **v2 standalone app** (this is NOT the v1 surface — see the warning below):
- **SDK hooks/providers ONLY come from `"bifrost"`**: `import { BifrostProvider, BifrostHeader, useWorkflowQuery, useWorkflowMutation, useWorkflow, useTable, useInfiniteTable, tables, files, useFiles } from "bifrost"`. That export list is the whole SDK — see `references/web-sdk-v2.md` / `generated/web-sdk-surface.md`. Nothing else lives in `"bifrost"`.
- **shadcn/ui components** (Button, Card, Dialog, …): `import { Button } from "@/components/ui/button"` — NOT from `"bifrost"`.
- **React** (useState, lazy, Suspense, …): `import { useState, lazy, Suspense } from "react"` — NOT from `"bifrost"`.
- **Router**: `import { Link, Outlet, useNavigate } from "react-router-dom"` — NOT from `"bifrost"`.
- **Icons**: `import { Phone, Mail } from "lucide-react"`.
- **User components**: `import ClientCard from "./components/ClientCard"`.

> **v1 vs v2 import surface — the #1 confusion.** The legacy v1 *inline* model injected ~40 shadcn components + React + react-router + lucide all under a single `from "bifrost"` import (via `globalThis.__bifrost_platform`). **v2 standalone apps do NOT do this** — only the SDK hooks/providers come from `"bifrost"`; everything else has its real home (`@/components/ui/*`, `react`, `react-router-dom`, `lucide-react`). Do not add new apps as v1. `references/import-patterns.md` covers the v1 inline rules (still relevant for existing inline apps); `references/platform-api.md` lists the v1 platform export names.

---

## App CLI Commands

```bash
bifrost apps create --name dashboard --slug dashboard --deps @package.json
bifrost apps update <ref> --name "Operations Dashboard"
bifrost apps set-deps <ref> --deps '{"recharts": "^2.12.0"}'
bifrost apps replace <ref> --repo-path apps/new-name
bifrost apps delete <ref>
```

See `references/entities.md` for semantics.

---

## Resilience and Design Rules

### 1. Loading & Error States

Every data-fetching page must render a distinct UI for loading and error states.

```tsx
import { useWorkflowQuery } from "bifrost";
import { Loader2 } from "lucide-react";

export default function ClientsPage() {
  const { data, loading, error } = useWorkflowQuery<{ items: Item[] }>("functions/list_clients.py::run");

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }
  if (error) {
    return <div className="text-destructive p-4">{error.message ?? "Failed to load"}</div>;
  }

  return <ul>{data?.items?.map((c) => <li key={c.id}>{c.name}</li>)}</ul>;
}
```

### 2. Null-safe Data Access

Workflow results are `null` until the first run completes. Use optional chaining and nullish coalescing everywhere.

```tsx
// YES — never throws if data / items are null
const count = data?.items?.length ?? 0;
data?.items?.map((c) => <Row key={c.id} name={c.name ?? "Unknown"} />);

// NO — throws on first render
data.items.map(...)       // TypeError: Cannot read properties of null
data.items.length
```

### 3. Mutation Error Handling

Every `useWorkflowMutation` must handle errors with user feedback and leave the user on the current page unless the mutation succeeds.

```tsx
import { useWorkflowMutation } from "bifrost";
import { Button } from "@/components/ui/button";

export default function SaveButton({ payload }: { payload: unknown }) {
  const { mutate, loading } = useWorkflowMutation("functions/save.py::save");

  async function onClick() {
    try {
      await mutate(payload);
      // success — navigate or update state here
    } catch (e) {
      alert(e instanceof Error ? e.message : "Save failed");
      // stay on page — user can retry
    }
  }

  return <Button onClick={onClick} disabled={loading}>Save</Button>;
}
```

Execution IDs: `mutate()` resolves with the final workflow result. If you need to navigate to an execution page, watch `executionId` in a `useEffect` — it becomes non-null after the execution is created and before the result arrives.

### 4. Dependency Safety (Hooks)

`useEffect` / `useCallback` / `useMemo` dependency arrays must include every referenced external value. Never disable `exhaustive-deps` without understanding why.

```tsx
// WRONG — missing `q` dep, stale if parent changes q
useEffect(() => { setLocal(q); }, []);

// RIGHT
useEffect(() => { setLocal(q); }, [q]);
```

### 5. Custom Components

Files under `<app>/components/*.tsx` hold app-specific components.

- One component per file; filename matches the component name (PascalCase).
- Either default export OR named export matching the filename.
- Import from siblings with relative paths: `import SearchInput from "./components/SearchInput"`.
- Import shadcn/ui components from `@/components/ui/<component>`, icons from `"lucide-react"`, router from `"react-router-dom"`, SDK hooks/providers/files from `"bifrost"`.

```tsx
// apps/my-app/components/ClientCard.tsx
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Building2 } from "lucide-react";

export default function ClientCard({ name, status }: { name: string; status: string }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-3">
        <Building2 className="h-4 w-4" />
        <span className="font-medium flex-1">{name}</span>
        <Badge>{status}</Badge>
      </CardContent>
    </Card>
  );
}
```

### 6. Code Splitting with React.lazy

Heavy pages (large user deps, charts, rich-text editors) should be code-split so they don't bloat the initial bundle.

When to split: pages with large user deps (`recharts`, `react-quill-new`), rarely-visited routes (settings, admin). When NOT to split: the index route (always loads), small pages with only platform imports.

```tsx
// apps/my-app/_layout.tsx
import { lazy, Suspense } from "react";             // React primitives — NOT from "bifrost"
import { Outlet } from "react-router-dom";          // router — NOT from "bifrost"
import { Loader2 } from "lucide-react";

import Dashboard from "./pages/index";                     // eager — first paint
const Reports = lazy(() => import("./pages/reports"));     // lazy chunk
const Editor = lazy(() => import("./pages/editor"));

export default function Layout() {
  return (
    <div className="flex h-full">
      <nav>…</nav>
      <main className="flex-1 min-h-0 overflow-auto">
        <Suspense fallback={<div className="flex h-full items-center justify-center"><Loader2 className="animate-spin" /></div>}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
```

### 7. Layout — Fixed-height Container

Your app renders in a fixed-height box. Manage your own scrolling; do not assume the page body scrolls.

```tsx
export default function Layout() {
  return (
    <div className="flex h-full">
      <aside className="w-56 shrink-0 border-r">…sidebar…</aside>
      <main className="flex-1 min-w-0 min-h-0 flex flex-col">
        <header className="shrink-0 border-b px-6 py-3">…toolbar…</header>
        <div className="flex-1 min-h-0 overflow-auto p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
```

Key classes: `h-full` on root; `flex-1 min-h-0` on scroll regions (without `min-h-0`, flex children refuse to shrink below intrinsic height); `overflow-auto` on the innermost scrollable region only; `shrink-0` on fixed-height siblings.

### 8. User Identity and Role Guards — v1-only, no v2 equivalent

> **v1 inline-only.** `useUser`, `RequireRole`, and `useAppState` were injected by the v1 inline platform shim (`globalThis.__bifrost_platform`). They do **not** exist in the v2 SDK — there is no `useUser()` hook importable from `"bifrost"` in a `standalone_v2` app.

**v2 approach — gate server-side, not client-side:**

`useBifrostContext()` exposes `baseUrl`, `token`, `orgScope`, `appId`, `authedFetch`, `logout`, `theme`/`setTheme`/`toggleTheme`, and `supportsTheme`. It does **not** expose the current user's identity or roles — the v2 SDK has no client-side role-check hook.

Role enforcement in v2 belongs in the workflow function (server-side). If a user without permission calls a protected endpoint, the workflow raises an error and the app renders the error state. Do not build client-only role gates that hide buttons based on a client-fetched role; they're a UX affordance at best and the server must enforce the rule.

If you need to display the user's name or email for UX purposes, call a lightweight workflow (`functions/me.py::get`) that returns the calling user's info from context — the workflow SDK provides the user identity server-side.

```tsx
// v2: gate in the workflow, not in the component
// functions/admin_action.py
// @workflow
// def run(ctx):
//     if not ctx.user.has_role("Admin"):
//         raise PermissionError("Admin required")
//     ...

// Component: show the error state the mutation returns
import { useWorkflowMutation } from "bifrost";
import { Button } from "@/components/ui/button";

export default function AdminAction() {
  const { mutate, loading, error } = useWorkflowMutation("functions/admin_action.py::run");
  return (
    <div>
      {error && <p className="text-destructive">{error.message}</p>}
      <Button onClick={() => mutate({})} disabled={loading}>Run Admin Action</Button>
    </div>
  );
}
```

### 9. Cross-page State — v1-only, no v2 equivalent

> **v1 inline-only.** `useAppState` was a v1 platform hook with no v2 SDK equivalent. Do not import it from `"bifrost"` in a `standalone_v2` app.

**v2 approach — standard React patterns:**

For cross-page ephemeral state, use React Router's [location state](https://reactrouter.com/en/main/hooks/use-location) (`navigate("/detail", { state: { item } })` → `useLocation().state`) or a React context you define in `App.tsx` and provide above the `<Routes>`. For state that must survive a reload, persist it via a workflow (write to a table row, read it back).

```tsx
// v2: React Router location state for navigation-scoped data
import { useNavigate, useLocation } from "react-router-dom";

// List page
const navigate = useNavigate();
navigate(`/clients/${id}`, { state: { name } });

// Detail page
const { state } = useLocation();
if (!state?.name) return <Navigate to="/" />;
```

### 10. Styling — Tailwind v4

Apps go through the platform's per-app Tailwind v4 pipeline at bundle time.

What works: all standard utilities, host shadcn theme tokens (`bg-background`, `text-muted-foreground`), arbitrary values, `@apply` in `styles.css`, `@layer components`, `:root` and `.dark` CSS variable blocks, per-app `tailwind.config.ts` with `theme.extend`.

What is NOT supported: Tailwind plugins beyond `@tailwindcss/typography`; `@source` directives outside the app root.

```css
/* apps/my-app/styles.css */
:root { --ops-bg: oklch(0.985 0 0); }
.dark { --ops-bg: oklch(0.145 0 0); }
@layer components {
  .ops-pill { @apply inline-flex items-center rounded-full px-3 py-1 text-xs font-medium; }
}
```

### 11. CRUD with Live Updates (own-row policy)

For apps where each user manages their own rows:

```bash
bifrost tables create --name my_tasks --policies '{"policies":[
  {"name":"admin_bypass","actions":["read","create","update","delete"],"when":{"user":"is_platform_admin"}},
  {"name":"own_row","actions":["read","create","update","delete"],"when":{"eq":[{"row":"created_by"},{"user":"user_id"}]}}
]}'
```

```tsx
import { tables, useTable } from "bifrost";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useState } from "react";

export default function MyTasks() {
  const [draft, setDraft] = useState("");
  const { rows, loading } = useTable("my_tasks");

  if (loading) return <div>Loading…</div>;

  return (
    <div>
      <Input value={draft} onChange={(e) => setDraft(e.target.value)} />
      <Button onClick={async () => { await tables.insert("my_tasks", { title: draft }); setDraft(""); }}>
        Add
      </Button>
      <ul>
        {rows.map((r) => (
          <li key={r.id}>
            {String(r.title)}
            <Button variant="ghost" onClick={() => tables.delete("my_tasks", r.id)}>Delete</Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

### 12. Drag and Drop — Use Native HTML5, NOT @dnd-kit

Do NOT use `@dnd-kit/*` or `react-beautiful-dnd`. These context-based libraries fail in Bifrost apps because esm.sh caches modules by externalization signature, producing two copies of the shared context — the drag handle never gets an `onPointerDown`.

Use the browser's native HTML5 drag-and-drop API (`draggable` attribute + `onDragStart` / `onDragOver` / `onDrop` handlers). To restrict dragging to a handle, use a `dragArmed` state toggled on `onMouseDown` of the handle element.

```tsx
function Row({ id, onDragStart, onDragEnd, onDragOver, onDrop, isDragging, isDropTarget }) {
  const [dragArmed, setDragArmed] = useState(false);
  return (
    <div
      draggable={dragArmed}
      onDragStart={(e) => onDragStart(id, e)}
      onDragEnd={() => { setDragArmed(false); onDragEnd(); }}
      onDragOver={(e) => onDragOver(id, e)}
      onDrop={(e) => onDrop(id, e)}
      style={{ opacity: isDragging ? 0.4 : 1, outline: isDropTarget ? "2px solid var(--cv-cb)" : undefined }}
    >
      <span
        role="button"
        aria-label="Drag row"
        onMouseDown={() => setDragArmed(true)}
        onMouseUp={() => setDragArmed(false)}
        style={{ cursor: "grab", userSelect: "none" }}
      >⠿</span>
      {/* row content */}
    </div>
  );
}
```

In the parent: always call `e.preventDefault()` in `onDragOver` (without it, `onDrop` never fires). For keyboard/touch parity, add explicit Up/Down buttons.
