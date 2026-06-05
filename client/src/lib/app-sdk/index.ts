/**
 * Public `bifrost` web SDK barrel — the surface a standalone_v2 Solution app
 * imports (`import { BifrostProvider, useTable } from "bifrost"`).
 *
 * v2 apps get the SDK as a real package (resolved from node_modules in
 * `npm run dev`, bundled at build). The provider supplies auth/session/org via
 * context; the existing data hooks remain the same public API. The v1 inline
 * path (globalThis proxy) does not import this barrel.
 */
export { BifrostProvider, useBifrostContext } from "./provider";
export type { BifrostContextValue, BifrostProviderProps } from "./provider";

export { BifrostHeader } from "@/components/solutions/BifrostHeader";
export type { BifrostHeaderProps } from "@/components/solutions/BifrostHeader";

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
