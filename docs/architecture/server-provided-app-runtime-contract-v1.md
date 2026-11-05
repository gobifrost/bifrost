# App SDK Preservation Inventory

Status: Verification inventory for [Server-Hosted App SDK](server-provided-app-runtime.md).
Source: exports reachable from `client/src/lib/app-sdk/index.v2.ts`.

Serve the existing SDK implementation, including its hooks and protocol handling.
This inventory is not a new runtime interface or a set of translation adapters.
It records public behavior that changing SDK delivery must preserve.

## Exports and React

Forward all existing value exports and ship self-contained public type
declarations. Preserve `BifrostProvider`, `useBifrostContext`, `BifrostHeader`,
`RequireRole`, workflow hooks, table hooks, file hooks, and resource hooks. The
App's React and JSX runtime must power both the SDK and the App's renderer.

Error constructors exported by the binding must be the constructors the SDK
actually throws. An `instanceof` check must work. Separate factory instances must
not share React contexts or mutable default transports/scopes.

## Tables

Preserve the existing `tables` methods:

`get`, `insert`, `upsert`, `update`, `delete`, `query`, `count`, `subscribe`.

Keep single/batch return shapes, missing-row behavior, table-level errors,
filter validation, ordering, pagination, audit fields, and flattened rows.
Preserve `TableAccessDeniedError` and `TableNotFoundError` identity.

Existing SDK code continues to handle document changes, invalidation, revocation,
errors, and acknowledged reconnect/resubscription. `useTable` keeps its current
page window; `useInfiniteTable` keeps its loaded window. Preserve refresh
coalescing. Private filter/flatten/event helpers do not become public exports
merely because the SDK moves to a hosted artifact.

## Files

Preserve the existing `files` methods:

`read`, `readBytes`, `write`, `writeBytes`, `delete`, `list`, `exists`,
`signedUrl`, `signedUrls`, `upload`, `download`.

Keep workspace/cloud defaults, explicit scope and Solution options, binary
conversion, signed URL results, upload completion, and downloaded Blob behavior.
Signed storage requests must not receive API authorization headers. Preserve
`FileAccessDeniedError`, `FileNotFoundError`, and `FilePolicyError` identity.

`useFiles` retains files, metadata, loading/error, denied/empty, and refetch
behavior, including live changes and subscription revocation.

## Workflows

Preserve `useWorkflow`, `useWorkflowQuery`, and `useWorkflowMutation` signatures,
results, logs, execution IDs, loading/error, query refresh, and explicit mutation.
Each overlapping call's promise settles independently; older runs cannot replace
the latest rendered state.

Keep the existing request, streaming, acknowledgement, terminal-result, and
disconnection logic together in the hosted SDK. Local transient results settle
immediately without observing a nonexistent durable execution record.

## Resources, Provider, and Raw Requests

Preserve `useUser`, role display helpers, `useOrganizations`, `useBranding`, and
header/theme behavior. Preserve all existing provider and mount bootstrap props.
Do not introduce cross-App resource caches or mutable host-global scope.

`useBifrostContext().authedFetch` remains fetch-shaped and preserves Response
semantics, live credentials, scope headers, and bounded challenge-only replay.
App-authored endpoint paths and response parsing still belong to App code;
hosting the SDK cannot rewrite those arbitrary calls.
