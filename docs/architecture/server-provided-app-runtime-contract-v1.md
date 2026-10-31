# App Runtime V1 Public Operation Inventory

Status: Design inventory for [Server-Provided App Runtime](server-provided-app-runtime.md).
Source: exports reachable from `client/src/lib/app-sdk/index.v2.ts`.

This inventory records the App-facing behavior the migration must preserve.
The runtime translates these stable SDK shapes into the server's current wire
protocol. Matching names between today's SDK event and today's server frame do
not make future raw server frames part of the interface.

## Tables

| Existing operation | Runtime responsibility and preserved result |
| --- | --- |
| `get(table, id, scope?)` | Read one document; return `null` for ambiguous row/table 404. |
| `insert(table, dataOrBatch, scope?)` | Single or batch insert; return document or document array. |
| `upsert(table, itemOrBatch, scope?)` | Single or batch upsert, preserving IDs and merge semantics. |
| `update(table, id, data, scope?)` | Update a document; return document or `null` for ambiguous 404. |
| `delete(table, idOrIds, scope?)` | Single returns boolean, including false when missing; batch returns deleted count. |
| `query(table, query?, scope?)` | Return stable document list, total, and canonical table identity. |
| `count(table, scope?)` | Return the numeric count. |
| `subscribe(tableId, filter, onEvent, onReconnect?)` | Return unsubscribe; reconnect/resubscribe and normalize events. Call reconnect only after acknowledgement. |

Table-level operations distinguish missing table from missing row. Preserve
binding-owned `TableAccessDeniedError` and `TableNotFoundError` constructors.
Do not turn denied reads into empty successful results.

Freeze the existing public `TableChangeEvent` behavior: document insert/update
with a row, delete with a row ID, invalidation, subscription revocation, and
explained errors. The runtime may translate a different future server event
into the stable `table_invalidated` SDK event. The binding then refreshes an
authoritative snapshot using the runtime query operation.

`useTable` retains `{rows, total, totalPages, loading, error}` and current page
window behavior. `useInfiniteTable` retains `{rows, loadMore, hasMore, loading,
error}`. Invalidation/reconnect refreshes the current page or loaded window;
coalescing prevents overlapping refresh storms. Both hooks obtain their scoped
client from React context rather than a shared mutable transport.

Filter validation, `DocumentFilter` behavior, ordering, pagination, audit fields,
and flattened `TableRow` results remain unchanged. Preserve explained rejection
of query-only operators when they cannot support live subscriptions. Helpers
such as `compileFilterToExpr`, `flattenDocument`, and event reducers are not
exports of `index.v2.ts`; moving them does not justify expanding the public API.

## Files

Default location is `workspace` and default mode is `cloud`. Preserve explicit
scope and Solution options; defaults come from the bound client.

| Existing operation | Runtime responsibility and preserved result |
| --- | --- |
| `read(path, options?)` | Read text; return string. |
| `readBytes(path, options?)` | Read/decode binary data; return `Uint8Array`. |
| `write(path, text, options?)` | Write text; resolve without a result. |
| `writeBytes(path, bytes, options?)` | Normalize supported binary inputs and encode the server request. |
| `delete(path, options?)` | Delete; resolve without a result. |
| `list(directory?, options?)` | Return paths and normalized optional metadata. |
| `exists(path, options?)` | Return boolean. |
| `signedUrl(path, options?)` | Return URL, path, and expiry with current defaults. |
| `signedUrls(paths, options?)` | Return the corresponding signed results; preserve per-item denial behavior. |
| `upload(path, content, options?)` | Obtain signed PUT, upload, then complete the upload through the API. |
| `download(path, options?)` | Obtain signed GET and return the downloaded `Blob`. |

Signed storage requests must not inherit API authorization headers. Preserve
403 access errors, 404 not-found errors, 400/422 policy errors, and generic
storage/network errors. The binding keeps `FileAccessDeniedError`,
`FileNotFoundError`, and `FilePolicyError` class identity.

`useFiles` retains `{files, filesMetadata, loading, error, denied, empty,
refetch}`. Its runtime subscription emits stable change/reconnect/revocation
signals; the binding can re-list and update rendering state. Revocation ends
the subscription and exposes denial. A failed list does not keep stale arrays
as a successful result.

## Workflows

Move workflow start, inline completion, live status/log observation,
acknowledgement reconciliation, terminal result retrieval, and existing
disconnection recovery into a React-free runtime operation. It returns the
result promise and supplies normalized execution ID, status, and log callbacks.

Preserve the current hook contracts:

- `useWorkflow(ref)`: data, loading, error, `run`, logs, status, execution ID.
- `useWorkflowQuery(ref, params?)`: automatic run and explicit refresh.
- `useWorkflowMutation(ref)`: explicit mutate operation and the same observable
  execution state.

Each caller's promise must settle independently. An older overlapping run
cannot overwrite the latest run's rendered state. Preserve terminal status and
error semantics without exposing raw execution frames to hooks.

Transient or already-terminal start responses settle immediately. Local
Solution execution has no durable execution record in that case, so the runtime
must not subscribe or poll it. Ordinary server execution still obtains the
authoritative terminal result rather than assuming its notification includes
the result. Cancellation/disposal stops observations without silently
cancelling a server execution unless a public operation explicitly requests it.

## User, Organizations, and Branding

| Existing hook/component | Runtime and binding responsibilities |
| --- | --- |
| `useUser()` | Runtime reads/normalizes current user; binding preserves roles, display defaults, loading/error, and `hasRole`. |
| `RequireRole` | Remains a display-only React component; server authorization still controls operations. |
| `useOrganizations(options?)` | Runtime lists normalized organizations; binding preserves enabled/include-inactive behavior, loading/error, and refetch. |
| `useBranding(options?)` | Runtime reads normalized branding and resolves asset URLs; binding retains palette calculation and optional DOM theme application. |
| `BifrostProvider` | Owns App React context, theme, scoped client readiness/lifetime, and existing bootstrap props. |
| `BifrostHeader` | Remains self-contained App-owned React UI. |

Resource observations must be isolated by client identity and scope. A scope
change cannot render a prior scope's pending result. Keep in-flight request
deduplication scoped to a client; do not introduce a cross-App authorization
cache while extracting the runtime.

The public context's `authedFetch` remains a `fetch`-shaped raw operation with
ordinary `Response` semantics. It delegates authentication and App/org scope to
the runtime. App-authored paths and payload parsing remain the App's contract.
