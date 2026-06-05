/**
 * The installable `bifrost` package surface — the entry the /api/sdk/download
 * endpoint bundles into the npm package a standalone_v2 app depends on.
 *
 * This is intentionally distinct from `index.ts` (the in-client barrel): it
 * EXCLUDES `BifrostHeader`, the one export that imports a client-only component
 * (`@/components/ui/button`) and so can't be bundled standalone without dragging
 * in shadcn. BifrostHeader is a later addition (an optional chrome component);
 * the core auth + tables + workflows surface ships first. Everything here pulls
 * only the four peer deps (react, react-dom, react-router-dom,
 * @tanstack/react-query) plus type-only `@/lib/v1` imports that esbuild drops.
 */
export { BifrostProvider, useBifrostContext } from "./provider";
export type { BifrostContextValue, BifrostProviderProps } from "./provider";

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
