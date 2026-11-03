import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
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
 * Live updates apply to whatever's been loaded, in `order_by` order (see
 * `applyEvent`): the loaded rows stay the server's ordered prefix, so the
 * next page's offset is the number of rows loaded.
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
  // memoizes its `where` literal. `rowsRef` mirrors the committed rows: their
  // count is the next page's offset, since live events keep the loaded rows
  // equal to the server's ordered prefix.
  const rowsRef = useRef<TableRow[]>([]);
  const hasMoreRef = useRef(true);
  const tableIdRef = useRef<string | null>(null);
  const cancelledRef = useRef(false);

  // Layout effects, so a `loadMore()` fired right after a live-event render
  // never reads the previous commit's rows.
  useLayoutEffect(() => {
    rowsRef.current = rows;
  }, [rows]);
  useLayoutEffect(() => {
    hasMoreRef.current = hasMore;
  }, [hasMore]);

  const loadMore = useCallback(async () => {
    if (cancelledRef.current) return;
    const offset = rowsRef.current.length;
    if (!hasMore && offset > 0) return;
    try {
      const snap = await tables.query(
        name,
        {
          where,
          limit: pageSize,
          offset,
          order_by,
          order_dir,
          // After the first page, skip the count query for speed. The
          // hasMore signal comes from the page-size check below.
          skip_count: offset > 0 ? true : undefined,
        },
        scope,
      );
      if (cancelledRef.current) return;
      tableIdRef.current = snap.table_id;
      const newRows = snap.documents.map(flattenDocument);
      setRows((prev) => {
        if (offset === 0) return newRows;
        // A live event can shift the server's ordering between pages; never
        // show a row twice.
        const loaded = new Set(prev.map((r) => r.id));
        return [...prev, ...newRows.filter((r) => !loaded.has(r.id))];
      });
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
    let effectCancelled = false;
    cancelledRef.current = false;
    rowsRef.current = [];
    tableIdRef.current = null;

    let unsubscribe: (() => void) | null = null;
    let refreshInFlight = false;
    let refreshDirty = false;

    async function loadInitialPage() {
      if (effectCancelled) return;
      try {
        const snap = await tables.query(
          name,
          {
            where,
            limit: pageSize,
            offset: 0,
            order_by,
            order_dir,
          },
          scope,
        );
        if (effectCancelled) return;
        tableIdRef.current = snap.table_id;
        const newRows = snap.documents.map(flattenDocument);
        setRows(newRows);
        setHasMore(newRows.length === pageSize);
        setLoading(false);
      } catch (e) {
        if (effectCancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setLoading(false);
      }
    }

    async function refreshLoadedSnapshot() {
      const targetCount = Math.max(rowsRef.current.length, pageSize);
      const refreshedRows: TableRow[] = [];
      let refreshOffset = 0;
      let total = 0;

      while (refreshOffset < targetCount) {
        const limit = Math.min(1000, targetCount - refreshOffset);
        const snap = await tables.query(
          name,
          {
            where,
            limit,
            offset: refreshOffset,
            order_by,
            order_dir,
            skip_count: refreshOffset > 0 ? true : undefined,
          },
          scope,
        );
        if (effectCancelled) return;
        if (refreshOffset === 0) total = snap.total;
        const pageRows = snap.documents.map(flattenDocument);
        refreshedRows.push(...pageRows);
        refreshOffset += pageRows.length;
        if (pageRows.length < limit) break;
      }

      if (effectCancelled) return;
      setRows(refreshedRows);
      setHasMore(refreshedRows.length < total);
      setError(null);
    }

    function refreshAuthoritativeSnapshot() {
      if (refreshInFlight) {
        refreshDirty = true;
        return;
      }

      refreshInFlight = true;
      void (async () => {
        try {
          do {
            refreshDirty = false;
            try {
              await refreshLoadedSnapshot();
            } catch (e) {
              if (!effectCancelled) {
                setError(e instanceof Error ? e : new Error(String(e)));
              }
            }
          } while (refreshDirty && !effectCancelled);
        } finally {
          refreshInFlight = false;
        }
      })();
    }

    async function init() {
      try {
        // Pre-compile the filter so we surface unsupported-operator errors
        // without the snapshot succeeding silently.
        const subscribeFilter: Expr | null = where
          ? compileFilterToExpr(where)
          : null;

        // Reset state from inside the async body so React doesn't see
        // synchronous setState-in-effect (the lint rule disallows that
        // pattern; cascading renders during the same commit).
        // `loadInitialPage()` then replaces `rows` with the first page.
        setHasMore(true);
        setError(null);
        setLoading(true);

        await loadInitialPage();
        if (effectCancelled) return;

        if (tableIdRef.current) {
          unsubscribe = tables.subscribe(
            tableIdRef.current,
            subscribeFilter,
            (evt) => {
              if (evt.type === "error") {
                if (!effectCancelled) setError(new Error(evt.message));
                return;
              }
              if (evt.type === "table_invalidated") {
                refreshAuthoritativeSnapshot();
                return;
              }
              applyEvent(evt, setRows, {
                order_by,
                order_dir,
                hasMore: () => hasMoreRef.current,
              });
            },
            () => {
              refreshAuthoritativeSnapshot();
            },
          );
        }
      } catch (e) {
        if (effectCancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setLoading(false);
      }
    }

    init();
    return () => {
      effectCancelled = true;
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
