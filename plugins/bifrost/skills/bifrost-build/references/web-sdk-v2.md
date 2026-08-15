# Web SDK v2

The instance-served `bifrost` package is the runtime SDK for `standalone_v2` Solution apps. Use `../generated/web-sdk-surface.md` for the exact export/type surface. Read `apps-v2.md` for project structure and `app-quality.md` for UI completion.

## Keeping an existing app current

The web SDK is vendored into each app. A platform deployment does not update an existing app's installed SDK automatically. When a reported behavior is fixed in the web SDK, or an app predates the SDK behavior it now relies on, refresh that app before rebuilding it:

```bash
bifrost solution sdk update [PATH] --app <app-slug>
```

Omit `--app` for a single-app Solution. This command downloads the SDK from the connected Bifrost instance and reinstalls the vendored package; do not hand-edit the generated SDK files. After updating, run the app's typecheck, tests, and production build, then redeploy it. API-only execution fixes and host-shell asset fixes do not require an app SDK update.

## Provider and context

Keep the scaffolded `BifrostProvider` wiring in `src/main.tsx`. The host supplies the API URL, viewer token, org scope, app ID, theme, logout handler, and mount basename.

`useBifrostContext()` exposes the scoped transport and host state, including `authedFetch`, logout, and theme controls. It does not expose a trustworthy client-side role authorization API; enforce access in workflows and platform policies.

`supportsTheme` declares that the entire app responds to host light/dark state. Read the theme contract in `app-quality.md` before retaining it.

## Header

`BifrostHeader` provides optional Bifrost chrome and account/theme controls:

```tsx
import { BifrostHeader } from "bifrost";

<BifrostHeader title="Operations" />
```

The platform does not insert it automatically. Compose it into the app's own layout and avoid adding a second competing top-level header.

## Workflow hooks

Prefer portable `path::function` locators such as `functions/orders.py::list_orders`.

| Need | API |
|---|---|
| Load/reload data on mount or parameter change | `useWorkflowQuery(ref, params)` |
| Run a submit/button action | `useWorkflowMutation(ref)` then `mutate(input)` |
| Fully imperative low-level control | `useWorkflow(ref)` then `run(input)` |

```tsx
import { useWorkflowMutation, useWorkflowQuery } from "bifrost";

const orders = useWorkflowQuery("functions/orders.py::list_orders", {
  status: "open",
});

const createOrder = useWorkflowMutation("functions/orders.py::create_order");
await createOrder.mutate({ customerId, lines });
```

Initial query data is nullable. Render loading and error states before accessing it. Keep query parameter objects stable so ordinary renders do not create accidental refetch loops. For mutations, expose progress, handle rejection, and prevent duplicate action where necessary.

Workflow hooks resolve within the current app/Solution context. A UUID is environment-specific; a bare name can be ambiguous and may proxy to a deployed copy during local work.

## Tables

Use imperative `tables` for one-shot CRUD and `useTable`/`useInfiniteTable` for live React views.

```tsx
import { useTable } from "bifrost";

const { rows, total, loading, error } = useTable("tickets", {
  where: { status: "open" },
  page: 1,
  pageSize: 25,
});
```

Hook rows are flattened; imperative query documents keep custom fields under `.data`. Web `tables.delete()` deletes rows, while Python `tables.delete()` deletes the table. Read `tables.md` before implementing table mutations or filters.

## Managed files

Use imperative `files` for reads/writes/uploads/downloads and `useFiles` for a live location/prefix listing.

```tsx
import { files, useFiles } from "bifrost";

const reports = useFiles("reports/", {
  location: "documents",
  includeMetadata: true,
});

await files.write("reports/status.txt", "ready", { location: "documents" });
```

Solution locations must be declared. Render denied separately from empty, and use signed upload/download behavior for large binary data. Read `files.md`.

## Errors

The SDK exports typed table/file errors including access denied, not found, and invalid-policy/location cases. Distinguish failures when they lead to different user actions; otherwise show one actionable message and preserve diagnostic detail for logs.

Do not display raw workflow stack traces, decrypted integration errors, access tokens, or secret config values.

## App identity and scope

The scaffolded provider carries `appId` so deployed workflow/table/file requests resolve within the correct install. Do not construct `X-Bifrost-App`, auth, or Solution query headers manually.

The host also supplies org scope. Explicit scope overrides are privileged behavior and should not be used for ordinary app navigation. `global_repo_access` affects server-side fallback; it does not change the web SDK API or bypass policies.

## Verification

- Confirm every imported symbol exists in the generated surface.
- Exercise hooks through the scaffolded provider and real local proxy.
- Test loading, empty, denied, missing, error, success, and reconnect behavior as relevant.
- Verify app/workflow portable refs locally and after deploy.
- Check table/file policies with a realistic viewer.
- Apply the full rendered acceptance checklist in `app-quality.md`.
