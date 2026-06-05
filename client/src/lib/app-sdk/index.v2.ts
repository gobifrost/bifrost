/**
 * The installable `bifrost` package surface — the entry the /api/sdk/download
 * endpoint bundles into the npm package a standalone_v2 app depends on.
 *
 * This is intentionally distinct from `index.ts` (the in-client barrel): the
 * in-client `BifrostHeader` imports shadcn `Button` + `cn` via `@/` aliases that
 * don't resolve outside the client project, so this entry re-exports a
 * SELF-CONTAINED header (./bifrost-header) instead. Everything here pulls only
 * peer deps the consuming app already has (react, react-dom, lucide-react) plus
 * type-only `@/lib/v1` imports that esbuild drops.
 */
export { BifrostProvider, useBifrostContext } from "./provider";
export type { BifrostContextValue, BifrostProviderProps } from "./provider";

export { BifrostHeader } from "./bifrost-header";
export type { BifrostHeaderProps } from "./bifrost-header";

export { useWorkflow } from "./use-workflow";
export type { UseWorkflowState } from "./use-workflow";

export { useTable } from "./use-table";
export type {
  DocumentFilter,
  FilterValue,
  TableRow,
  UseTableQuery,
} from "./use-table";

export { useInfiniteTable } from "./use-infinite-table";

export { tables, TableAccessDeniedError, TableNotFoundError } from "./tables";
export type { TableChangeEvent } from "./tables";
