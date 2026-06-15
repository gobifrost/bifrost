# Import Patterns

Every name referenced in app code needs an explicit import. There is no auto-injection. This file tells you which source a given name comes from.

## Sources

| Source | Module specifier | What it provides |
|--------|------------------|------------------|
| Platform | `"bifrost"` | UI components, platform hooks, utilities — the names in [platform-api.md](platform-api.md) |
| Lucide icons | `"lucide-react"` | ~1000 `<Icon />` components (Phone, Mail, ChevronRight, …) |
| React Router | `"react-router-dom"` | `Link`, `NavLink`, `Navigate`, `useNavigate`, `Outlet`, etc. |
| User components | Relative path | Files under `components/*.tsx` in the current app |
| User npm deps | Bare specifier | Packages declared on the app via `bifrost apps update --deps` / `bifrost apps set-deps` (resolved via esm.sh at runtime) |

## Rules

### Platform names → `"bifrost"`

```tsx
import { Button, Card, CardContent, useWorkflowQuery, useState, toast, cn } from "bifrost";
```

Platform exports include: React hooks (`useState`, `useEffect`, …), shadcn-style UI components (`Button`, `Dialog`, `Table`, …), platform-specific components (`Combobox`, `MultiCombobox`, `TagsInput`, `DateRangePicker`), platform hooks (`useWorkflowQuery`, `useWorkflowMutation`, `useUser`, `useAppState`, `RequireRole`), utilities (`cn`, `clsx`, `twMerge`, `toast`, `format`, `formatDate`, `formatDateShort`, `formatTime`, `formatRelativeTime`, `formatBytes`, `formatNumber`, `formatCost`, `formatDuration`).

Complete list: [platform-api.md](platform-api.md). Canonical source: `api/bifrost/platform_names.py`.

### Icons → `"lucide-react"`

```tsx
import { Phone, Mail, ChevronRight, AlertTriangle } from "lucide-react";
```

Never import icons from `"bifrost"`. (The bundler's deprecation shim still translates icon names from `"bifrost"` for unmigrated apps, but new code goes directly to `"lucide-react"`.)

### React Router → `"react-router-dom"` (preferred)

```tsx
import { Link, NavLink, Outlet, useNavigate, useLocation, useParams } from "react-router-dom";
```

These primitives are also available from `"bifrost"` for legacy reasons, but prefer `"react-router-dom"` explicitly. The bundled runtime sets `basename` on the outer `<BrowserRouter>`, so raw React Router `<Link to="/clients">` correctly navigates to `/apps/<slug>/preview/clients` (preview) or `/apps/<slug>/clients` (live) — no wrapping needed.

### User components → relative path

```tsx
import SearchInput from "./components/SearchInput";        // default export (most common)
import { ClientEditor } from "./components/ClientEditor";  // named export
```

- Files in `components/<Name>.tsx` must have either a default export or a named export matching the filename.
- Imports are always relative (`./components/Foo`, `../components/Foo`) — never from `"bifrost"`.
- Page files under `pages/**` always use default export for the page component.

### User npm deps → bare specifier

```tsx
import { format } from "date-fns";
import { LineChart, Line, XAxis, YAxis } from "recharts";
```

- Declared on the `Application` record (max 20). Inspect with `bifrost apps get <ref> --json | jq .dependencies`; modify with one of:
  - `bifrost apps create --slug my-app --name "My App" --deps '{"recharts":"^2.12.0"}'`
  - `bifrost apps create --slug my-app --name "My App" --deps @package.json` (extracts the `dependencies` block)
  - `bifrost apps update <ref> --deps '{"recharts":"^2.12.0"}'`
  - `bifrost apps set-deps <ref> --deps '{"recharts":"^2.12.0"}'` (full replace)
- Resolved at runtime via the host's import map pointing at esm.sh.
- No `package.json` / no `npm install` / no `node_modules` in the workspace.
- Always declare the dep on the app record BEFORE writing the import — otherwise the browser import map won't include it and the module load 404s.

Pre-included (do NOT declare): `react`, `react-dom`, `react-router-dom`, `lucide-react`, `clsx`, `tailwind-merge`, `date-fns`.

## Full example — a page combining every source

```tsx
// apps/my-crm/pages/clients/index.tsx

import { useState, Card, CardContent, Button, Badge, useWorkflowQuery, toast, cn } from "bifrost";
import { Phone, Mail, ChevronRight } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { format } from "date-fns";
import SearchInput from "../../components/SearchInput";

const WF_LIST_CLIENTS = "4f262085-f4d1-4b3d-a601-575ba4c9d207";

export default function ClientsPage() {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const { data, isLoading, isError, errorMessage } = useWorkflowQuery(WF_LIST_CLIENTS, { q });

  if (isError) {
    toast.error(errorMessage ?? "Failed to load clients");
    return null;
  }

  return (
    <div className="flex flex-col gap-4">
      <SearchInput value={q} onChange={setQ} />
      {isLoading ? (
        <div>Loading…</div>
      ) : (
        <div className="grid gap-2">
          {data?.items?.map((c: any) => (
            <Card key={c.id} className={cn("cursor-pointer")} onClick={() => navigate(`/clients/${c.id}`)}>
              <CardContent className="flex items-center gap-3 p-3">
                <Phone className="h-4 w-4 text-muted-foreground" />
                <div className="flex-1">
                  <div className="font-medium">{c.name}</div>
                  <div className="text-xs text-muted-foreground">{format(new Date(c.created_at), "MMM d, yyyy")}</div>
                </div>
                <Badge>{c.status}</Badge>
                <ChevronRight className="h-4 w-4" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Link to="/clients/new"><Button>New client</Button></Link>
    </div>
  );
}
```

## Things not to do

- Do NOT import icons from `"bifrost"`. Use `"lucide-react"`.
- Do NOT write `import X from "bifrost/Button"` — `"bifrost"` is a single module with named exports.
- Do NOT rely on auto-injection — every `<PascalCase>` tag and every referenced identifier needs an explicit import.
- Do NOT add a `package.json` or `node_modules/` to the app directory at runtime — user deps live on the `Application` record (set via `bifrost apps update --deps` / `bifrost apps set-deps`) and resolve via esm.sh. (`--deps @package.json` is a one-shot import helper, not a per-app config file.)
- Do NOT wrap the app in your own `<BrowserRouter>` — the shell already does this with the correct `basename`.

## Migration notes

Apps that still use the legacy "everything from `'bifrost'`" pattern continue to work — the bundler emits a synthesized `node_modules/bifrost/index.js` that re-exports user components, Lucide icons, and React Router primitives alongside real platform names. A console warning lists the deprecated imports.

To migrate an existing app:

```bash
bifrost migrate-imports            # prints diff, asks to apply
bifrost migrate-imports --dry-run  # prints diff only
bifrost migrate-imports --yes      # prints diff, applies without asking
```

**Always review the diff.** The classifier uses regex, not AST, so it cannot track function parameters / destructured bindings / type-level identifiers. A locally-declared name that shadows a platform export (e.g. a destructured parameter `{ Badge }`) may be incorrectly imported. If the diff adds an import for a name that was already defined locally, reject it and fix by hand.

Classifier precedence (first match wins):
1. User component — `components/<Name>.tsx` exists → relative default/named import
2. React Router primitive → move to `"react-router-dom"`
3. Platform export (in `PLATFORM_EXPORT_NAMES`) → stays in `"bifrost"`
4. Lucide icon → move to `"lucide-react"`
5. Unknown → stays in `"bifrost"` (bundler will error loudly at build if it's a typo)
