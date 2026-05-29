# Bifrost Platform

Bifrost is an automation platform for building workflows, forms, agents, and apps. Everything below is what you need to build on the platform.

## Workflows & Tools

Workflows are async Python functions decorated with `@workflow`, `@tool`, or `@data_provider`. They run in a sandboxed execution engine with access to the Bifrost SDK.

{decorator_docs}

{context_docs}

{error_docs}

### SDK Modules

All SDK methods are async and must be awaited.

```python
from bifrost import agents, ai, config, files, integrations, knowledge, tables
from bifrost import workflow, data_provider, context
from bifrost import UserError, WorkflowError, ValidationError
```

{sdk_module_docs}

{sdk_models_docs}

### File Locations

The `files` module accepts special locations plus custom, user-defined
locations. Custom location names must match `^[a-z0-9][a-z0-9-]*$`; internal
prefixes `_repo`, `_tmp`, and `_apps` are blocked.

| Location | Usage | Example |
|----------|-------|---------|
| `"workspace"` (default) | General-purpose file storage | `files.read("data/report.csv")` |
| `"temp"` | Temporary files scoped to a single execution | `files.write("scratch.txt", content, location="temp")` |
| `"uploads"` | Files uploaded via form file fields (read-only) | `files.read(path, location="uploads")` |
| custom, e.g. `"reports"` | Scoped user-defined storage | `files.write("q1.pdf", pdf_bytes, location="reports")` |

When a form has a `file` field, the workflow receives the S3 path as a string (or list if `multiple: true`). Read with `location="uploads"`:

```python
from bifrost import workflow, files

@workflow
async def handle_upload(resume: str, cover_letters: list[str]) -> dict:
    resume_bytes = await files.read(resume, location="uploads")
    return {"resume_size": len(resume_bytes)}
```

## Forms

Forms collect user input and trigger workflows. Define them as YAML files with a `form_schema` containing typed fields.

{form_model_docs}

### File Upload Fields

```yaml
- name: resume
  type: file
  label: Upload Resume
  options:
    allowed_types: [".pdf", ".docx"]
    max_size_mb: 10
```

File fields pass S3 paths to workflows as strings. Use `multiple: true` for multi-file uploads.

### Data Provider Fields

Forms can use data providers for dynamic dropdowns:

```yaml
- name: customer
  type: select
  label: Customer
  data_provider:
    id: "workflow-uuid"
    label_field: label
    value_field: value
```

Data providers are workflows decorated with `@data_provider` that return `[{"label": "...", "value": "..."}]`.

## Agents

Agents are AI-powered assistants with access to workflows as tools, knowledge bases, and delegation to other agents. Agents can operate in two modes:

1. **Chat mode** — interactive conversations via the chat UI, Teams, Slack, or voice
2. **Autonomous mode** — headless execution triggered by events, schedules, or SDK calls

{agent_model_docs}

### Available Channels

| Channel | Description |
|---------|-------------|
| `chat` | Web-based chat interface |
| `voice` | Voice interaction |
| `teams` | Microsoft Teams |
| `slack` | Slack |

### Key Fields

- `tool_ids`: List of workflow UUIDs this agent can call as tools
- `delegated_agent_ids`: Other agent UUIDs it can delegate to
- `knowledge_sources`: Knowledge namespace names for RAG search
- `system_tools`: Built-in tools (`http`, etc.)
- `max_iterations`: Max LLM iterations for autonomous runs (default 50)
- `max_token_budget`: Max token budget for autonomous runs (default 100000)
- Scope: `organization_id=None` for global (all orgs) or `organization_id=UUID` for org-scoped

### Autonomous Agent Runs

Agents can run autonomously without a chat session. The agent receives input data, executes a tool-calling loop (LLM → tool → LLM), and returns structured output.

#### SDK Invocation

```python
from bifrost import workflow, agents

@workflow
async def process_ticket(ticket_id: str):
    result = await agents.run(
        "ticket-triage-agent",
        input={"ticket_id": ticket_id, "action": "triage"},
        output_schema={
            "type": "object",
            "properties": {
                "priority": {"type": "string"},
                "category": {"type": "string"},
                "summary": {"type": "string"}
            }
        },
        timeout=300,
    )
    return result
```

#### Event-Triggered Agent Runs

Event subscriptions can target agents directly using `target_type: "agent"`:

```
create_event_subscription(source_id=<id>, agent_id=<agent_id>,
                          target_type="agent", event_type="ticket.created")
```

The event payload is passed as the agent's input data.

#### Agent Run Observability

Each autonomous run records:
- **Steps**: Every LLM call and tool execution as an `AgentRunStep`
- **Token usage**: Total tokens consumed across all iterations
- **Iteration count**: Number of LLM call cycles used
- **Status**: `queued` → `running` → `completed` | `failed` | `timeout`
- **AI usage**: Full cost/token breakdown per LLM call

## Apps

Apps are React + Tailwind applications that run inside the Bifrost platform. You have full creative control — build custom components, use CSS variables, create any UI you can imagine.

### File Structure

```
apps/my-app/
  _layout.tsx           # Root layout (MUST use <Outlet />, NOT {children})
  _providers.tsx        # Optional context providers
  styles.css            # Custom CSS (dark mode via .dark selector)
  pages/
    index.tsx           # Home page (/)
    settings.tsx        # /settings
    clients/
      index.tsx         # /clients
      [id].tsx          # /clients/:id
  components/
    MyWidget.tsx        # Custom components
  modules/
    utils.ts            # Utility modules
```

App metadata (name, description, npm dependencies, access policies) lives on the `Application` record, not in a YAML file. Manage it with the CLI: `bifrost apps create`, `bifrost apps update <ref>`, `bifrost apps get <ref> --json`, `bifrost apps set-deps <ref> --deps '{...}'`.

### Imports

Everything comes from a single import:

```tsx
import { Button, Card, useState, useWorkflowQuery } from "bifrost";
```

External npm packages (declared on the app via `bifrost apps update --deps` / `bifrost apps set-deps`):

```tsx
import dayjs from "dayjs";
import { LineChart, Line } from "recharts";
```

### Workflow Hooks

**CRITICAL: Always use workflow UUIDs, not names.**

#### useWorkflowQuery(workflowId, params?, options?)

Auto-executes on mount. For loading data.

| Property | Type | Description |
|----------|------|-------------|
| `data` | `T \| null` | Result data |
| `isLoading` | `boolean` | True while executing |
| `isError` | `boolean` | True if failed |
| `error` | `string \| null` | Error message |
| `refetch` | `() => Promise<T>` | Re-execute |
| `logs` | `StreamingLog[]` | Real-time logs |

Options: `{ enabled?: boolean }` — set `false` to defer.

#### useWorkflowMutation(workflowId)

Manual execution via `execute()`. For user-triggered actions.

| Property | Type | Description |
|----------|------|-------------|
| `execute` | `(params?) => Promise<T>` | Run workflow |
| `isLoading` | `boolean` | True while executing |
| `data` | `T \| null` | Last result |
| `error` | `string \| null` | Error message |
| `reset` | `() => void` | Reset state |

```tsx
// Load data on mount
const { data, isLoading } = useWorkflowQuery("workflow-uuid", { limit: 10 });

// Button-triggered action
const { execute, isLoading } = useWorkflowMutation("workflow-uuid");
const result = await execute({ name: "New Item" });

// Conditional loading
const { data } = useWorkflowQuery("workflow-uuid", { id }, { enabled: !!id });
```

#### Other Hooks

##### useUser()

Returns the current authenticated user:

```tsx
const user = useUser();
// user.id: string — unique user ID
// user.email: string — user's email
// user.name: string — display name
// user.roles: string[] — all assigned roles
// user.hasRole("Admin"): boolean — check specific role
// user.organizationId: string — org ID (empty for platform users)
```

##### useAppState(key, initialValue)

Zustand-backed cross-page state — like `useState` but persists across page navigations within the same app session.

```tsx
const [selectedClient, setSelectedClient] = useAppState("selectedClient", null);
```

- Can store anything: primitives, objects, arrays, nested structures
- Scoped to the app session — cleared on browser refresh or switching apps
- NOT persistent storage — for permanent data, use workflows to save/load from DB
- Use cases: selected item between list/detail pages, filter/sort preferences, multi-step form data, sidebar state

Example — list page sets, detail page reads:
```tsx
// List page
const [, setClient] = useAppState("selectedClient", null);
<Button onClick={() => { setClient(client); navigate("/details"); }}>View</Button>

// Detail page
const [client] = useAppState("selectedClient", null);
if (!client) return <Navigate to="/" />;
return <div>{client.name}</div>;
```

##### RequireRole

Conditionally renders children based on user role:

```tsx
<RequireRole role="Admin" fallback={<Navigate to="/" />}>
  <AdminPage />
</RequireRole>
```

Props: `role` (string, required), `children` (ReactNode), `fallback` (ReactNode, defaults to null).

##### Other Navigation Hooks

- `useParams()` — URL path parameters
- `useSearchParams()` — query string parameters
- `useNavigate()` — programmatic navigation `navigate("/path")`
- `useLocation()` — current location object

### Table Access (browser SDK)

App-side table reads and writes go through `tables.*` and the `useTable` / `useInfiniteTable` hooks — same data layer as the Python `tables.*` SDK in workflows, just exposed in React. The `where` filter DSL is identical across both, so authors learn it once.

#### `useTable(name, query?)`

Live-updating table data hook with **page-based pagination**. Loads a snapshot of the requested page via `tables.query` and subscribes to live changes; insert/update/delete events apply to the visible page window automatically.

```tsx
import { useTable } from "bifrost";

const { rows, total, totalPages, loading, error } = useTable("notes", {
  where: { client_id: clientId },
  page: 1,
  pageSize: 100,
});
```

Returns `{ rows, total, totalPages, loading, error }`:

- `rows` — current page only, flat row shape (JSONB fields spread to the top level alongside `id`, `created_by`, `created_at`, `updated_at`, `table_id`, `updated_by`). Matches the websocket event shape so live updates merge cleanly.
- `total` — count of matching rows across all pages (server's `total`). Use for "Page X of Y" UI or to detect "more on the server" via `rows.length < total`.
- `totalPages` — `Math.ceil(total / pageSize)`, or `0` for empty tables (unambiguous empty state).
- `loading` — true until the snapshot loads.
- `error` — populated on snapshot failure or subscribe rejection (table not found / policy denied / unsupported `where` operator).

Query options:

| Option | Type | Default | Description |
|---|---|---|---|
| `where` | `DocumentFilter` | none | Filter conditions, see DSL below |
| `page` | `number` | `1` | 1-indexed page number |
| `pageSize` | `number` | `100` | Rows per page (server cap 1000) |
| `order_by` | `string` | `updated_at` | Field name to sort by |
| `order_dir` | `"asc" \| "desc"` | `"asc"` | Sort direction |
| `scope` | `string` | app's org / caller's org | Org scope override |

Live updates: `insert` events bump `total` and append to the visible window iff there's room (out-of-window inserts trim to `pageSize`); `update` replaces in place; `delete` removes from the window and decrements `total`.

#### `useInfiniteTable(name, query?)`

Same DSL as `useTable` but **accumulates** pages on demand for "Load more" / infinite-scroll UI. Each `loadMore()` call appends the next page to `rows`.

```tsx
import { useInfiniteTable } from "bifrost";

const { rows, loadMore, hasMore, loading, error } = useInfiniteTable("contacts", {
  pageSize: 100,
});
```

Returns `{ rows, loadMore, hasMore, loading, error }`. `loadMore()` fetches the next page and appends to `rows`. `hasMore` flips false when a partial page comes back. After page 1 the hook automatically uses `skip_count: true` for speed.

Pick `useTable` for "Page X of Y" numbered-page UI; pick `useInfiniteTable` for "Load more" / infinite-scroll. Both wire live updates the same way.

#### `tables.*` direct calls

| Method | Purpose |
|---|---|
| `tables.get(table, id, scope?)` | Single row read. Returns `null` if missing. Throws `TableAccessDeniedError` on 403. |
| `tables.insert(table, data \| array, scope?)` | Insert one or many rows. Throws `TableNotFoundError` if the table doesn't exist. |
| `tables.upsert(table, item \| array, scope?)` | Insert or update by id. |
| `tables.update(table, id, data, scope?)` | Patch a row's data fields. Returns `null` if missing. |
| `tables.delete(table, id \| array, scope?)` | Delete by id; single-form is idempotent (returns `false` if missing). |
| `tables.query(table, q?, scope?)` | One-shot read with `where`/`limit`/`offset`/`order_by`/`order_dir`/`skip_count`. |
| `tables.count(table, scope?)` | Row count. Throws `TableNotFoundError` if missing. |
| `tables.subscribe(tableId, filter, onEvent)` | Direct live subscription; advanced — `useTable` covers the common case. |

Errors:

- `TableAccessDeniedError` (403) — policy denied.
- `TableNotFoundError` (404, table-level ops) — table doesn't exist in scope.

#### Scope behavior

If you don't pass `scope`:

- **Org-scoped apps** default to the app's `organization_id` — the same convention as org-scoped workflows always running as their org.
- **Global apps** fall back to the server's default (caller's-org behavior).

Cross-org admin tooling can pass an explicit `scope` to override.

#### `where` filter DSL

Field-keyed dict shorthand — same shape the Python SDK uses.

```tsx
// Equality
useTable("notes", { where: { client_id: "abc", pinned: true } });

// Comparison operators
useTable("invoices", { where: { amount: { gte: 100, lt: 1000 } } });

// Substring (case-insensitive)
useTable("clients", { where: { name: { contains: "acme" } } });

// IN
useTable("tickets", { where: { status: { in: ["open", "pending"] } } });

// Null check
useTable("tasks", { where: { deleted_at: { is_null: true } } });
```

Operators: `eq`, `neq` / `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `is_null`, `has_key`, `contains`, `starts_with`, `ends_with`.

**Live-update caveat.** A few operators are query-only: `contains`, `starts_with`, `ends_with`, `has_key`. Using them in `useTable.where` throws via `error` — the policy `Expr` AST has no equivalent. For these cases use `tables.query()` directly (one-shot read, no live updates) or filter client-side.

### Pre-included Components (standard shadcn/ui)

These are available from `"bifrost"` without installation. They are standard shadcn/ui components — use them exactly as documented in the shadcn/ui docs.

**Layout:** Card, CardHeader, CardFooter, CardTitle, CardAction, CardDescription, CardContent

**Forms:** Button, Input, Label, Textarea, Checkbox, Switch, Select (+ SelectTrigger, SelectContent, SelectItem, SelectGroup, SelectLabel, SelectValue, SelectSeparator), RadioGroup, RadioGroupItem, Combobox, MultiCombobox, TagsInput

**Display:** Badge, Avatar (+ AvatarImage, AvatarFallback), Alert (+ AlertTitle, AlertDescription), Skeleton, Progress

**Navigation:** Tabs (+ TabsList, TabsTrigger, TabsContent), Pagination (+ PaginationContent, PaginationEllipsis, PaginationItem, PaginationLink, PaginationNext, PaginationPrevious)

**Feedback:** Dialog (+ DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger), AlertDialog (+ sub-components), Tooltip (+ TooltipContent, TooltipProvider, TooltipTrigger), Popover (+ PopoverContent, PopoverTrigger, PopoverAnchor)

**Data:** Table (+ TableHeader, TableBody, TableFooter, TableHead, TableRow, TableCell, TableCaption)

**Date:** CalendarPicker, DateRangePicker

**Icons:** All lucide-react icons (e.g., `Settings`, `ChevronRight`, `Search`, `Plus`, `Trash2`, `Users`, `Mail`)

**Utilities:** `cn(...)` (Tailwind class merging), `toast(message)` (Sonner notifications), `format(date, pattern)` (date-fns)

### Custom Components

Need a component not listed above? Build it in `components/`. shadcn/ui components are just TSX files — recreate any shadcn component, customize it, or build entirely new ones from scratch with React and Tailwind.

For example, to add a Sheet component, create `components/Sheet.tsx` using Radix primitives and Tailwind — the same pattern shadcn/ui uses. Or build a rich text editor, a kanban board, a color picker — anything you can build in React.

### Custom CSS

Add a `styles.css` file to your app root for custom styles:

```css
/* CSS variables for theming */
:root {
  --app-primary: oklch(0.5 0.18 260);
  --app-surface: #fffef9;
}

/* Dark mode — inherits from platform toggle */
.dark {
  --app-primary: oklch(0.7 0.15 260);
  --app-surface: #1e1e22;
}

/* Custom classes */
.paper-bg {
  background-color: var(--app-surface);
  background-image: repeating-linear-gradient(
    transparent, transparent 1.7rem,
    rgba(0,0,0,0.06) 1.7rem, rgba(0,0,0,0.06) 1.75rem
  );
}
```

Use in components: `<div className="paper-bg rounded-lg">`. Tailwind classes and custom CSS classes can be mixed freely.

### External Dependencies

Declare npm packages in `.bifrost/apps.yaml` under the app's `dependencies` field:

```yaml
dependencies:
  recharts: "2.12"
  dayjs: "1.11"
```

Max 20 packages. Loaded at runtime from esm.sh CDN.

**React compatibility warning:** Packages with complex dependency trees (e.g. `framer-motion`, `@tiptap/*`, `react-beautiful-dnd`) may load a duplicate React instance through their transitive dependencies, causing "Cannot read properties of null (reading 'useContext')" errors. This happens because esm.sh doesn't always propagate React version pinning to transitive deps.

**Safe packages** (pure logic, or simple React wrappers): `dayjs`, `lodash`, `zod`, `uuid`, `react-icons`, `@tanstack/react-table`. Note: `recharts`, `date-fns`, `clsx`, and `tailwind-merge` are pre-included and don't need to be declared.

**Before adding a package**, test it by opening the browser console on a running app and running:
```js
import("https://esm.sh/PACKAGE@VERSION?deps=react@19.1.0,react-dom@19.1.0").then(m => console.log(Object.keys(m)))
```
If it loads and exports look correct, it will likely work. If it errors or the app crashes with a React context error after adding it, the package has a dual-React problem — use Tailwind CSS and custom components instead.

**For animations:** Use Tailwind (`animate-in`, `fade-in`, `transition-all`, `duration-200`) and CSS `@keyframes` in `styles.css` instead of JS animation libraries.

### Runtime Environment

Apps run inside the Bifrost shell (not in an iframe). Browser globals (`window`, `document`, `fetch`, `ResizeObserver`, `MutationObserver`, etc.) are accessible — use them directly when needed. External npm packages that depend on DOM APIs work normally as long as they don't hit the React dual-instance issue above.

**Cannot use:**
- ES dynamic `import()` — all dependencies must be declared in `.bifrost/apps.yaml`
- Node.js APIs (`fs`, `path`, `process`, etc.)

Use `useWorkflowQuery`/`useWorkflowMutation` for calling backend workflows. Use `fetch` directly for external HTTP calls that don't need backend logic.

### Layout

Your app renders in a fixed-height container. The platform does not scroll the page for you — if a page needs scrolling, add `overflow-auto` to the element that should scroll.

### Pre-included Packages

These packages are available without adding them to the app's `dependencies`:

- `recharts` — charts and data visualization
- `date-fns` — date formatting (`format` is available directly from `"bifrost"`)
- `lucide-react` — all icons available from `"bifrost"` import
- `clsx` — class name utility (also available as `cn` from `"bifrost"`)
- `tailwind-merge` — Tailwind class merging (used by `cn`)

### Error Handling in Apps

**Loading states (required for every data-fetching page):**
```tsx
const { data, isLoading, isError, error } = useWorkflowQuery("uuid");
if (isLoading) return <div className="flex items-center justify-center h-full"><Loader2 className="animate-spin" /></div>;
if (isError) return <Alert variant="destructive"><AlertTitle>Error</AlertTitle><AlertDescription>{error ?? "Failed to load"}</AlertDescription></Alert>;
```

**Null-safe access:** Always `data?.items?.map(...)`, never `data.items.map(...)`.

**Mutation error handling:**
```tsx
const { execute, isLoading } = useWorkflowMutation("uuid");
const handleSubmit = async () => {
  const result = await execute(params);
  if (result.error) { toast.error(result.error); return; }
  toast.success("Saved");
};
```

### Common Mistakes

| Mistake | What happens | Fix |
|---------|-------------|-----|
| Relative imports (`./utils`) | Stripped silently, module not found | Import from `"bifrost"` or npm package names only |
| `{children}` in layout | Children not rendered | Use `<Outlet />` in `_layout.tsx` |
| Workflow name instead of UUID | Runtime error | Use UUIDs from `.bifrost/workflows.yaml` |
| Undeclared npm dependency | `undefined` exports, runtime error | `bifrost apps update <ref> --deps '{"pkg":"^x.y.z"}'` first |
| Missing default export in component | Component renders as undefined | Add `export default function MyComponent()` |
| Using `$`, `$deps`, `__defaultExport__` as variable names | Conflicts with runtime internals | Use different variable names |
| No loading/error state for queries | Blank page or crash on slow/failed loads | Always handle `isLoading` and `isError` |

## Tables

Tables provide structured data storage with schema validation and multi-tenancy.

{table_model_docs}

### Column Types

| Type | Options |
|------|---------|
| `string` | minLength, maxLength, enum |
| `number` | minimum, maximum |
| `integer` | minimum, maximum |
| `boolean` | — |
| `date` | — |
| `datetime` | — |
| `json` | — |
| `array` | — |

### Scope & Visibility

| Scope | `organization_id` | Visible to |
|-------|--------------------|-----------|
| Global | `None` | All organizations |
| Organization | UUID | Only the owning org |

Scope is resolved via cascade: org-specific first, then global fallback. The SDK `scope` parameter accepts `None` for global or an org UUID for a specific org. Omit it to use the execution context's org (default, with global cascade).

## Data Providers

Data providers are workflows that return label/value pairs for form dropdowns.

Return format: `[{"label": "Display Name", "value": "unique-id"}]`

Reference in forms:

```yaml
- name: customer
  type: select
  data_provider:
    id: "data-provider-workflow-uuid"
    label_field: label
    value_field: value
```

Use `@data_provider` decorator — see Workflows section for syntax.

## Events

### Schedule Source

```
create_event_source(name="Daily Report", source_type="schedule",
                    cron_expression="0 9 * * *", timezone="America/New_York")
create_event_subscription(source_id=<id>, workflow_id=<id>,
                          input_mapping={"report_type": "daily"})
```

### Webhook Source

```
create_event_source(name="HaloPSA Tickets", source_type="webhook",
                    adapter_name="generic")
  → returns callback_url: /api/hooks/{source_id}
create_event_subscription(source_id=<id>, workflow_id=<id>,
                          event_type="ticket.created")
```

Configure the external service to POST to the callback_url.

### Agent-Targeted Subscriptions

Subscriptions can target agents instead of workflows:

```
create_event_subscription(source_id=<id>, agent_id=<agent_id>,
                          target_type="agent", event_type="ticket.created")
```

The event payload is passed as the agent's input data. The agent runs autonomously and results are recorded as agent runs.

## Manifest YAML Formats (SDK-First / Git Sync)

The `.bifrost/*.yaml` manifest files declare all platform entities as configuration-as-code. Each entity has a manifest entry (identity, org binding, roles) and optionally an entity file (portable definition).

### Workspace Structure

The workspace root is your git repository root. Only `.bifrost/*.yaml` manifests are required — all other directories are convention, not enforced.

```
<repo-root>/
  .bifrost/                   # REQUIRED — manifest files (source of truth)
    organizations.yaml        # Org definitions
    roles.yaml                # Role definitions
    workflows.yaml            # Workflow identity + runtime config
    forms.yaml                # Form identity + org/role binding
    agents.yaml               # Agent identity + org/role binding
    apps.yaml                 # App identity + org/role binding
    integrations.yaml         # Integration definitions + config schema
    configs.yaml              # Config values (secrets redacted)
    tables.yaml               # Table schema declarations
    events.yaml               # Event sources + subscriptions
    knowledge.yaml            # Namespace declarations
  workflows/                  # Convention — workflow Python files
    onboard_user.py
  # Forms and agents are defined inline under their UUIDs in
  # .bifrost/forms.yaml and .bifrost/agents.yaml — there are no
  # standalone forms/ or agents/ files.
  apps/                       # Convention — app source directories
    my-dashboard/             # Source-only; metadata + deps live on the
      _layout.tsx             # Application record (manage with `bifrost apps`)
      styles.css
      pages/index.tsx
      components/
  modules/                    # Convention — shared Python modules
    shared/utils.py
```

{manifest_docs}
