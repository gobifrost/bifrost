# Web SDK v2 Reference

The v2 web SDK is the `bifrost` npm package served from `/api/sdk/download`. It is the SDK for `standalone_v2` Solution apps — self-contained React projects that mount at `/apps/{slug}`, own their own `createRoot`, and import the SDK as a real package. This is distinct from v1 inline apps, which reach for `globalThis.__bifrost_platform`.

See `generated/web-sdk-surface.md` for the full export list.

---

## BifrostProvider

The root context provider for every v2 app. Wrap the app's root in it; every SDK hook requires it.

```tsx
import { BifrostProvider } from "bifrost";

<BifrostProvider baseUrl={BASE_URL} token={TOKEN} orgScope={ORG_ID} appId={APP_ID} supportsTheme>
  <App />
</BifrostProvider>
```

Key props:

| Prop | Required | Purpose |
|------|----------|---------|
| `baseUrl` | yes | Absolute URL of the Bifrost API (no trailing slash) |
| `token` | yes | Bearer access token |
| `orgScope` | no | Active org UUID; null = caller's default |
| `appId` | no | This install's app UUID — required for correct `path::function` resolution at deploy; omit in dev |
| `supportsTheme` | no (default false) | When true, BifrostHeader shows the light/dark toggle |
| `fetchImpl` | no | Override fetch (tests / non-browser) |
| `onLogout` | no | Called when the app requests logout |
| `theme` / `onThemeChange` | no | Host-controlled theme sync |

The provider installs an authed transport synchronously during render (not in an effect) so child mount effects (such as `useTable`'s first snapshot query) always see the correct auth credentials.

## useBifrostContext

Read the SDK context from any child component. Throws if called outside a `<BifrostProvider>`.

```tsx
import { useBifrostContext } from "bifrost";

const { baseUrl, token, orgScope, appId, authedFetch, logout, theme, setTheme } = useBifrostContext();
```

## BifrostHeader

A self-contained header component safe to use in v2 apps — it depends only on `react` and `lucide-react`, not on shadcn internal aliases that do not resolve outside the client project.

```tsx
import { BifrostHeader } from "bifrost";

<BifrostHeader title="My App" />
```

The light/dark toggle only appears when the parent `<BifrostProvider supportsTheme>` prop is set. An app with hardcoded colors should omit `supportsTheme` so the toggle is never shown.

---

## Workflow Hooks

Three hooks for running Bifrost workflows from a v2 app. Workflow refs are portable `path::function` strings (e.g. `functions/hello.py::main`) or UUIDs — bare names are not supported because they are not unique.

### useWorkflowQuery — READ

Auto-runs on mount and re-runs when `workflowRef` or `params` change. Use for "load data when the component mounts."

```tsx
import { useWorkflowQuery } from "bifrost";

const { data, loading, error, refresh } = useWorkflowQuery<{ items: Item[] }>(
  "functions/list_items.py::run",
  { status: "active" }          // optional params, passed as input_data
);
```

- `data` is `null` before the first run completes (guard with `??` / `?.`).
- `refresh(input?)` re-runs with the original params (or overrides).
- Do not use this for click/submit actions — use `useWorkflowMutation`.

### useWorkflowMutation — WRITE / ACTION

Does NOT run on mount. Call `mutate(input)` from an event handler.

```tsx
import { useWorkflowMutation } from "bifrost";

const { mutate, data, loading, error } = useWorkflowMutation("functions/save.py::save");

async function onSubmit() {
  const result = await mutate({ name, email });   // resolves to the workflow result
}
```

### useWorkflow — LOW-LEVEL

The building block that the two hooks above wrap. Returns `{ data, loading, error, run }` where `run(input?)` is an imperative trigger. The hook never auto-runs; `const { data } = useWorkflow(ref)` silently stays null until you call `run()`. Prefer `useWorkflowQuery` / `useWorkflowMutation` unless you need the lower-level control.

### Choosing the right hook

| Situation | Hook |
|-----------|------|
| Load data on page mount | `useWorkflowQuery` |
| Re-load when a param changes | `useWorkflowQuery` with reactive `params` |
| Submit a form / handle a button click | `useWorkflowMutation` |
| Need a single hook for both auto-run and imperative control | `useWorkflow` + your own effect |

---

## useTable and useInfiniteTable

Live-updating hooks backed by a Bifrost table. See `references/tables.md` for the full table data model and filter DSL. The hooks below document the React interface.

### useTable — paged, live

```tsx
import { useTable } from "bifrost";

const { rows, total, totalPages, loading, error } = useTable("my_table", {
  where: { status: "open" },    // field-keyed filter DSL
  page: 1,                      // 1-indexed
  pageSize: 50,                 // default 100, server cap 1000
  order_by: "created_at",
  order_dir: "desc",
  scope: orgId,                 // optional org override
});
```

- `rows` is the flat shape: JSONB fields are spread to the top level alongside `id`, `created_by`, `updated_by`, `created_at`, `updated_at`, `table_id`.
- `total` is the count matching `where` across ALL pages (not just the current page). Use it to drive pagination UI.
- Live inserts outside the current page window are dropped to keep the visible page stable; navigate to that page to see them.
- Operators `contains`, `starts_with`, `ends_with`, `has_key` work with `tables.query` one-shot reads but throw an error in `useTable.where` (the live subscribe filter has no equivalent). Split into a query-only call if you need them.

### useInfiniteTable — infinite scroll

```tsx
import { useInfiniteTable } from "bifrost";

const { rows, loadMore, hasMore, loading, error } = useInfiniteTable("my_table", {
  where: { status: "open" },
  pageSize: 50,
});

// on scroll-to-bottom:
if (hasMore && !loading) await loadMore();
```

Accumulates rows across all loaded pages. `hasMore` is true until a partial page is returned. Live updates apply to whatever's been loaded.

---

## Error Classes

```tsx
import { tables, TableAccessDeniedError, TableNotFoundError } from "bifrost";

try {
  const snap = await tables.query("my_table");
} catch (e) {
  if (e instanceof TableNotFoundError) { /* table missing */ }
  if (e instanceof TableAccessDeniedError) { /* policy denied */ }
}
```

---

## v2 App Anatomy (brief)

A `scaffold-app` skeleton gives:

```
my-solution/
  apps/my-app/
    package.json      # vite + react deps (app root)
    vite.config.ts    # Tailwind v4 via @tailwindcss/vite
    index.html        # loads /src/main.tsx
    src/              # ALL app source under src/
      main.tsx        # createRoot + <BifrostProvider> (keep as scaffolded)
      App.tsx         # <BrowserRouter> + routes
      index.css       # @import "tailwindcss" + shadcn tokens
  functions/
    hello.py          # @workflow decorated function (solution root)
```

The workflow ref in the app is workspace-root-relative: `"functions/hello.py::main"` regardless of where the calling app lives under the solution root.

Note: v1 inline apps are legacy. They inject `React`, shadcn components, and `useWorkflowQuery` via `globalThis.__bifrost_platform` and import everything from `"bifrost"`. Migrate to v2 only when converting the app to `standalone_v2`. See the migrate skill for guidance.
