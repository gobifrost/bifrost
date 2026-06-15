# App Patterns

Mandatory resilience and structure patterns for every Bifrost app. Skipping these produces broken apps.

## 1. Loading & Error States

Every data-fetching page must render a distinct UI for `isLoading` and `isError`.

### `useWorkflowQuery`

```tsx
import { useWorkflowQuery, Alert, AlertTitle, AlertDescription, Skeleton } from "bifrost";
import { Loader2 } from "lucide-react";

export default function ClientsPage() {
  const { data, isLoading, isError, errorMessage } = useWorkflowQuery<{ items: any[] }>("uuid-here");

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }
  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Error</AlertTitle>
        <AlertDescription>{errorMessage ?? "Failed to load"}</AlertDescription>
      </Alert>
    );
  }

  return (
    <ul>
      {data?.items?.map((c) => <li key={c.id}>{c.name}</li>)}
    </ul>
  );
}
```

### Plain `fetch`

```tsx
import { useEffect, useState } from "bifrost";

export default function Page() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/something")
      .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((json) => { if (!cancelled) setData(json); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div>Loading…</div>;
  if (error) return <div className="text-destructive">{error}</div>;
  return <pre>{JSON.stringify(data, null, 2)}</pre>;
}
```

## 2. Null-safe data access

Workflow results can be null until the execution completes. Use optional chaining and nullish coalescing everywhere.

```tsx
const { data } = useWorkflowQuery<{ items?: Client[] }>("uuid");

// YES — never throws if data / items are null
const count = data?.items?.length ?? 0;
data?.items?.map((c) => <Row key={c.id} name={c.name ?? "Unknown"} />);

// NO — throws on first render
data.items.map(...)           // TypeError: Cannot read properties of null
data.items.length             // TypeError
```

## 3. Mutation error handling

Every `useWorkflowMutation` must handle errors with user feedback and leave the user on the current page unless the mutation succeeds.

```tsx
import { useWorkflowMutation, Button, toast } from "bifrost";

export default function SaveButton({ payload }: { payload: any }) {
  const { execute, isLoading } = useWorkflowMutation("save-uuid");

  async function onClick() {
    try {
      await execute(payload);
      toast.success("Saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
      // stay on page — user can retry
    }
  }

  return <Button onClick={onClick} disabled={isLoading}>Save</Button>;
}
```

### Execution IDs and redirects

`execute()` resolves with the final workflow result, not the execution ID. Both `useWorkflowMutation` and `useWorkflowQuery` expose `executionId` as reactive state; it becomes non-null after the workflow execution is created and before the final result is available. If you need to navigate to the execution page, react to `executionId` in `useEffect`.

```tsx
import { useEffect, useState, useWorkflowMutation, useNavigate, Button, toast } from "bifrost";

export default function StartReportButton() {
  const navigate = useNavigate();
  const [shouldRedirect, setShouldRedirect] = useState(false);
  const { execute, executionId, isLoading } = useWorkflowMutation("start-report-uuid");

  useEffect(() => {
    if (!shouldRedirect || !executionId) return;
    navigate(`/executions/${executionId}`);
  }, [shouldRedirect, executionId, navigate]);

  function start() {
    setShouldRedirect(true);
    execute({ reportType: "monthly" }).catch((e) => {
      setShouldRedirect(false);
      toast.error(e instanceof Error ? e.message : "Could not start report");
    });
  }

  return <Button onClick={start} disabled={isLoading}>Start report</Button>;
}
```

Use the `shouldRedirect` guard so an old latest `executionId` does not redirect immediately. For concurrent/background runs, remember that the hook's `executionId`, `data`, `errorMessage`, and `isLoading` describe the latest run from that hook, not separate per-run state.

### Optimistic update reversal

If you mutate local state before awaiting the workflow, revert it on error.

```tsx
import { useState, useWorkflowMutation, toast } from "bifrost";

export default function ToggleStar({ item }: { item: { id: string; starred: boolean } }) {
  const [starred, setStarred] = useState(item.starred);
  const { execute } = useWorkflowMutation("toggle-star-uuid");

  async function toggle() {
    const prev = starred;
    setStarred(!prev);            // optimistic
    try {
      await execute({ id: item.id, starred: !prev });
    } catch (e) {
      setStarred(prev);           // revert
      toast.error("Could not update");
    }
  }

  return <button onClick={toggle}>{starred ? "★" : "☆"}</button>;
}
```

## 4. Dependency safety (hooks)

`useEffect` / `useCallback` / `useMemo` dependency arrays must include every referenced external value or you will get stale closures. Never disable the exhaustive-deps rule without understanding why.

```tsx
import { useEffect, useState } from "bifrost";

export default function Search({ q, onChange }: { q: string; onChange: (s: string) => void }) {
  const [local, setLocal] = useState(q);

  // WRONG — missing `q` dep, stale if parent changes q
  // useEffect(() => { setLocal(q); }, []);

  // RIGHT
  useEffect(() => { setLocal(q); }, [q]);

  // RIGHT — debounce pattern, cleanup cancels stale callbacks
  useEffect(() => {
    const h = setTimeout(() => onChange(local), 250);
    return () => clearTimeout(h);
  }, [local, onChange]);

  return <input value={local} onChange={(e) => setLocal(e.target.value)} />;
}
```

## 5. Custom components

Files under `<app>/components/*.tsx` hold app-specific components. Rules:

- One component per file; filename matches the component name (PascalCase).
- Either default export OR named export matching the filename. The bundler detects which.
- Import from sibling files with relative paths: `import SearchInput from "./components/SearchInput"` (from a page) or `import Helper from "./Helper"` (from another component).
- Components CAN reference each other — the bundler handles the dependency graph.
- Components import platform primitives from `"bifrost"`, icons from `"lucide-react"`, router from `"react-router-dom"` — same rules as pages.

```tsx
// apps/my-app/components/ClientCard.tsx
import { Card, CardContent, Badge } from "bifrost";
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

```tsx
// apps/my-app/pages/clients/index.tsx
import ClientCard from "../../components/ClientCard";
// …
```

## 6. Code splitting with `React.lazy`

Heavy pages (large dependencies, charts, rich text editors) should be code-split so they don't bloat the initial bundle. The bundler supports `lazy(() => import("./pages/heavy"))` natively — esbuild emits a separate chunk and the browser fetches it on demand.

**When to split:**
- Pages that pull in large user deps (`recharts`, `react-quill-new`, etc.).
- Rarely-visited routes (settings, admin, onboarding wizards).
- Anything that makes first paint slow for common routes.

**When NOT to split:**
- The index route — it always loads; splitting adds one extra round-trip.
- Small pages with only platform imports.

### Pattern

```tsx
// apps/my-app/_layout.tsx
import { Outlet, Suspense, lazy } from "bifrost";
import { Loader2 } from "lucide-react";
import { Route, Routes } from "react-router-dom";

// Index is eager — first paint has no extra round-trip.
import Dashboard from "./pages/index";
// Heavy routes are lazy — separate chunk, fetched on navigation.
const Reports = lazy(() => import("./pages/reports"));
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

If your app routes via `<Outlet />` in `_layout.tsx` (the typical case), a single `<Suspense>` around `<Outlet />` covers every lazy child page. If you build your own `<Routes>` tree, wrap it in `<Suspense>`.

## 7. Layout — fixed-height container

Your app renders in a fixed-height box. Manage your own scrolling; do not assume the page body scrolls.

```tsx
// apps/my-app/_layout.tsx
import { Outlet } from "bifrost";

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

Key classes:
- `h-full` on the root to claim the container's height.
- `flex-1 min-h-0` on scroll regions — without `min-h-0`, flex children refuse to shrink below their intrinsic content height and overflow escapes the container.
- `overflow-auto` on the innermost scrollable region only.
- `shrink-0` on fixed-height siblings (toolbar, sidebar).

## 8. `useUser` + role guards

### Page-level guard (first line of the component)

```tsx
import { useUser, Navigate } from "bifrost";

export default function AdminPage() {
  const user = useUser();
  if (!user.hasRole("Admin")) return <Navigate to="/" />;
  // …
}
```

### Section-level guard

```tsx
{user.hasRole("Manager") && <AdminPanel />}
```

### Declarative guard

```tsx
import { RequireRole, Navigate } from "bifrost";

<RequireRole role="Admin" fallback={<Navigate to="/" />}>
  <AdminPage />
</RequireRole>
```

### Layout-level guard (protect all child routes)

```tsx
// _layout.tsx
import { Outlet, useUser, Navigate } from "bifrost";

export default function Layout() {
  const user = useUser();
  if (!user.hasRole("Admin")) return <Navigate to="/" />;
  return <div className="flex h-full"><Sidebar /><Outlet /></div>;
}
```

## 9. `useAppState` — cross-page state

Like `useState` but persists across page navigations within the same app session.

```tsx
import { useAppState, Button, useNavigate } from "bifrost";

// List page
export default function ClientsList() {
  const [, setSelected] = useAppState<any>("selectedClient", null);
  const navigate = useNavigate();
  return clients.map((c) => (
    <Button key={c.id} onClick={() => { setSelected(c); navigate("/client-details"); }}>
      {c.name}
    </Button>
  ));
}

// Detail page
import { useAppState, Navigate } from "bifrost";

export default function ClientDetails() {
  const [selected] = useAppState<any>("selectedClient", null);
  if (!selected) return <Navigate to="/" />;
  return <div>{selected.name}</div>;
}
```

Scope: the app session. Cleared on browser refresh or when switching apps. NOT persistent storage — save to DB via workflows for anything that must survive a reload.

## 10. Styling — Tailwind v4 in apps

Apps go through the platform's per-app Tailwind v4 pipeline at bundle time. Everything a normal Tailwind project supports works — including the long-tail features that landed in v4. **Use them.** The platform compiles your app's classes against the host theme; you don't have to remember a list of "what's safe."

### What works

- **All standard utilities**, including the host's shadcn theme tokens (`bg-background`, `bg-card`, `text-muted-foreground`, etc.) — these come from the host preload, available everywhere.
- **Arbitrary values**, including with CSS variables and modern color spaces:
  - `max-w-[1400px]`, `min-h-[calc(100vh-4rem)]`, `px-[clamp(1rem,3vw,2.5rem)]`
  - `lg:grid-cols-[minmax(0,1fr)_360px]`, `md:grid-cols-[repeat(auto-fit,minmax(220px,1fr))]`
  - `bg-[color:var(--ops-paper)]`, `bg-[oklch(0.4_0.1_220)]`, `bg-[hsl(var(--accent)/0.6)]`
- **Responsive variants of arbitrary values:** `lg:py-14`, `xl:grid-cols-[1fr_440px_280px]`
- **`@apply` in `styles.css`**, including with arbitrary values: `.themed { @apply bg-[color:var(--paper)] p-4 rounded; }`
- **`@layer components { .x { @apply ... } }`** for shared component styles.
- **`:root` and `.dark` CSS variable blocks** in `styles.css` — pass through unchanged.
- **Per-app `tailwind.config.{ts,js,mjs,cjs}`** — drop one at the app root and its `theme.extend` is honored. Use this to add custom theme tokens like `theme.extend.colors.brand.500` so `bg-brand-500` compiles.

### Worked example: app with theme tokens, @apply, and per-app config

```css
/* apps/my-app/styles.css */
:root {
  --ops-bg: oklch(0.985 0 0);
  --ops-paper: oklch(1 0 0);
  --ops-fg: oklch(0.145 0 0);
}
.dark {
  --ops-bg: oklch(0.145 0 0);
  --ops-paper: oklch(0.205 0 0);
  --ops-fg: oklch(0.985 0 0);
}

@layer components {
  .ops-pill {
    @apply inline-flex items-center rounded-full px-3 py-1 text-xs font-medium;
  }
}
```

```ts
// apps/my-app/tailwind.config.ts (optional)
export default {
  theme: {
    extend: {
      colors: { brand: { 500: "#facc15" } },
    },
  },
};
```

```tsx
// apps/my-app/pages/index.tsx
export default function Page() {
  return (
    <div className="bg-[color:var(--ops-bg)] text-[color:var(--ops-fg)] min-h-[calc(100vh-4rem)]">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <span className="ops-pill bg-brand-500">Status</span>
      </div>
    </div>
  );
}
```

All four feature classes here (custom CSS variables, `@layer` + `@apply`, per-app theme token, arbitrary-value layout) compile correctly. No workarounds needed.

### Cascade order

The bundler emits the per-app Tailwind output ahead of any other user CSS in the synthesized entry, then user CSS comes after. So host preload < app utilities < user CSS specificity. If you need to override a Tailwind utility, just write the CSS rule.

### What's still NOT supported

- Tailwind plugins beyond `@tailwindcss/typography` (which the host already provides via the preload). The bundler's compile pass uses the default v4 plugin set; per-app `plugins: [...]` arrays in `tailwind.config.ts` are ignored.
- `@source` directives to scan files outside the app root. The bundler scans the app's own materialized source tree only.

## 11. CRUD-with-live-updates app (own-row policy)

For apps where each user manages their own rows, configure the table with an `own_row` policy and use `useTable` in the app.

Step 1: create the table with policies
```bash
bifrost tables create my_tasks --policies '{"policies":[
  {"name":"admin_bypass","actions":["read","create","update","delete"],"when":{"user":"is_platform_admin"}},
  {"name":"own_row","actions":["read","create","update","delete"],"when":{"eq":[{"row":"created_by"},{"user":"user_id"}]}}
]}'
```

Step 2: build the app
```tsx
import { tables, useTable, Button, Input } from "bifrost";
import { useState } from "react";

export default function MyTasks() {
  const [draft, setDraft] = useState("");
  const { rows, loading } = useTable("my_tasks");

  if (loading) return <div>Loading…</div>;

  return (
    <div>
      <Input value={draft} onChange={(e) => setDraft(e.target.value)} />
      <Button onClick={async () => {
        await tables.insert("my_tasks", { title: draft });
        setDraft("");
      }}>Add</Button>
      <ul>
        {rows.map((r) => (
          <li key={r.id}>
            {r.title}
            <Button variant="ghost" onClick={() => tables.delete("my_tasks", r.id)}>Delete</Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

The `created_by` is auto-stamped by the API. The `own_row` policy ensures each user only sees and mutates their own rows. The `useTable` hook keeps the rendered list in sync via websocket — when the user inserts via `tables.insert`, the local state updates from the server's `insert` event (no manual refresh).

## 12. Drag-and-drop — use native HTML5, not `@dnd-kit` / `react-beautiful-dnd`

**TL;DR:** Don't pull in `@dnd-kit/*` or `react-beautiful-dnd`. Use the browser's native HTML5 drag-and-drop API (`draggable` attribute + `onDragStart`/`onDragOver`/`onDrop` handlers). Anything that relies on a React Context shared between multiple packages will silently fail because of how esm.sh keys its cache.

### Why context-based DnD libraries fail

App dependencies resolve at runtime via esm.sh. esm.sh caches each module by `(package, version, externalization-signature)` — the externalization signature is the set of deps the requester wants to treat as external (passed through the `?external=` query). Two import sites that ask for the same package with different externals get two distinct, cached copies.

For `@dnd-kit`:
1. The page imports `@dnd-kit/core` directly — esm.sh fetches it with the full app externals (`react,react-dom,react-dom/client,react/jsx-runtime,react-router-dom`).
2. The page also imports `@dnd-kit/sortable`, which has an internal `import "@dnd-kit/core"`. That internal request is resolved by esm.sh with a *different* externals signature (`react,react-dom` only).
3. **Two copies of `@dnd-kit/core@x.y.z` load**, each with its own `React.createContext()` for `DndContext`.
4. The page's `<DndContext>` provider uses copy A. `useSortable` (inside `@dnd-kit/sortable`) reads context from copy B — which is empty.
5. `useSortable` returns `{ attributes, listeners: {}, setNodeRef, ... }`. `{...attributes}` spreads aria props onto the handle but `{...listeners}` is a no-op. **The drag handle never gets an `onPointerDown` — holding it does nothing.**

You can confirm the duplication in DevTools:

```js
performance.getEntriesByType('resource')
  .filter(e => e.name.includes('@dnd-kit/core'))
```

Two entries for the same version with different `X-...` segments = two physically separate modules. Pinning the version in `bifrost apps set-deps` does **not** dedupe — the cache key includes the externals signature, not just the version.

`react-beautiful-dnd` has the same class of failure (different `react-redux`/`react-dom` resolution under esm.sh).

### Pattern: native HTML5 drag-and-drop with handle-armed dragging

By default, HTML5 `draggable="true"` makes the entire element draggable. To restrict dragging to a specific handle (like the grip icon), keep a local `dragArmed` state, set it on `onMouseDown` of the handle, and clear it on `onDragEnd` and the handle's `onMouseUp`. The row is only `draggable={dragArmed}`.

```tsx
function Row({ id, onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop, isDragging, isDropTarget }) {
  const [dragArmed, setDragArmed] = useState(false);

  return (
    <div
      draggable={dragArmed}
      onDragStart={(e) => onDragStart(id, e)}
      onDragEnd={() => { setDragArmed(false); onDragEnd(); }}
      onDragOver={(e) => onDragOver(id, e)}
      onDragLeave={() => onDragLeave(id)}
      onDrop={(e) => onDrop(id, e)}
      style={{
        opacity: isDragging ? 0.4 : 1,
        outline: isDropTarget ? "2px solid var(--cv-cb)" : undefined,
      }}
    >
      <span
        role="button"
        aria-label="Drag row"
        onMouseDown={() => setDragArmed(true)}
        onMouseUp={() => setDragArmed(false)}
        style={{ cursor: "grab", userSelect: "none" }}
      >
        <GripVertical size={14} />
      </span>
      {/* …row content… */}
    </div>
  );
}
```

Parent component owns the drag state:

```tsx
const [draggedId, setDraggedId] = useState<string | null>(null);
const [dropTargetId, setDropTargetId] = useState<string | null>(null);

function onDragStart(id, e) {
  setDraggedId(id);
  try {
    e.dataTransfer.setData("text/plain", id);
    e.dataTransfer.effectAllowed = "move";
  } catch {}
}

function onDragOver(id, e) {
  if (!draggedId || draggedId === id) return;
  e.preventDefault();       // REQUIRED — without preventDefault, drop never fires
  e.stopPropagation();
  try { e.dataTransfer.dropEffect = "move"; } catch {}
  if (dropTargetId !== id) setDropTargetId(id);
}

function onDragLeave(id) {
  if (dropTargetId === id) setDropTargetId(null);
}

function onDragEnd() {
  setDraggedId(null);
  setDropTargetId(null);
}

async function onDrop(id, e) {
  e.preventDefault();
  e.stopPropagation();
  const src = draggedId;
  setDraggedId(null);
  setDropTargetId(null);
  if (!src || src === id) return;
  // reorder logic → tables.update(...) with new sort_order, etc.
}
```

### Trade-offs vs `@dnd-kit`

| | `@dnd-kit` (broken on Bifrost) | Native HTML5 (works) |
|---|---|---|
| Items slide aside as you drag over | ✅ when working | ❌ — drop target gets an outline, items stay put until drop |
| Touch support | ✅ via PointerSensor | ⚠️ inconsistent across mobile browsers |
| Keyboard a11y | ✅ via KeyboardSensor | ❌ — add explicit Up/Down buttons if you need keyboard reorder |
| Setup complexity | Sensors, contexts, strategies, collision detection | A few handlers |
| Works in a Bifrost app | ❌ | ✅ |

For most app reorder UIs (kanban-lite, tree drag, sortable lists) the native pattern is sufficient. If you need full touch/keyboard parity, expose explicit Up/Down buttons alongside the handle.

### Reference implementations

- `apps/notes/_layout.tsx` — reparent notes via drag-into-row (tree drag).
- `apps/bifrost-grc/pages/frameworks/[id].tsx` + `components/framework-builder/` — sortable list with within-container reorder and cross-container move (domains + controls).
