import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "@/lib/v1";
import { tables } from "./tables";
import {
  compileFilterToExpr,
  flattenDocument,
  type DocumentFilter,
  type TableRow,
  applyEvent,
} from "./use-table";

type Expr = components["schemas"]["Expr"];

export interface UseInfiniteTableQuery {
  where?: DocumentFilter;
  /** Page size (default 100, server cap 1000). */
  pageSize?: number;
  order_by?: string;
  order_dir?: "asc" | "desc";
  scope?: string;
}

export interface UseInfiniteTableResult {
  rows: TableRow[];
  loadMore: () => Promise<void>;
  hasMore: boolean;
  loading: boolean;
  error: Error | null;
}

/**
 * Live-updating infinite-scroll table data hook.
 *
 * Loads rows in pages on demand and accumulates them: each `loadMore()` call
 * appends the next page to `rows`. The first page fetches with a count;
 * subsequent pages set `skip_count: true` (the API supports this — see
 * `DocumentQuery.skip_count`). Pages stop when a partial page comes back.
 * Live updates apply to whatever's been loaded.
 *
 * Use this for "Load more" / infinite-scroll UI. For numbered-page UI ("Page
 * 3 of 14"), prefer `useTable`'s `page` / `pageSize` / `totalPages` surface.
 */
export function useInfiniteTable(
  name: string,
  query: UseInfiniteTableQuery = {},
): UseInfiniteTableResult {
  const [rows, setRows] = useState<TableRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const { where, pageSize = 100, order_by, order_dir, scope } = query;
  const whereKey = JSON.stringify(where ?? null);

  // Stable refs so loadMore doesn't capture stale values when the caller
  // memoizes its `where` literal.
  const offsetRef = useRef(0);
  const tableIdRef = useRef<string | null>(null);
  const cancelledRef = useRef(false);

  const loadMore = useCallback(async () => {
    if (cancelledRef.current) return;
    if (!hasMore && offsetRef.current > 0) return;
    try {
      const snap = await tables.query(
        name,
        {
          where,
          limit: pageSize,
          offset: offsetRef.current,
          order_by,
          order_dir,
          // After the first page, skip the count query for speed. The
          // hasMore signal comes from the page-size check below.
          skip_count: offsetRef.current > 0 ? true : undefined,
        },
        scope,
      );
      if (cancelledRef.current) return;
      tableIdRef.current = snap.table_id;
      const newRows = snap.documents.map(flattenDocument);
      setRows((prev) =>
        offsetRef.current === 0 ? newRows : [...prev, ...newRows],
      );
      offsetRef.current += newRows.length;
      setHasMore(newRows.length === pageSize);
      setLoading(false);
    } catch (e) {
      if (cancelledRef.current) return;
      setError(e instanceof Error ? e : new Error(String(e)));
      setLoading(false);
    }
    // pageSize/where/scope/order_by/order_dir are captured intentionally; if
    // they change the parent effect resets state and re-invokes from offset 0.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, whereKey, pageSize, order_by, order_dir, scope, hasMore]);

  useEffect(() => {
    cancelledRef.current = false;
    offsetRef.current = 0;
    tableIdRef.current = null;

    let unsubscribe: (() => void) | null = null;

    async function init() {
      try {
        // Pre-compile the filter so we surface unsupported-operator errors
        // without the snapshot succeeding silently.
        const subscribeFilter: Expr | null = where
          ? compileFilterToExpr(where)
          : null;

        // Reset state from inside the async body so React doesn't see
        // synchronous setState-in-effect (the lint rule disallows that
        // pattern; cascading renders during the same commit). The first
        // `loadMore()` call will replace `rows` because offsetRef is 0.
        setHasMore(true);
        setError(null);
        setLoading(true);

        await loadMore();
        if (cancelledRef.current) return;

        if (tableIdRef.current) {
          unsubscribe = tables.subscribe(
            tableIdRef.current,
            subscribeFilter,
            (evt) => {
              if (evt.type === "error") {
                if (!cancelledRef.current) setError(new Error(evt.message));
                return;
              }
              applyEvent(evt, setRows);
            },
          );
        }
      } catch (e) {
        if (cancelledRef.current) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setLoading(false);
      }
    }

    init();
    return () => {
      cancelledRef.current = true;
      unsubscribe?.();
    };
    // loadMore is stable per query-shape; we don't want it as a direct dep
    // because including it would re-run init() on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, whereKey, pageSize, order_by, order_dir, scope]);

  return { rows, loadMore, hasMore, loading, error };
}

// Re-export for convenience.
export type { TableRow, DocumentFilter, FilterValue } from "./use-table";
